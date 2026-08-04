"""蓝图 §4.2/§4.4/§4.5 MemoryManager - 记忆提取/淘汰/注入。

核心职责:
- maybe_extract: 每 EXTRACT_INTERVAL_TURNS 轮自动触发提取(§4.2)
- on_session_end: 会话结束触发提取(§4.2)
- manual_extract: UI 手动触发提取(§4.2)
- evict_memories: 提取后淘汰(§4.4)
- load_user_memories: 会话启动时注入(§4.5)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from private_agent.memory.memories_repo import (
    MEMORY_TYPES,
    TYPE_IMPORTANCE_MAP,
    MemoriesRepo,
    Memory,
)

__all__ = ["MemoryManager"]

# 蓝图 §4.2 提取 prompt 模板
EXTRACT_PROMPT_TEMPLATE = """请从以下对话中提取用户的关键信息,分类为:
1. preference: 用户偏好(语言、风格、工作习惯等)
2. fact: 事实信息(用户身份、项目背景、技术栈等)
3. todo: 待办事项(用户提到需要完成的任务)
4. decision: 已做出的决策(用户明确选择的方向)

仅提取明确出现的信息,不要推测。每条记忆格式:
[type] content

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
        eviction_max_active: 活跃记忆上限(默认 200)。
        eviction_min_importance: 淘汰重要性阈值(默认 0.3)。
        eviction_expire_days: 淘汰超期天数(默认 30)。
    """

    def __init__(
        self,
        memories_repo: MemoriesRepo,
        compress_adapter: Any | None = None,
        react_events_insert: Callable | None = None,
        extract_interval_turns: int = 8,
        inject_limit: int = 10,
        eviction_max_active: int = 200,
        eviction_min_importance: float = 0.3,
        eviction_expire_days: int = 30,
    ) -> None:
        self._repo = memories_repo
        self._compress_adapter = compress_adapter
        self._react_events_insert = react_events_insert
        self.extract_interval_turns = extract_interval_turns
        self.inject_limit = inject_limit
        self.eviction_max_active = eviction_max_active
        self.eviction_min_importance = eviction_min_importance
        self.eviction_expire_days = eviction_expire_days

    # ── 提取 ──────────────────────────────────────────────────────────────

    async def maybe_extract(
        self, session_id: int, current_turn: int
    ) -> list[Memory] | None:
        """每 extract_interval_turns 轮触发提取(蓝图 §4.2 条件 1)。

        Args:
            session_id: 会话 ID。
            current_turn: 当前轮次(≥1)。

        Returns:
            提取到的记忆列表(未触发时返回 None)。
        """
        if (
            current_turn > 0
            and current_turn % self.extract_interval_turns == 0
        ):
            return await self._extract_and_evict(session_id, current_turn)
        return None

    async def on_session_end(
        self, session_id: int, current_turn: int
    ) -> list[Memory]:
        """会话结束触发提取(蓝图 §4.2 条件 2)。

        Args:
            session_id: 会话 ID。
            current_turn: 最后轮次。

        Returns:
            提取到的记忆列表。
        """
        return await self._extract_and_evict(session_id, current_turn)

    async def manual_extract(
        self, session_id: int, current_turn: int
    ) -> list[Memory]:
        """UI 手动触发提取(蓝图 §4.2 缺口补充)。

        Args:
            session_id: 会话 ID。
            current_turn: 当前轮次。

        Returns:
            提取到的记忆列表。
        """
        return await self._extract_and_evict(session_id, current_turn)

    # ── 注入 ──────────────────────────────────────────────────────────────

    async def load_user_memories(
        self, user_id: int = 1, limit: int | None = None
    ) -> list[Memory]:
        """会话启动时调用,返回高重要性记忆(蓝图 §4.5)。

        Args:
            user_id: 用户 ID(单人场景固定为 1)。
            limit: 返回条数(默认 self.inject_limit)。

        Returns:
            高重要性记忆列表。
        """
        limit = limit if limit is not None else self.inject_limit
        memories = await self._repo.get_top_active(
            user_id, order_by="importance DESC, last_accessed_at DESC", limit=limit
        )
        if memories:
            await self._repo.batch_update_access(memories)
        return memories

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
            lines.append(f"[{m.type}] {content}")
        return "\n".join(lines)

    # ── 淘汰 ──────────────────────────────────────────────────────────────

    async def evict_memories(self, user_id: int = 1) -> int:
        """执行记忆淘汰(蓝图 §4.4)。

        条件 1: 超过上限,按 importance 升序淘汰最低的。
        条件 2: 低重要性 + 长期未访问,标记 inactive。

        Args:
            user_id: 用户 ID。

        Returns:
            本次淘汰的记忆数。
        """
        total = 0
        active = await self._repo.count_active(user_id)
        if active > self.eviction_max_active:
            excess = active - self.eviction_max_active
            evicted = await self._repo.deactivate_lowest(user_id, excess)
            total += len(evicted)

        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self.eviction_expire_days
        )
        evicted = await self._repo.deactivate_expired(
            user_id,
            min_importance=self.eviction_min_importance,
            cutoff=cutoff,
        )
        total += len(evicted)
        return total

    # ── 内部 ──────────────────────────────────────────────────────────────

    async def _extract_and_evict(
        self, session_id: int, current_turn: int
    ) -> list[Memory]:
        """提取记忆 + 淘汰 + 记录事件(蓝图 §4.2/§4.4)。"""
        memories = await self._extract_memories(session_id, current_turn)
        evicted = await self.evict_memories()
        if self._react_events_insert:
            await self._react_events_insert(
                session_id=session_id,
                turn=current_turn,
                event_type="memory_extracted",
                payload={
                    "count": len(memories),
                    "types": [m.type for m in memories],
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
        self, session_id: int, current_turn: int
    ) -> list[Memory]:
        """LLM 摘要提取记忆(蓝图 §4.2)。

        当无 compress_adapter 时(测试环境),返回空列表。
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
        parsed = self._parse_extracted(result.content, session_id)
        if parsed:
            await self._repo.batch_insert(parsed)
        return parsed

    @staticmethod
    def _parse_extracted(
        text: str, source_session_id: int
    ) -> list[Memory]:
        """解析 LLM 输出的 [type] content 格式(蓝图 §4.2 解析规则)。

        Args:
            text: LLM 输出文本。
            source_session_id: 来源会话 ID。

        Returns:
            Memory 列表。
        """
        memories: list[Memory] = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("[") and "]" in line:
                close = line.index("]")
                type_str = line[1:close].strip().lower()
                content = line[close + 1 :].strip()
                if type_str in MEMORY_TYPES:
                    importance = TYPE_IMPORTANCE_MAP.get(type_str, 0.5)
                    memories.append(
                        Memory(
                            type=type_str,
                            content=content,
                            importance=importance,
                            source_session_id=source_session_id,
                        )
                    )
                # 未匹配的行丢弃(蓝图 §4.2: type 不在枚举内的丢弃并日志告警)
        return memories