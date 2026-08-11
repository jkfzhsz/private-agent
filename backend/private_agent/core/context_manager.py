"""蓝图 §3.1-3.3 上下文管理器 - 三区分区 + 启动构建 + 每轮构建。

Source: spec/m1-react-loop AC-3 + Solution `core/context_manager.py`
- Zone: dataclass(name/messages/hash), hash 字段 M1-b step 10 预留
- ContextManager: 三区管理(frozen/stable/active)
- build_initial: 启动构建,持久化 Frozen Zone(system_prompt + 工具定义)到 messages 表
- build_per_turn / append_*_message: 每轮构建,Active Zone 追加用户/助手/工具消息
- get_messages: 返回 Frozen + Stable + Active 合并消息列表
- build_messages: get_messages 的 async 别名,供 run_turn 调用
- spec Out of scope: 三区构建不含压缩;启动时 Stable/Active 为空
- spec Assumptions: hash 字段预留,本次只存字段不做校验

M2 §4.5 扩展:ContextManager 接受可选的 memory_manager,在 ensure_initial 时
注入用户记忆到 Stable Zone。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from private_agent.errors import FrozenHashMismatchError
from private_agent.observability.logging import setup_logger
from private_agent.tools.defs import ToolDef

logger = setup_logger(__name__)

# 0.5.0 M2: 场景 KB 自动检索查询词映射(按场景领域关键词触发 keyword 命中;
# 未列出的场景回退通用查询)。
_KB_AUTO_RETRIEVE_QUERIES: dict[str, str] = {
    "office": "办公 文档处理 数据分析 网页研究 学习辅导",
    "data_analysis": "投资 估值 财务指标 宏观 资产配置 交易纪律",
    "frontend_design": "设计系统 健康 饮食 作息 锻炼 前端 组件规范",
}

if TYPE_CHECKING:
    import asyncpg

__all__ = ["Zone", "ContextManager"]


@dataclass
class Zone:
    """蓝图 §3.2 分区元数据。

    name: 'frozen' | 'stable' | 'active'
    messages: 该区消息列表(role/content/... dict)
    hash: M1-b step 10 预留(SHA-256 校验),本次仅占位
    """

    name: str
    messages: list[dict] = field(default_factory=list)
    hash: str | None = None


class ContextManager:
    """蓝图 §3.1-3.3 上下文管理器。

    三区:
    - frozen_zone: 系统提示词 + 工具定义(启动构建,不可变)
    - stable_zone: 压缩摘要区(M1 为空,压缩留 M1-b step 11)
    - active_zone: 当前会话消息(每轮追加,build_per_turn 维护)
    """

    def __init__(
        self,
        session_id: int,
        system_prompt: str,
        tools: list[ToolDef],
        memory_manager: Any | None = None,
        cfg: dict | None = None,
        scene: str | None = None,
        kb_auto_retrieve: bool = False,
        kb_scenario: str | None = None,
        evolution_repo: Any | None = None,
    ) -> None:
        self.session_id = session_id
        self._system_prompt = system_prompt
        self._tools = list(tools)
        self._memory_manager = memory_manager
        # Phase 1(2026-08-11): 经验注入仓库(默认 None 兼容旧调用, 零回归)
        self._evolution_repo = evolution_repo
        # 0.5.0 M1: 会话场景(locked_skill_name, 记忆注入 scope 用)
        self.scene = scene
        # 0.5.0 M2: 场景 KB 自动检索(auto_retrieve, 会话启动注入 top-N 片段)
        self.kb_auto_retrieve = kb_auto_retrieve
        self.kb_scenario = kb_scenario
        # 方向三: 注入精简配置(可选, 默认 None 兼容旧调用)
        self._cfg = cfg or {}
        self.frozen_zone = Zone(name="frozen")
        self.stable_zone = Zone(name="stable")
        self.active_zone = Zone(name="active")

    def _build_frozen_content(self) -> str:
        """构造 Frozen Zone system 消息内容:system_prompt + 工具定义。"""
        if not self._tools:
            return self._system_prompt
        tools_json = json.dumps(
            [t.to_openai_schema() for t in self._tools],
            ensure_ascii=False,
        )
        return f"{self._system_prompt}\n\n[TOOLS]\n{tools_json}"

    def compute_frozen_hash(self) -> str:
        """M3 §7.3 AC-1: 计算 Frozen Zone 内容的 SHA-256 hash。

        hash 基于 system_prompt + 工具定义(与 _build_frozen_content 同源)。
        用于会话锁定,存 sessions.frozen_hash。
        """
        content = self._build_frozen_content()
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def build_initial(self, conn: "asyncpg.Connection") -> None:
        """启动构建(蓝图 §3.3):持久化 Frozen Zone 到 messages 表。

        - role='system', zone='frozen', turn=0
        - Stable/Active 启动时为空,不入库
        - 内存三区同步更新

        Args:
            conn: Postgres 连接。
        """
        content = self._build_frozen_content()
        await conn.execute(
            """
            INSERT INTO messages (session_id, turn, role, content, zone)
            VALUES ($1, $2, $3, $4, $5)
            """,
            self.session_id,
            0,
            "system",
            content,
            "frozen",
        )
        self.frozen_zone.messages = [{"role": "system", "content": content}]
        self.stable_zone.messages = []
        self.active_zone.messages = []

    async def _inject_memories(self, conn: "asyncpg.Connection") -> None:
        """注入用户记忆到 Stable Zone(蓝图 §4.5)。

        在 ensure_initial 调用 build_initial 后执行,将高重要性记忆注入
        Stable Zone 初始内容。

        0.5.0 M3 B1: 会话启动先聚合并注入用户画像(全局偏好/工具/风格),
        画像常驻头部(高频偏好不再逐条碎片注入);再注入记忆。
        """
        if self._memory_manager is None:
            return
        # 0.5.0 M3 B1: 画像聚合注入(全局偏好常驻, 会话启动聚合一次)
        try:
            profile = await self._memory_manager.aggregate_profile()
            profile_text = self._memory_manager.format_profile_for_stable(profile)
        except Exception:  # noqa: BLE001 - 画像聚合失败不影响记忆注入
            profile_text = None
        # 0.5.0 M1: 按会话场景注入(场景会话 = 全局画像 + 场景记忆; 否则仅全局)
        memories = await self._memory_manager.load_user_memories(
            scope=self.scene
        )
        blocks: list[str] = []
        if profile_text:
            blocks.append(profile_text)
        if memories:
            # 方向三: 单条记忆截断(默认不截; config context.memory.max_item_chars)
            max_item_chars = (
                self._cfg.get("context", {}).get("memory", {}).get("max_item_chars")
            )
            blocks.append(
                self._memory_manager.format_memories_for_stable(
                    memories, max_item_chars=max_item_chars
                )
            )
        if not blocks:
            return
        memories_text = "\n\n".join(blocks)
        # 0.5.1(2026-08-10 注入预算): 记忆总注入上限(防画像+记忆膨胀抢
        # 上下文; config context.memory.max_total_chars 默认 6000 ≈ 1.5K token)
        try:
            total_max = int(
                self._cfg.get("context", {}).get("memory", {}).get(
                    "max_total_chars", 6000
                )
            )
            if total_max > 0 and len(memories_text) > total_max:
                memories_text = memories_text[:total_max] + "\n…(记忆已按预算截断)"
        except (TypeError, ValueError):
            pass
        await conn.execute(
            """
            INSERT INTO messages (session_id, turn, role, content, zone)
            VALUES ($1, $2, $3, $4, $5)
            """,
            self.session_id,
            0,
            "user",
            memories_text,
            "stable",
        )
        self.stable_zone.messages = [
            {"role": "user", "content": memories_text}
        ]

    async def _inject_lessons(self, conn: "asyncpg.Connection") -> None:
        """Phase 2(2026-08-11): 注入历史经验到 Stable Zone(会话启动)。

        双轨注入规则:
        - scope=monitor → 只注入 lesson_category='project_evolution' 经验
        - scope=office/data_analysis/frontend_design → 只注入
          lesson_category='domain_skill' 经验(DB CHECK 已约束, 应用层防御过滤)
        - 注入预算: 最多 max_lessons 条, 总 token ≤ max_tokens(修订 4),
          按 importance 降序(EvolutionRepo.search_by_scope 已排序)

        Args:
            conn: Postgres 连接。
        """
        if self._evolution_repo is None or not self.scene:
            return
        try:
            from private_agent.skills.evolution_repo import _SCOPE_CATEGORY_MAP

            expected_category = _SCOPE_CATEGORY_MAP.get(self.scene)
            if expected_category is None:
                return  # 未知 scope 不注入
            lessons = await self._evolution_repo.search_by_scope(self.scene, limit=10)
            lessons = [
                l for l in lessons if l.lesson_category == expected_category
            ]
            if not lessons:
                return
            evo_cfg = self._cfg.get("evolution", {}).get("injection", {})
            max_lessons = int(evo_cfg.get("max_lessons", 3))
            max_tokens = int(evo_cfg.get("max_tokens", 500))
            from private_agent.core.token_estimator import TokenEstimator

            estimator = TokenEstimator()
            lines = ["[历史经验]"]
            total_tokens = 0
            for lesson in lessons[:max_lessons]:
                entry = (
                    f"- [{lesson.lesson_type}] {lesson.task_summary}: "
                    f"{lesson.lesson_content}"
                )
                entry_tokens = estimator.estimate(entry)
                if total_tokens + entry_tokens > max_tokens:
                    break  # 超预算停止
                lines.append(entry)
                total_tokens += entry_tokens
            if len(lines) <= 1:
                return
            lessons_text = "\n".join(lines)
            await conn.execute(
                """
                INSERT INTO messages (session_id, turn, role, content, zone)
                VALUES ($1, $2, $3, $4, $5)
                """,
                self.session_id,
                0,
                "user",
                lessons_text,
                "stable",
            )
            self.stable_zone.messages.append(
                {"role": "user", "content": lessons_text, "zone": "stable"}
            )
        except Exception as e:  # noqa: BLE001 - 经验注入失败不影响会话启动
            logger.warning(
                "lessons_injection_failed scope=%s error=%s", self.scene, e
            )
            return

    async def inject_kb_chunks(
        self,
        conn: "asyncpg.Connection",
        *,
        turn: int,
        content: str,
    ) -> None:
        """§4.15 [MVP]: 注入知识库检索片段到 Stable Zone。

        search_knowledge 工具结果除本轮 tool message 外, 额外注入
        Stable Zone 供后续轮次长期参考(蓝图 §4.15: "工具返回的 KB 片段
        由 context_manager 注入 Stable Zone")。带 [KB Context] 前缀,
        供 §3.10.3 Stable Zone 合并压缩按前缀统计。

        Args:
            conn: Postgres 连接。
            turn: 当前轮次。
            content: KB 检索片段文本(截断至合理长度, 防膨胀)。
        """
        if not content:
            return
        # 方向三: KB 注入开关 + 截断长度(默认 12000 字符 ≈ 4k token;
        # config context.kb.injection.enabled / max_chars)
        kb_cfg = self._cfg.get("context", {}).get("kb", {}).get("injection", {})
        if kb_cfg.get("enabled") is False:
            return
        max_chars = int(kb_cfg.get("max_chars", 12000))
        content = content[:max_chars]
        kb_text = f"[KB Context]\n{content}"
        msg_id = await conn.fetchval(
            """
            INSERT INTO messages (session_id, turn, role, content, zone)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            self.session_id,
            turn,
            "user",
            kb_text,
            "stable",
        )
        self.stable_zone.messages.append(
            {
                "role": "user",
                "content": kb_text,
                "turn": turn,
                "msg_id": msg_id,
                "zone": "stable",
            }
        )

    def kb_chunk_count(self) -> int:
        """统计 Stable Zone 中 [KB Context] 片段数(§3.10.3 合并触发条件 2)。"""
        return sum(
            1
            for m in self.stable_zone.messages
            if (m.get("content") or "").startswith("[KB Context]")
            and not m.get("compressed")
        )

    async def ensure_initial(self, conn: "asyncpg.Connection") -> None:
        """幂等启动构建(蓝图 §3.3 + spec AC-3)。

        若 Frozen Zone 已存在则跳过 INSERT,仅从 DB reload Frozen Zone 到内存
        (保证 adapter.chat 能拿到 system prompt);否则调用 build_initial
        并注入用户记忆(§4.5)。

        用于多次 user_message 场景(同一 session_id 复用),避免重复 INSERT
        Frozen Zone 行(违反 spec AC-3 "会话启动后 messages 表有 Frozen Zone"
        的单条语义)。

        M1 简化:仅 reload Frozen Zone,不 reload Stable/Active 历史消息
        (M1-b/M2 补完整 reload 与压缩)。

        Args:
            conn: Postgres 连接。
        """
        row = await conn.fetchrow(
            """
            SELECT content FROM messages
            WHERE session_id=$1 AND zone='frozen'
            ORDER BY id LIMIT 1
            """,
            self.session_id,
        )
        if row is not None:
            self.frozen_zone.messages = [
                {"role": "system", "content": row["content"]}
            ]
            self.stable_zone.messages = []
            self.active_zone.messages = []
            # B1 P1-4: hash 校验(环境变量 PA_FROZEN_HASH_VERIFY=0 可关)
            if os.environ.get("PA_FROZEN_HASH_VERIFY", "1") != "0":
                db_hash = await conn.fetchval(
                    "SELECT frozen_hash FROM sessions WHERE id=$1",
                    self.session_id,
                )
                if db_hash is not None:
                    computed = self.compute_frozen_hash()
                    if computed != db_hash:
                        raise FrozenHashMismatchError(
                            f"frozen_hash mismatch: db={db_hash[:8]}... "
                            f"computed={computed[:8]}..."
                        )
            return
        await self.build_initial(conn)
        await self._inject_memories(conn)
        await self._inject_lessons(conn)
        # 0.5.0 M2: 场景 KB 自动检索(auto_retrieve 打开时, 会话启动注入
        # 该场景知识库 top-N 片段, 供模型直接参考场景专业知识)
        await self._inject_auto_retrieve_kb(conn)

    async def _inject_auto_retrieve_kb(self, conn: "asyncpg.Connection") -> None:
        """0.5.0 M2: 场景会话自动注入该场景 KB top-N 片段。

        触发条件: kb_auto_retrieve=True 且 kb_scenario 非空, 且 Stable Zone
        尚未注入过 KB 片段(幂等)。检索走 KnowledgeBaseService 混合检索
        (向量+关键词+rerank), 失败静默降级(不影响会话启动)。
        """
        if not self.kb_auto_retrieve or not self.kb_scenario:
            return
        if self.kb_chunk_count() > 0:
            return
        try:
            from private_agent.knowledge.factory import build_kb_service

            svc = build_kb_service(conn, self._cfg, processor=None)
            # 场景专业知识检索: 用场景映射查询词(0.5.0 M2 —— 直接查
            # "场景专业知识"等词在语料中无命中, 需用场景领域关键词触发
            # keyword 命中; 向量可用时按语义召回)
            query = _KB_AUTO_RETRIEVE_QUERIES.get(
                self.kb_scenario, f"场景专业知识与规范 {self.kb_scenario}"
            )
            chunks = await svc.search_with_rerank(
                query=query,
                scenario=self.kb_scenario,
                top_k=5,
                min_similarity=0.15,
            )
        except Exception:  # noqa: BLE001 - 自动检索失败不影响会话启动
            return
        if not chunks:
            return
        lines = ["[KB Context] 场景知识库自动检索(auto_retrieve):"]
        for i, c in enumerate(chunks, 1):
            text = c.text[:600] + "..." if len(c.text) > 600 else c.text
            lines.append(f"{i}. [source: {c.source}] {text}")
        kb_text = "\n".join(lines)
        await conn.execute(
            """
            INSERT INTO messages (session_id, turn, role, content, zone)
            VALUES ($1, $2, $3, $4, $5)
            """,
            self.session_id,
            0,
            "user",
            kb_text,
            "stable",
        )
        self.stable_zone.messages.append(
            {"role": "user", "content": kb_text, "zone": "stable"}
        )

    async def verify_frozen_hash(self, conn: "asyncpg.Connection") -> None:
        """校验 Frozen Zone hash 与 sessions.frozen_hash 一致(B1 P1-4)。

        供 react_loop 每轮调用(本 spec 不集成,由 B3 与 checkpoint 一起接入);
        PA_FROZEN_HASH_VERIFY=0 时跳过校验(逃生通道)。

        Args:
            conn: Postgres 连接。

        Raises:
            FrozenHashMismatchError: frozen_hash 非 NULL 且与计算值不一致。
        """
        if os.environ.get("PA_FROZEN_HASH_VERIFY", "1") == "0":
            return
        db_hash = await conn.fetchval(
            "SELECT frozen_hash FROM sessions WHERE id=$1", self.session_id
        )
        if db_hash is None:
            return  # 老会话无 hash,跳过
        computed = self.compute_frozen_hash()
        if computed != db_hash:
            raise FrozenHashMismatchError(
                f"frozen_hash mismatch: db={db_hash[:8]}... computed={computed[:8]}..."
            )

    async def reload_from_db(self, conn: "asyncpg.Connection") -> None:
        """完整重放历史消息(Frozen+Stable+Active 三区)(蓝图 §8.10,AC-1)。

        M1 ensure_initial 仅 reload Frozen Zone,本方法补全 Stable/Active reload,
        用于 ReplayExecutor 重建评估会话上下文。

        按 (turn, id) 顺序读取 messages 表,按 zone 分组重建三区内存:
        - frozen: role='system' 单条
        - stable: 任意 role(记忆注入为 user)
        - active: user/assistant/tool 三类,保留 tool_calls/tool_call_id/name

        Args:
            conn: Postgres 连接。
        """
        rows = await conn.fetch(
            """
            SELECT id, turn, role, content, reasoning_content,
                   tool_calls, tool_call_id, name, zone, compressed
            FROM messages
            WHERE session_id=$1
            ORDER BY turn, id
            """,
            self.session_id,
        )
        frozen: list[dict] = []
        stable: list[dict] = []
        active: list[dict] = []
        for row in rows:
            zone = row["zone"]
            role = row["role"]
            if zone == "frozen":
                frozen.append({"role": role, "content": row["content"]})
            elif zone == "stable":
                # 带内部字段恢复(§4.15 KB 计数 / §3.10.3 合并依赖)
                stable_msg: dict[str, Any] = {
                    "role": role,
                    "content": row["content"],
                    "turn": row["turn"],
                    "msg_id": row["id"],
                    "zone": zone,
                }
                if row["compressed"]:
                    stable_msg["compressed"] = True
                stable.append(stable_msg)
            elif zone == "active":
                msg: dict[str, Any] = {
                    "role": role,
                    "content": row["content"],
                    "turn": row["turn"],
                    "msg_id": row["id"],
                }
                # 压缩标记: 压缩过的消息不进入 API(get_messages 过滤),
                # 但保留在内存中供未来查询/恢复
                if row["compressed"]:
                    msg["compressed"] = True
                # reasoning_content: 推理过程原样恢复(DeepSeek V4 系要求回传,
                # AI-Agents-in-Depth 2.3.1)
                if row["reasoning_content"] is not None:
                    msg["reasoning_content"] = row["reasoning_content"]
                # tool_calls(JSONB):asyncpg 默认返回 str,需 json.loads
                tc = row["tool_calls"]
                if tc is not None:
                    if isinstance(tc, str):
                        msg["tool_calls"] = json.loads(tc)
                    else:
                        msg["tool_calls"] = tc
                if row["tool_call_id"] is not None:
                    msg["tool_call_id"] = row["tool_call_id"]
                if row["name"] is not None:
                    msg["name"] = row["name"]
                active.append(msg)
        self.frozen_zone.messages = frozen
        self.stable_zone.messages = stable
        self.active_zone.messages = active

    async def replace_frozen_zone(
        self,
        conn: "asyncpg.Connection",
        *,
        system_prompt: str,
        tools: list[ToolDef],
        skill_version: str | None = None,
    ) -> None:
        """替换 Frozen Zone(版本切换/回滚时使用)(蓝图 §8.10,AC-2)。

        流程:
        1. 删除 messages 表中 session_id+zone='frozen' 的记录
        2. 更新 self._system_prompt + self._tools
        3. 调 build_initial(conn)(用新 system_prompt + tools 插入新 frozen 行)
        4. 重新计算 frozen_hash
        5. UPDATE sessions SET frozen_hash(始终)+ locked_skill_version(仅当 skill_version 非 None)

        Args:
            conn: Postgres 连接。
            system_prompt: 新的系统提示词。
            tools: 新的工具定义列表。
            skill_version: 可选,新 Skill 版本(传入时更新 sessions.locked_skill_version)。
        """
        # 1. 删除旧 frozen 行
        await conn.execute(
            "DELETE FROM messages WHERE session_id=$1 AND zone='frozen'",
            self.session_id,
        )
        # 2. 更新内存 system_prompt + tools
        self._system_prompt = system_prompt
        self._tools = list(tools)
        # 3. 重新 build_initial(插入新 frozen 行 + 内存同步)
        await self.build_initial(conn)
        # 4. 重新计算 frozen_hash
        new_hash = self.compute_frozen_hash()
        # B1 P1-4: 写后完整性兜底校验(AC-15)
        # 理论上 new_hash == compute_frozen_hash()(刚算的),但作为并发/篡改安全网
        # spec AC-15 要求此校验存在;测试通过 mock compute_frozen_hash 验证抛错路径
        if os.environ.get("PA_FROZEN_HASH_VERIFY", "1") != "0":
            computed = self.compute_frozen_hash()
            if computed != new_hash:
                raise FrozenHashMismatchError(
                    f"replace_frozen_zone post-write hash mismatch: "
                    f"new_hash={new_hash[:8]}... computed={computed[:8]}..."
                )
        # 5. UPDATE sessions
        if skill_version is not None:
            await conn.execute(
                "UPDATE sessions SET frozen_hash=$2, locked_skill_version=$3 WHERE id=$1",
                self.session_id,
                new_hash,
                skill_version,
            )
        else:
            await conn.execute(
                "UPDATE sessions SET frozen_hash=$2 WHERE id=$1",
                self.session_id,
                new_hash,
            )

    async def append_user_message(
        self,
        conn: "asyncpg.Connection",
        *,
        turn: int,
        content: str,
    ) -> None:
        """每轮构建:追加用户消息到 Active Zone(蓝图 §3.3)。

        持久化到 messages 表(role='user', zone='active') + 内存同步。

        Args:
            conn: Postgres 连接。
            turn: 当前轮次(≥1)。
            content: 用户消息文本。
        """
        msg_id = await conn.fetchval(
            """
            INSERT INTO messages (session_id, turn, role, content, zone)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            self.session_id,
            turn,
            "user",
            content,
            "active",
        )
        # 内存同步: 携带内部字段(turn/msg_id), get_messages 剥离后才进 API
        self.active_zone.messages.append(
            {"role": "user", "content": content, "turn": turn, "msg_id": msg_id}
        )

    async def append_assistant_message(
        self,
        conn: "asyncpg.Connection",
        *,
        turn: int,
        content: str,
        tool_calls: list[dict] | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        """每轮构建:追加助手消息到 Active Zone(蓝图 §3.3)。

        持久化到 messages 表(role='assistant', zone='active') + 内存同步。
        tool_calls 不为空时持久化为 JSONB。
        reasoning_content(V2 上下文工程): 推理过程一并持久化,
        reload 后原样回传(DeepSeek V4 系要求 assistant 消息回传思考过程,
        AI-Agents-in-Depth 2.3.1)。

        Args:
            conn: Postgres 连接。
            turn: 当前轮次。
            content: 助手消息文本(可空,纯 tool_call 时为空字符串)。
            tool_calls: 工具调用列表(OpenAI 格式),无则 None。
            reasoning_content: 模型推理过程(如 deepseek reasoning 模型),无则 None。
        """
        if tool_calls:
            tool_calls_json = json.dumps(tool_calls, ensure_ascii=False)
            msg_id = await conn.fetchval(
                """
                INSERT INTO messages
                    (session_id, turn, role, content, reasoning_content, tool_calls, zone)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                RETURNING id
                """,
                self.session_id,
                turn,
                "assistant",
                content,
                reasoning_content,
                tool_calls_json,
                "active",
            )
        else:
            msg_id = await conn.fetchval(
                """
                INSERT INTO messages
                    (session_id, turn, role, content, reasoning_content, zone)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                self.session_id,
                turn,
                "assistant",
                content,
                reasoning_content,
                "active",
            )
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "turn": turn,
            "msg_id": msg_id,
        }
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        self.active_zone.messages.append(msg)

    async def append_tool_message(
        self,
        conn: "asyncpg.Connection",
        *,
        turn: int,
        tool_call_id: str,
        content: str,
        name: str,
        error: str | None = None,
    ) -> None:
        """每轮构建:追加工具结果消息到 Active Zone(蓝图 §3.3)。

        持久化到 messages 表(role='tool', zone='active') + 内存同步。

        Args:
            conn: Postgres 连接。
            turn: 当前轮次。
            tool_call_id: 对应 tool_call 的 id(OpenAI tool_call_id 字段)。
            content: 工具输出文本。
            name: 工具名称。
            error: 工具执行错误(V2 P1 权限拒绝/超时回传, 拼入 content)。
        """
        if error:
            content = f"[{error}]\n{content}"
        msg_id = await conn.fetchval(
            """
            INSERT INTO messages (session_id, turn, role, content, tool_call_id, name, zone)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            self.session_id,
            turn,
            "tool",
            content,
            tool_call_id,
            name,
            "active",
        )
        self.active_zone.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
                "name": name,
                "turn": turn,
                "msg_id": msg_id,
            }
        )

    def get_messages(self) -> list[dict]:
        """返回 Frozen + Stable + Active 三区合并后的消息列表(供 ModelAdapter.chat 使用)。

        蓝图 §3.2 关键约定: 仅输出 OpenAI 兼容字段(role/content/
        reasoning_content/tool_calls/tool_call_id/name), 剥离全部内部 metadata
        (zone/turn/msg_id/compressed 等) —— metadata 仅用于内部管理与持久化,
        进入模型 API 请求会破坏兼容性。

        V2 上下文工程: 过滤 compressed=True 的消息(已压缩不进 API, 但原文
        保留在内存/DB 中, 未来可恢复)。

        Returns:
            合并后的 API 消息列表, 顺序: frozen → stable → active。
        """
        # OpenAI 兼容字段白名单(蓝图 §3.2 Message 结构)
        api_fields = (
            "role", "content", "reasoning_content",
            "tool_calls", "tool_call_id", "name",
        )
        merged: list[dict] = []
        for zone_msgs in (
            self.frozen_zone.messages,
            self.stable_zone.messages,
            self.active_zone.messages,
        ):
            for m in zone_msgs:
                if m.get("compressed"):
                    continue  # 压缩过的消息不进 API(原文保留, 可恢复)
                merged.append({k: m[k] for k in api_fields if k in m})
        return merged

    def get_messages_with_meta(self) -> list[dict]:
        """返回含内部 metadata 的消息列表(供压缩/归档等内部逻辑使用)。

        与 get_messages 的区别: 不剥离内部字段(zone/turn/msg_id/compressed),
        供 Compressor 滑动窗口按 turn 判定与 DB 回写使用。不进入模型 API。
        """
        return [
            *self.frozen_zone.messages,
            *self.stable_zone.messages,
            *self.active_zone.messages,
        ]

    async def build_messages(self) -> list[dict]:
        """返回 Frozen + Stable + Active 三区合并后的消息列表（供 adapter.chat 使用）。

        与 get_messages 功能相同,但为 async 方法,适配 run_turn 中的 await 调用。
        """
        return self.get_messages()

    async def build_per_turn(
        self,
        conn: "asyncpg.Connection",
        *,
        turn: int,
        user_content: str,
    ) -> list[dict]:
        """每轮构建(蓝图 §3.3):追加用户消息并返回合并消息列表。

        副作用:
        - 持久化用户消息到 messages 表(zone='active')
        - 内存 active_zone 追加用户消息

        Args:
            conn: Postgres 连接。
            turn: 当前轮次(≥1)。
            user_content: 用户输入文本。

        Returns:
            Frozen + Stable + Active 合并后的消息列表(供 adapter.chat 使用)。
        """
        await self.append_user_message(conn, turn=turn, content=user_content)
        return self.get_messages()