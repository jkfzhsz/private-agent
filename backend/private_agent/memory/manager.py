"""蓝图 §4.2/§4.4/§4.5 MemoryManager - 记忆提取/淘汰/注入。

核心职责:
- maybe_extract: 每 EXTRACT_INTERVAL_TURNS 轮自动触发提取(§4.2)
- on_session_end: 会话结束触发提取(§4.2)
- manual_extract: UI 手动触发提取(§4.2)
- evict_memories: 提取后淘汰(§4.4)
- load_user_memories: 会话启动时注入(§4.5)

0.5.0 M1(2026-08-08): 场景独立记忆。
- 提取按会话场景打标(scope = locked_skill_name / global);
- 注入策略(用户确认): 全局只注入"身份+核心偏好"画像(默认 2 条),
  其余配额全给场景记忆(默认 8 条), 比例存 memory.inject_ratio 可配;
  全局其他内容按需检索(memory_search / search_knowledge)。
0.5.0 M3: 驱逐前巩固归档(摘要入 user_memories_archive 再 deactivate);
注入排序(importance × 时间衰减) + 内容去重。
"""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from private_agent.memory.memories_repo import (
    MEMORY_TYPES,
    SCENE_KEYS,
    TYPE_IMPORTANCE_MAP,
    MemoriesRepo,
    Memory,
)

__all__ = ["MemoryManager"]

# 蓝图 §4.2 提取 prompt 模板
# 0.5.0 M1: 增加场景归属判定(由模型判断每条记忆属于哪个场景,
# 避免全部落 global)。格式 [type@scope] content; 未标 scope 时按会话场景兜底。
EXTRACT_PROMPT_TEMPLATE = """请从以下对话中提取用户的关键信息,分类为:
1. preference: 用户偏好(语言、风格、工作习惯等)
2. fact: 事实信息(用户身份、项目背景、技术栈等)
3. todo: 待办事项(用户提到需要完成的任务)
4. decision: 已做出的决策(用户明确选择的方向)

每条记忆还需判定场景归属(scope):
- office = 工作与学习(子瞻): 文档处理、数据分析、网页研究、学习辅导
- data_analysis = 投资与理财(白圭): 行情、基金、宏观、财务分析
- frontend_design = 生活健康与美学设计(清和): 健康管理、美学设计、前端设计
- global = 全局通用(用户身份、跨场景偏好、协作规则、项目概况等所有场景都相关的信息)

仅提取明确出现的信息,不要推测。每条记忆格式:
[type@scope] content

对话历史:
{session_messages}

输出:每行一条,空行分隔不同类型。"""


class MemoryManager:
    """蓝图 §4.2-§4.5 用户记忆管理核心类。

    Args:
        memories_repo: MemoriesRepo 实例。
        compress_adapter: 压缩模型适配器(复用 §3.11 compress_model)。
        react_events_insert: react_events 插入回调(可选,默认 None)。
        extract_interval_turns: 自动提取间隔轮次(默认 8)。
        inject_limit: 注入上限(默认 10)。
        inject_global_n: 全局常驻画像条数(默认 2, memory.inject_ratio.global)。
        eviction_max_active: 活跃记忆上限(默认 200)。
        eviction_min_importance: 淘汰重要性阈值(默认 0.3)。
        eviction_expire_days: 淘汰超期天数(默认 30)。
        archive_before_evict: 驱逐前巩固归档开关(默认 False, M3 打开)。
    """

    def __init__(
        self,
        memories_repo: MemoriesRepo,
        compress_adapter: Any | None = None,
        react_events_insert: Callable | None = None,
        extract_interval_turns: int = 8,
        inject_limit: int = 10,
        inject_global_n: int = 2,
        eviction_max_active: int = 200,
        eviction_min_importance: float = 0.3,
        eviction_expire_days: int = 30,
        archive_before_evict: bool = False,
    ) -> None:
        self._repo = memories_repo
        self._compress_adapter = compress_adapter
        self._react_events_insert = react_events_insert
        self.extract_interval_turns = extract_interval_turns
        self.inject_limit = inject_limit
        self.inject_global_n = inject_global_n
        self.eviction_max_active = eviction_max_active
        self.eviction_min_importance = eviction_min_importance
        self.eviction_expire_days = eviction_expire_days
        self.archive_before_evict = archive_before_evict

    # ── 提取 ──────────────────────────────────────────────────────────────

    async def maybe_extract(
        self,
        session_id: int,
        current_turn: int,
        scope: str | None = None,
    ) -> list[Memory] | None:
        """每 extract_interval_turns 轮触发提取(蓝图 §4.2 条件 1)。

        Args:
            session_id: 会话 ID。
            current_turn: 当前轮次(≥1)。
            scope: 0.5.0 M1 会话场景(locked_skill_name; None=global)。

        Returns:
            提取到的记忆列表(未触发时返回 None)。
        """
        if (
            current_turn > 0
            and current_turn % self.extract_interval_turns == 0
        ):
            return await self._extract_and_evict(session_id, current_turn, scope)
        return None

    async def on_session_end(
        self,
        session_id: int,
        current_turn: int,
        scope: str | None = None,
    ) -> list[Memory]:
        """会话结束触发提取(蓝图 §4.2 条件 2)。

        Args:
            session_id: 会话 ID。
            current_turn: 最后轮次。
            scope: 0.5.0 M1 会话场景(None=global)。

        Returns:
            提取到的记忆列表。
        """
        return await self._extract_and_evict(session_id, current_turn, scope)

    async def manual_extract(
        self,
        session_id: int,
        current_turn: int,
        scope: str | None = None,
    ) -> list[Memory]:
        """UI 手动触发提取(蓝图 §4.2 缺口补充)。

        Args:
            session_id: 会话 ID。
            current_turn: 当前轮次。
            scope: 0.5.0 M1 会话场景(None=global)。

        Returns:
            提取到的记忆列表。
        """
        return await self._extract_and_evict(session_id, current_turn, scope)

    # ── 注入 ──────────────────────────────────────────────────────────────

    async def load_user_memories(
        self,
        user_id: int = 1,
        limit: int | None = None,
        scope: str | None = None,
    ) -> list[Memory]:
        """会话启动时调用,返回注入记忆(蓝图 §4.5)。

        0.5.0 M1 注入策略(用户 2026-08-08 确认):
        - 场景会话(scope ∈ SCENE_KEYS): 全局常驻"身份+核心偏好"画像
          (self.inject_global_n 条, 默认 2) + 场景记忆占余量
          (limit - inject_global_n, 默认 8);
        - 全局会话(scope=None/'global'): 全部注入 global 记忆(仍按
          inject_global_n 截断, 其余按需检索)。
        - 注入排序(0.5.0 M3 B4): importance × 时间衰减因子
          (1/(1+ln(1+days_since_access))) + 内容 hash 去重。

        Args:
            user_id: 用户 ID(单人场景固定为 1)。
            limit: 返回条数(默认 self.inject_limit)。
            scope: 会话场景技术标识(None/'global'=全局会话;
                   office/data_analysis/frontend_design=场景会话)。

        Returns:
            注入记忆列表(含 access 更新)。
        """
        limit = limit if limit is not None else self.inject_limit
        global_n = min(self.inject_global_n, max(1, limit // 2))
        scene_n = max(0, limit - global_n)

        if scope in SCENE_KEYS:
            # 场景会话: 全局画像子集 + 场景记忆(配额 2:8)
            global_mems = await self._repo.get_global_core(
                user_id, limit=global_n
            )
            scene_mems = await self._repo.get_top_active(
                user_id,
                order_by="importance DESC, last_accessed_at DESC",
                limit=scene_n,
                scope=scope,
            )
            memories = global_mems + scene_mems
        else:
            # 全局会话: 全局记忆(identity/偏好/项目概况)
            memories = await self._repo.get_top_active(
                user_id,
                order_by="importance DESC, last_accessed_at DESC",
                limit=limit,
                scope="global",
            )
        # 0.5.0 M3 B4: 注入排序(importance × 时间衰减) + 内容去重
        memories = self._rank_memories(memories)
        if memories:
            await self._repo.batch_update_access(memories)
        return memories

    @staticmethod
    def _rank_memories(
        memories: list[Memory], now: datetime | None = None
    ) -> list[Memory]:
        """0.5.0 M3 B4: 注入排序 = importance × 时间衰减 + 内容 hash 去重。

        衰减因子: 1 / (1 + ln(1 + days_since_last_access)), 3 天内访问
        衰减 <10%, 30 天 ~35%, 180 天 ~55%。同内容(归一化 hash)只留一条
        (importance 高的优先)。

        Args:
            memories: 待排序记忆列表。
            now: 当前时间(测试可注入)。

        Returns:
            排序去重后的记忆列表。
        """
        if not memories:
            return []
        now = now or datetime.now(timezone.utc)
        best: dict[str, tuple[float, Memory]] = {}
        for m in memories:
            # 内容归一化(去空白/小写)后 hash 去重: 同内容只保留 importance×衰减 最高者
            key = hashlib.md5(
                (" ".join((m.content or "").lower().split()))
                .encode("utf-8")
            ).hexdigest()
            days = 0.0
            if m.last_accessed_at is not None:
                try:
                    days = max(0.0, (now - m.last_accessed_at).total_seconds() / 86400.0)
                except TypeError:  # 时区不一致等防御
                    days = 0.0
            decay = 1.0 / (1.0 + math.log1p(days))
            score = float(m.importance) * decay
            if key not in best or score > best[key][0]:
                best[key] = (score, m)
        ranked = sorted(best.values(), key=lambda t: t[0], reverse=True)
        return [m for _, m in ranked]

    @staticmethod
    def format_memories_for_stable(
        memories: list[Memory], max_item_chars: int | None = None
    ) -> str:
        """格式化记忆为 Stable Zone 文本(蓝图 §4.5)。

        Args:
            memories: 记忆列表。
            max_item_chars: 方向三: 单条记忆最大字符数(超出截断, None 不截)。

        Returns:
            格式化文本。
        """
        lines = ["[User Memories]"]
        for m in memories:
            content = m.content
            if max_item_chars and len(content) > max_item_chars:
                content = content[:max_item_chars] + "…"
            # 0.5.0 M1: 场景记忆标注 scope(显示层子瞻/白圭/清和, 全局不标)
            tag = ""
            if m.scope in SCENE_KEYS:
                tag = f"@{m.scope}"
            lines.append(f"[{m.type}{tag}] {content}")
        return "\n".join(lines)

    # ── 淘汰 ──────────────────────────────────────────────────────────────

    async def evict_memories(self, user_id: int = 1) -> int:
        """执行记忆淘汰(蓝图 §4.4)。

        条件 1: 超过上限,按 importance 升序淘汰最低的。
        条件 2: 低重要性 + 长期未访问,标记 inactive。

        0.5.0 M3 B3: archive_before_evict=True 时, 驱逐前先巩固归档
        (原内容 1 行摘要入 user_memories_archive, 再 deactivate) ——
        缓解"提取即压缩"不可逆损失; 归档不参与注入, 可 search 召回。

        Args:
            user_id: 用户 ID。

        Returns:
            本次淘汰的记忆数。
        """
        total = 0
        active = await self._repo.count_active(user_id)
        evicted_mems: list[Memory] = []
        if active > self.eviction_max_active:
            excess = active - self.eviction_max_active
            evicted_mems += await self._repo.deactivate_lowest(user_id, excess)

        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self.eviction_expire_days
        )
        evicted_mems += await self._repo.deactivate_expired(
            user_id,
            min_importance=self.eviction_min_importance,
            cutoff=cutoff,
        )

        if evicted_mems and self.archive_before_evict:
            try:
                # 无压缩模型时降级截断摘要(_default_summary), 不静默失败
                await self._repo.archive_memories(evicted_mems)
            except Exception:  # noqa: BLE001 - 归档失败不影响淘汰主流程
                pass
        total += len(evicted_mems)
        return total

    # ── 0.5.0 M3 B1: 画像聚合 ───────────────────────────────────────────

    async def aggregate_profile(
        self,
        user_id: int = 1,
        refresh_interval_hours: float = 24.0,
    ) -> dict | None:
        """0.5.0 M3 B1: 全局偏好记忆 → 用户画像聚合。

        将 global scope 的 preference/correction 记忆按内容聚类, 生成/更新
        user_profile 表(单一事实源)。画像字段:
        - collaboration_prefs: 协作偏好(高频关键词聚合摘要)
        - common_tools: 常用工具/技术栈
        - communication_style: 沟通风格(结论先行/简洁/中文等)

        聚合算法(轻量, 无 LLM 依赖): 取 global 高 importance 偏好记忆
        拼接摘要, 按关键词分桶(协作/工具/风格), 超长截断。correction 记忆
        高价值优先纳入。

        Args:
            user_id: 用户 ID。
            refresh_interval_hours: 刷新间隔(距上次更新 < 该值则跳过, 默认 24h)。

        Returns:
            更新后的画像 dict(无可聚合记忆时返回 None)。
        """
        profile = await self._repo.get_profile(user_id)
        if profile and profile.get("updated_at"):
            try:
                last = profile["updated_at"]
                age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600.0
                if age_h < refresh_interval_hours:
                    return profile  # 未到刷新周期, 复用现有画像
            except TypeError:
                pass
        # 聚合素材: global preference(高价值优先) + correction
        mems = await self._repo.get_top_active(
            user_id,
            order_by="importance DESC, last_accessed_at DESC",
            limit=50,
            scope="global",
        )
        prefs = [m for m in mems if m.type in ("preference", "correction")]
        if not prefs:
            return None
        # 关键词分桶(计数制: 每桶统计命中关键词数, 取最高分桶, 避免
        # "习惯用 python"同时命中 collab"习惯"与 tools"用"的歧义)
        collab_kw = ("协作", "偏好", "习惯", "喜欢", "希望", "不要", "避免", "注释", "命名", "优先")
        tool_kw = ("python", "pandas", "excel", "vscode", "git", "linux", "node",
                   "postgres", "docker", "react", "工具", "mcp")
        style_kw = ("简洁", "结论先行", "详细", "中文", "英文", "结构化", "口语", "严谨")
        collab, tools, style = [], [], []
        for m in prefs:
            c = (m.content or "").strip()
            if not c:
                continue
            hit = c[:60] + ("…" if len(c) > 60 else "")
            scores = {
                "collab": sum(1 for k in collab_kw if k in c),
                "tools": sum(1 for k in tool_kw if k in c),
                "style": sum(1 for k in style_kw if k in c),
            }
            # 平局时按 style > tools > collab 优先级(风格/工具描述更具体)
            best = max(scores, key=lambda k: (scores[k], {"style": 2, "tools": 1, "collab": 0}[k]))
            if scores[best] == 0:
                best = "collab"  # 未命中任何桶的归协作偏好
            (collab if best == "collab" else tools if best == "tools" else style).append(hit)
        def _join(items: list[str], cap: int = 400) -> str:
            return "；".join(dict.fromkeys(items))[:cap]

        new_profile = {
            "name": profile.get("name") if profile else None,
            "collaboration_prefs": _join(collab),
            "common_tools": _join(tools),
            "communication_style": _join(style),
            "ongoing_projects": (
                profile.get("ongoing_projects") if profile else []
            ),
        }
        await self._repo.upsert_profile(new_profile, user_id)
        return new_profile

    # ── 0.5.0 M3 B4: 注入画像常驻 ───────────────────────────────────────

    @staticmethod
    def format_profile_for_stable(profile: dict | None) -> str | None:
        """0.5.0 M3 B1: 画像格式化为 Stable Zone 文本(常驻头部)。

        Returns:
            画像文本(无可展示字段时返回 None)。
        """
        if not profile:
            return None
        parts = []
        if profile.get("collaboration_prefs"):
            parts.append(f"协作偏好: {profile['collaboration_prefs']}")
        if profile.get("common_tools"):
            parts.append(f"常用工具: {profile['common_tools']}")
        if profile.get("communication_style"):
            parts.append(f"沟通风格: {profile['communication_style']}")
        if not parts:
            return None
        return "[User Profile]\n" + "\n".join(parts)

    # ── 内部 ──────────────────────────────────────────────────────────────

    async def _extract_and_evict(
        self, session_id: int, current_turn: int, scope: str | None = None
    ) -> list[Memory]:
        """提取记忆 + 淘汰 + 记录事件(蓝图 §4.2/§4.4)。

        Args:
            session_id: 会话 ID。
            current_turn: 当前轮次。
            scope: 0.5.0 M1 会话场景(locked_skill_name; None=global)。
        """
        memories = await self._extract_memories(session_id, current_turn, scope)
        evicted = await self.evict_memories()
        if self._react_events_insert:
            # 2026-08-19(修复): 不传 conn —— react_events_insert 由外部注入
            # 已绑定连接的 callable(main.py 用 partial(insert_react_event, conn)),
            # 调用签名 (session_id, turn, event_type, payload)。此前 main.py
            # 误传裸模块函数 insert_react_event(需 conn 首参) → TypeError
            # "missing 1 required positional argument: 'conn'" → turn_end 后
            # 记忆提取阶段崩溃, 误报 user_message_failed(任务已完成)。
            await self._react_events_insert(
                session_id=session_id,
                turn=current_turn,
                event_type="memory_extracted",
                payload={
                    "count": len(memories),
                    "types": [m.type for m in memories],
                    "scopes": [m.scope for m in memories],
                    "evicted": evicted,
                },
            )
            # §4.4 [MVP]: 淘汰事件单独记录(评估回放需要区分提取/淘汰)
            if evicted > 0:
                await self._react_events_insert(
                    session_id=session_id,
                    turn=current_turn,
                    event_type="memory_evicted",
                    payload={"count": evicted},
                )
        return memories

    async def _extract_memories(
        self,
        session_id: int,
        current_turn: int,
        scope: str | None = None,
    ) -> list[Memory]:
        """LLM 摘要提取记忆(蓝图 §4.2)。

        0.5.0 M1: 提取结果按会话场景打标 —— 模型输出的 [type@scope] 优先,
        未标注的按会话场景兜底(会话锁定场景 → 该场景; 否则 global)。

        Args:
            session_id: 会话 ID。
            current_turn: 当前轮次。
            scope: 会话场景(locked_skill_name; None=global)。

        Returns:
            提取并入库的记忆列表。
        """
        if not self._compress_adapter:
            return []
        # 构建提取 prompt(简化: 无实际消息历史时使用占位)
        prompt = EXTRACT_PROMPT_TEMPLATE.format(
            session_messages=f"[session_id={session_id}, turn={current_turn}]"
        )
        result = await self._compress_adapter.chat(
            messages=[{"role": "user", "content": prompt}], tools=[]
        )
        parsed = self._parse_extracted(result.content, session_id, scope)
        if parsed:
            await self._repo.batch_insert(parsed)
        return parsed

    async def maybe_extract_from_correction(
        self,
        original: str,
        corrected: str,
        user_id: int = 1,
        scope: str | None = None,
    ) -> list[Memory]:
        """阶段三批次3(T3.4, 调研 round2 §4.4.1): 用户纠正沉淀。

        用户对生成结果明确纠正/编辑后重发时触发:
        1. 有 compress_adapter → LLM 定向提取纠正要点(压缩模型通道);
        2. 提取内容封装为 correction 类型记忆(importance 0.9 高价值);
        3. 无 adapter → 降级启发式(差异文本摘要), 不静默失败。

        0.5.0 M1: correction 记忆继承会话场景(scope, None=global)。

        Returns:
            沉淀的记忆列表(未触发/失败 → 空列表)。
        """
        if not original or not corrected or original == corrected:
            return []
        memory: Memory
        if self._compress_adapter:
            try:
                prompt = (
                    "用户对 Agent 的回答进行了纠正。请提取一条简明经验"
                    "（≤80 字，面向未来同类任务），说明用户偏好或正确做法。\n"
                    f"【用户原始表达/Agent 回答】{original[:800]}\n"
                    f"【用户纠正后内容】{corrected[:800]}\n"
                    "输出格式: 仅一行经验文本，不要任何前缀。"
                )
                result = await self._compress_adapter.chat(
                    messages=[{"role": "user", "content": prompt}], tools=[]
                )
                content = (result.content or "").strip()
                if not content:
                    return []
            except Exception:  # noqa: BLE001 - LLM 提取失败降级启发式
                content = ""
        else:
            content = ""
        if not content:
            # 启发式降级: 差异文本首 120 字
            content = f"用户纠正: {corrected[:120]}（原: {original[:80]}）"
        memory = Memory(
            type="correction",
            content=content,
            importance=TYPE_IMPORTANCE_MAP.get("correction", 0.9),
            scope=scope if scope in SCENE_KEYS else "global",
        )
        inserted = await self._repo.insert(memory)
        memory.id = inserted
        if self._react_events_insert is not None:
            try:
                # 2026-08-19(修复): 去掉 conn 位置参数 —— react_events_insert
                # 由外部注入 partial(insert_react_event, conn), 与上方
                # _extract_and_evict 调用签名统一 (session_id, turn, ...)。
                await self._react_events_insert(
                    session_id=0,
                    turn=0,
                    event_type="memory_extracted",
                    payload={
                        "source": "correction",
                        "memory_id": inserted,
                        "type": "correction",
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        return [memory]

    @staticmethod
    def _parse_extracted(
        text: str,
        source_session_id: int,
        scope: str | None = None,
    ) -> list[Memory]:
        """解析 LLM 输出的 [type@scope] content 格式(蓝图 §4.2 解析规则)。

        0.5.0 M1: 支持 [type@scope] 显式归属; 未标注 scope 时按会话场景
        兜底(scope 参数); 仍缺失则 global。

        Args:
            text: LLM 输出文本。
            source_session_id: 来源会话 ID。
            scope: 会话场景(兜底用, None=global)。

        Returns:
            Memory 列表。
        """
        fallback_scope = scope if scope in SCENE_KEYS else "global"
        memories: list[Memory] = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("[") and "]" in line:
                close = line.index("]")
                type_str = line[1:close].strip().lower()
                content = line[close + 1 :].strip()
                # [type@scope] 双段解析
                mem_scope = fallback_scope
                if "@" in type_str:
                    t, _, s = type_str.partition("@")
                    type_str = t.strip()
                    s = s.strip()
                    if s in SCENE_KEYS or s == "global":
                        mem_scope = s
                if type_str in MEMORY_TYPES:
                    importance = TYPE_IMPORTANCE_MAP.get(type_str, 0.5)
                    memories.append(
                        Memory(
                            type=type_str,
                            content=content,
                            importance=importance,
                            source_session_id=source_session_id,
                            scope=mem_scope,
                        )
                    )
                # 未匹配的行丢弃(蓝图 §4.2: type 不在枚举内的丢弃并日志告警)
        return memories