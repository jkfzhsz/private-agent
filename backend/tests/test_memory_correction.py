"""阶段三批次3(T3.4) - 用户纠正沉淀(correction 记忆)测试(调研 round2 §4.4.1)。

覆盖 AC-20:
- 有 compress_adapter(mock) → LLM 提取纠正要点, type=correction, importance=0.9
- 无 adapter → 启发式降级(差异文本摘要), 不静默失败
- original == corrected → 不触发
- LLM 提取失败 → 降级启发式
"""
import asyncio

import pytest

from private_agent.memory.manager import MemoryManager
from private_agent.memory.memories_repo import (
    MEMORY_TYPES,
    MemoriesRepo,
    Memory,
    TYPE_IMPORTANCE_MAP,
)


class _FakeRepo(MemoriesRepo):
    """内存版 repo(mock 数据库连接)。"""

    def __init__(self) -> None:
        self.inserted: list[Memory] = []

    async def insert(self, memory: Memory) -> int:
        memory.id = len(self.inserted) + 1
        self.inserted.append(memory)
        return memory.id


class _FakeAdapter:
    """mock 压缩适配器。"""

    def __init__(self, content: str = "用户偏好使用图表而非表格") -> None:
        self._content = content
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        return type("ChatResult", (), {"content": self._content})()


class TestCorrectionExtract:
    def test_llm_extract_with_adapter(self):
        repo = _FakeRepo()
        adapter = _FakeAdapter("用户偏好使用图表而非表格")
        mgr = MemoryManager(memories_repo=repo, compress_adapter=adapter)

        memories = asyncio.run(
            mgr.maybe_extract_from_correction(
                original="用表格展示数据", corrected="用图表展示数据",
            )
        )

        assert len(memories) == 1
        assert adapter.calls == 1
        assert repo.inserted[0].type == "correction"
        assert repo.inserted[0].content == "用户偏好使用图表而非表格"
        assert repo.inserted[0].importance == pytest.approx(
            TYPE_IMPORTANCE_MAP["correction"]
        )

    def test_no_adapter_fallback_heuristic(self):
        repo = _FakeRepo()
        mgr = MemoryManager(memories_repo=repo, compress_adapter=None)

        memories = asyncio.run(
            mgr.maybe_extract_from_correction(
                original="A", corrected="B" * 50,
            )
        )

        assert len(memories) == 1
        assert memories[0].type == "correction"
        assert memories[0].content.startswith("用户纠正:")
        assert "B" in memories[0].content

    def test_identical_input_no_extract(self):
        repo = _FakeRepo()
        adapter = _FakeAdapter()
        mgr = MemoryManager(memories_repo=repo, compress_adapter=adapter)

        memories = asyncio.run(
            mgr.maybe_extract_from_correction(
                original="same", corrected="same",
            )
        )

        assert memories == []
        assert adapter.calls == 0
        assert repo.inserted == []

    def test_empty_input_no_extract(self):
        repo = _FakeRepo()
        mgr = MemoryManager(memories_repo=repo, compress_adapter=None)

        assert asyncio.run(mgr.maybe_extract_from_correction("", "x")) == []
        assert asyncio.run(mgr.maybe_extract_from_correction("x", "")) == []

    def test_llm_failure_falls_back(self):
        class _FailAdapter:
            async def chat(self, messages, tools=None, **kwargs):
                raise RuntimeError("llm down")

        repo = _FakeRepo()
        mgr = MemoryManager(memories_repo=repo, compress_adapter=_FailAdapter())

        memories = asyncio.run(
            mgr.maybe_extract_from_correction("a", "b")
        )

        assert len(memories) == 1  # 降级启发式
        assert memories[0].content.startswith("用户纠正:")

    def test_empty_llm_output_no_memory(self):
        repo = _FakeRepo()
        adapter = _FakeAdapter(content="   ")  # 空输出
        mgr = MemoryManager(memories_repo=repo, compress_adapter=adapter)

        memories = asyncio.run(mgr.maybe_extract_from_correction("a", "b"))

        assert memories == []

    def test_correction_type_in_enum(self):
        """correction 类型已纳入记忆类型枚举。"""
        assert "correction" in MEMORY_TYPES
