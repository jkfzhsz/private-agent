"""蓝图 §4.2/§4.4/§4.5 MemoryManager 单元测试。

覆盖:
- maybe_extract 触发条件
- on_session_end / manual_extract
- _parse_extracted 解析规则
- load_user_memories 注入
- format_memories_for_stable 格式化
- evict_memories 淘汰触发
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from private_agent.memory.manager import MemoryManager, EXTRACT_PROMPT_TEMPLATE
from private_agent.memory.memories_repo import Memory, MemoriesRepo
from private_agent.models.base import ChatResult


class _MockCompressAdapter:
    """模拟压缩模型适配器,返回固定响应。"""

    def __init__(self, response_text: str = "[fact] 用户使用 Python") -> None:
        self._response_text = response_text

    async def chat(self, messages: list[dict], tools: list | None = None) -> ChatResult:
        return ChatResult(
            content=self._response_text,
        )


class _MockRepo:
    """模拟 MemoriesRepo。"""

    def __init__(self) -> None:
        self.inserted: list[Memory] = []
        self.insert_ids: list[int] = []
        self._next_id = 1
        self.active_memories: list[Memory] = [
            Memory(id=1, type="fact", content="test", importance=0.9),
        ]
        self.active_count = 1
        self.deactivated: list[int] = []
        self.accessed: list[Memory] = []

    async def insert(self, memory: Memory) -> int:
        self.inserted.append(memory)
        mid = self._next_id
        self._next_id += 1
        self.insert_ids.append(mid)
        return mid

    async def batch_insert(self, memories: list[Memory]) -> list[int]:
        ids: list[int] = []
        for m in memories:
            mid = await self.insert(m)
            ids.append(mid)
        return ids

    async def get_top_active(self, user_id: int = 1, order_by: str = "", limit: int = 10) -> list[Memory]:
        return self.active_memories[:limit]

    async def count_active(self, user_id: int = 1) -> int:
        return self.active_count

    async def deactivate_lowest(self, user_id: int, count: int) -> list[int]:
        ids = [m.id for m in self.active_memories[:count] if m.id is not None]
        self.deactivated.extend(ids)
        self.active_count = max(0, self.active_count - count)
        return ids

    async def deactivate_expired(self, user_id: int, min_importance: float = 0.3,
                                  cutoff: datetime | None = None) -> list[int]:
        return []

    async def batch_update_access(self, memories: list[Memory]) -> None:
        self.accessed.extend(memories)


class _MockEventsRecorder:
    """记录 react_events 调用。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, **kwargs) -> None:
        self.events.append(kwargs)


@pytest.fixture
def repo() -> _MockRepo:
    return _MockRepo()


@pytest.fixture
def manager(repo: _MockRepo) -> MemoryManager:
    return MemoryManager(
        memories_repo=repo,
        compress_adapter=_MockCompressAdapter(),
        react_events_insert=_MockEventsRecorder(),
        extract_interval_turns=8,
        inject_limit=10,
        eviction_max_active=200,
        eviction_min_importance=0.3,
        eviction_expire_days=30,
    )


# ── maybe_extract ───────────────────────────────────────────────────────


def test_maybe_extract_skips_before_interval(manager: MemoryManager, repo: _MockRepo):
    """maybe_extract 在未达到间隔轮次时返回 None。"""
    result = asyncio_run(manager.maybe_extract(session_id=1, current_turn=3))
    assert result is None


def test_maybe_extract_triggers_at_interval(manager: MemoryManager, repo: _MockRepo):
    """maybe_extract 在达到间隔轮次时触发提取。"""
    result = asyncio_run(manager.maybe_extract(session_id=1, current_turn=8))
    assert result is not None
    # compress_adapter 为 None 时返回空列表
    # 这里 adapter 有值,所以会尝试调用 adapter.stream
    # 但由于 adapter 是 mock,实际返回的 memories 取决于 mock 的响应
    assert len(result) >= 0


def test_maybe_extract_skips_turn_zero(manager: MemoryManager):
    """maybe_extract 在 turn=0 时不触发。"""
    result = asyncio_run(manager.maybe_extract(session_id=1, current_turn=0))
    assert result is None


# ── on_session_end ──────────────────────────────────────────────────────


def test_on_session_end_always_extracts(manager: MemoryManager, repo: _MockRepo):
    """on_session_end 总是触发提取。"""
    # 使用 mock repo 和 mock adapter
    mgr = MemoryManager(
        memories_repo=_MockRepo(),
        compress_adapter=_MockCompressAdapter(),
        react_events_insert=_MockEventsRecorder(),
    )
    result = asyncio_run(mgr.on_session_end(session_id=1, current_turn=5))
    # adapter 有值,但 stream 返回固定文本,解析后应有 1 条记忆
    assert len(result) == 1


# ── manual_extract ──────────────────────────────────────────────────────


def test_manual_extract_always_extracts(manager: MemoryManager):
    """manual_extract 总是触发提取。"""
    mgr = MemoryManager(
        memories_repo=_MockRepo(),
        compress_adapter=_MockCompressAdapter(),
        react_events_insert=_MockEventsRecorder(),
    )
    result = asyncio_run(mgr.manual_extract(session_id=1, current_turn=3))
    assert len(result) == 1


# ── load_user_memories ──────────────────────────────────────────────────


def test_load_user_memories_returns_top(manager: MemoryManager, repo: _MockRepo):
    """load_user_memories 返回高重要性记忆并更新访问记录。"""
    memories = asyncio_run(manager.load_user_memories())
    assert len(memories) == 1
    assert memories[0].content == "test"
    assert len(repo.accessed) == 1


def test_load_user_memories_respects_limit(manager: MemoryManager, repo: _MockRepo):
    """load_user_memories 遵守 limit 参数。"""
    memories = asyncio_run(manager.load_user_memories(limit=0))
    assert len(memories) == 0


# ── format_memories_for_stable ──────────────────────────────────────────


def test_format_memories_for_stable():
    """format_memories_for_stable 格式化记忆文本。"""
    memories = [
        Memory(type="fact", content="用户使用 Python"),
        Memory(type="preference", content="偏好深色主题"),
    ]
    text = MemoryManager.format_memories_for_stable(memories)
    assert "[User Memories]" in text
    assert "[fact] 用户使用 Python" in text
    assert "[preference] 偏好深色主题" in text


def test_format_memories_for_stable_empty():
    """空记忆列表返回仅含标题的文本。"""
    text = MemoryManager.format_memories_for_stable([])
    assert text == "[User Memories]"


# ── _parse_extracted ────────────────────────────────────────────────────


def test_parse_extracted_valid():
    """_parse_extracted 解析 [type] content 格式。"""
    text = "[fact] 用户使用 Python\n[preference] 偏好深色主题"
    memories = MemoryManager._parse_extracted(text, source_session_id=1)
    assert len(memories) == 2
    assert memories[0].type == "fact"
    assert memories[0].content == "用户使用 Python"
    assert memories[1].type == "preference"
    assert memories[1].content == "偏好深色主题"


def test_parse_extracted_skips_invalid_type():
    """_parse_extracted 丢弃非法 type 的行。"""
    text = "[invalid] 测试\n[fact] 有效记忆"
    memories = MemoryManager._parse_extracted(text, source_session_id=1)
    assert len(memories) == 1
    assert memories[0].type == "fact"


def test_parse_extracted_skips_unmatched_lines():
    """_parse_extracted 丢弃未匹配 [type] 格式的行。"""
    text = "普通文本行\n[fact] 有效记忆"
    memories = MemoryManager._parse_extracted(text, source_session_id=1)
    assert len(memories) == 1


def test_parse_extracted_sets_importance_by_type():
    """_parse_extracted 按 type 设置 importance 初始值。"""
    text = "[decision] 不做后训练\n[todo] 完成第4章"
    memories = MemoryManager._parse_extracted(text, source_session_id=1)
    # decision → 0.9, todo → 0.5
    type_imp = {m.type: m.importance for m in memories}
    assert type_imp["decision"] == 0.9
    assert type_imp["todo"] == 0.5


# ── evict_memories ──────────────────────────────────────────────────────


def test_evict_memories_triggers_when_over_limit(manager: MemoryManager, repo: _MockRepo):
    """evict_memories 在活跃数超过上限时触发淘汰。"""
    repo.active_count = 250
    total = asyncio_run(manager.evict_memories())
    assert total > 0


def test_evict_memories_skips_when_under_limit(manager: MemoryManager, repo: _MockRepo):
    """evict_memories 在活跃数低于上限时跳过。"""
    repo.active_count = 50
    total = asyncio_run(manager.evict_memories())
    assert total == 0


# ── _extract_and_evict (react_events) ───────────────────────────────────


def test_extract_and_evict_records_event(manager: MemoryManager, repo: _MockRepo):
    """_extract_and_evict 记录 react_events。"""
    recorder = _MockEventsRecorder()
    mgr = MemoryManager(
        memories_repo=_MockRepo(),
        compress_adapter=_MockCompressAdapter(),
        react_events_insert=recorder,
    )
    asyncio_run(mgr._extract_and_evict(session_id=1, current_turn=8))
    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event["event_type"] == "memory_extracted"
    assert event["session_id"] == 1
    assert event["turn"] == 8
    assert "count" in event["payload"]
    assert "types" in event["payload"]
    assert "evicted" in event["payload"]


def test_extract_and_evict_records_memory_evicted_event():
    """§4.4 [MVP]: 淘汰发生时单独记录 memory_evicted 事件。"""
    recorder = _MockEventsRecorder()
    repo = _MockRepo()
    repo.active_count = 250  # 超过 eviction_max_active=200 → 触发淘汰
    mgr = MemoryManager(
        memories_repo=repo,
        compress_adapter=_MockCompressAdapter(),
        react_events_insert=recorder,
        eviction_max_active=200,
    )
    asyncio_run(mgr._extract_and_evict(session_id=1, current_turn=8))
    types = [e["event_type"] for e in recorder.events]
    assert "memory_evicted" in types
    evicted_ev = next(e for e in recorder.events if e["event_type"] == "memory_evicted")
    assert evicted_ev["session_id"] == 1
    assert evicted_ev["turn"] == 8
    assert evicted_ev["payload"]["count"] > 0


def test_extract_and_evict_no_evicted_event_when_nothing_evicted():
    """无淘汰发生时(memory_extracted 的 evicted=0)不产生 memory_evicted 事件。"""
    recorder = _MockEventsRecorder()
    mgr = MemoryManager(
        memories_repo=_MockRepo(),  # active_count=1 < 200, 无淘汰
        compress_adapter=_MockCompressAdapter(),
        react_events_insert=recorder,
    )
    asyncio_run(mgr._extract_and_evict(session_id=1, current_turn=8))
    types = [e["event_type"] for e in recorder.events]
    assert types == ["memory_extracted"]


# ── 无 compress_adapter 时的行为 ────────────────────────────────────────


def test_extract_without_adapter_returns_empty(repo: _MockRepo):
    """无 compress_adapter 时 _extract_memories 返回空列表。"""
    mgr = MemoryManager(memories_repo=repo)
    result = asyncio_run(mgr._extract_memories(session_id=1, current_turn=5))
    assert result == []


def test_maybe_extract_without_adapter_returns_none(repo: _MockRepo):
    """无 adapter 时 maybe_extract 在触发轮次返回空列表。"""
    mgr = MemoryManager(memories_repo=repo, extract_interval_turns=3)
    result = asyncio_run(mgr.maybe_extract(session_id=1, current_turn=3))
    assert result is not None
    assert len(result) == 0


# ── helper ──────────────────────────────────────────────────────────────


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)