"""蓝图 §4.3 MemoriesRepo - user_memories 表 CRUD 测试。

依赖:
- 真实 PostgreSQL (TEST_DSN)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from private_agent.memory.memories_repo import (
    MEMORY_TYPES,
    MemoriesRepo,
    Memory,
)
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


async def _setup_schema() -> None:
    """重建 schema + 跑 migrate_all。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
    finally:
        await conn.close()


@pytest.fixture
async def conn():
    await _setup_schema()
    c = await asyncpg.connect(TEST_DSN)
    try:
        yield c
    finally:
        await c.close()


@pytest.fixture
def repo(conn: "asyncpg.Connection") -> MemoriesRepo:
    return MemoriesRepo(conn)


@pytest.fixture
async def sample_session(conn: "asyncpg.Connection") -> int:
    return await conn.fetchval(
        "INSERT INTO sessions (title) VALUES ($1) RETURNING id",
        "test_memories",
    )


# ── Memory dataclass ────────────────────────────────────────────────────


def test_memory_dataclass_defaults():
    """Memory 数据类有合理的默认值。"""
    m = Memory()
    assert m.user_id == 1
    assert m.type == "fact"
    assert m.importance == 0.5
    assert m.is_active is True


# ── insert ──────────────────────────────────────────────────────────────


async def test_insert_basic(repo: MemoriesRepo, sample_session: int):
    """插入一条记忆,返回 id。"""
    mid = await repo.insert(Memory(
        type="fact", content="用户使用 PostgreSQL",
        importance=0.7, source_session_id=sample_session,
    ))
    assert isinstance(mid, int)
    assert mid > 0


async def test_insert_invalid_type_raises(repo: MemoriesRepo):
    """非法 type 抛出 ValueError。"""
    with pytest.raises(ValueError, match="不合法"):
        await repo.insert(Memory(type="invalid", content="test"))


# ── batch_insert ────────────────────────────────────────────────────────


async def test_batch_insert(repo: MemoriesRepo, sample_session: int):
    """批量插入多条记忆。"""
    memories = [
        Memory(type="preference", content="偏好浅色主题", importance=0.6,
               source_session_id=sample_session),
        Memory(type="fact", content="项目使用 Electron + Python", importance=0.7,
               source_session_id=sample_session),
        Memory(type="decision", content="MVP 不做模型后训练", importance=0.9,
               source_session_id=sample_session),
    ]
    ids = await repo.batch_insert(memories)
    assert len(ids) == 3
    assert all(isinstance(i, int) and i > 0 for i in ids)


# ── get_top_active ──────────────────────────────────────────────────────


async def test_get_top_active_returns_ordered(
    repo: MemoriesRepo, sample_session: int
):
    """get_top_active 按 importance 降序返回。"""
    await repo.batch_insert([
        Memory(type="fact", content="low", importance=0.3,
               source_session_id=sample_session),
        Memory(type="fact", content="high", importance=0.9,
               source_session_id=sample_session),
        Memory(type="fact", content="mid", importance=0.6,
               source_session_id=sample_session),
    ])
    result = await repo.get_top_active(limit=10)
    assert len(result) == 3
    assert result[0].content == "high"
    assert result[1].content == "mid"
    assert result[2].content == "low"


async def test_get_top_active_limit(repo: MemoriesRepo, sample_session: int):
    """get_top_active 遵守 limit 参数。"""
    await repo.batch_insert([
        Memory(type="fact", content=f"m{i}", importance=0.5 + i * 0.1,
               source_session_id=sample_session)
        for i in range(5)
    ])
    result = await repo.get_top_active(limit=2)
    assert len(result) == 2


async def test_get_top_active_excludes_inactive(
    repo: MemoriesRepo, sample_session: int
):
    """get_top_active 排除 inactive 记忆。"""
    mid = await repo.insert(Memory(
        type="fact", content="active", importance=0.8,
        source_session_id=sample_session,
    ))
    await repo.insert(Memory(
        type="fact", content="inactive", importance=0.9,
        source_session_id=sample_session,
    ))
    # 人工标记 inactive
    await repo._conn.execute(
        "UPDATE user_memories SET is_active = FALSE WHERE id != $1", mid
    )
    result = await repo.get_top_active()
    assert len(result) == 1
    assert result[0].content == "active"


# ── count_active ────────────────────────────────────────────────────────


async def test_count_active(repo: MemoriesRepo, sample_session: int):
    """count_active 返回正确的活跃记忆数。"""
    await repo.batch_insert([
        Memory(type="fact", content=f"m{i}", importance=0.5,
               source_session_id=sample_session)
        for i in range(3)
    ])
    cnt = await repo.count_active()
    assert cnt == 3


# ── deactivate_lowest ───────────────────────────────────────────────────


async def test_deactivate_lowest(repo: MemoriesRepo, sample_session: int):
    """deactivate_lowest 淘汰最低重要性的记忆。"""
    ids = await repo.batch_insert([
        Memory(type="fact", content=f"m{i}", importance=0.1 * (i + 1),
               source_session_id=sample_session)
        for i in range(5)
    ])
    evicted = await repo.deactivate_lowest(user_id=1, count=2)
    assert len(evicted) == 2
    # 验证剩下 3 条活跃
    cnt = await repo.count_active()
    assert cnt == 3


# ── deactivate_expired ──────────────────────────────────────────────────


async def test_deactivate_expired(repo: MemoriesRepo, sample_session: int):
    """deactivate_expired 淘汰低重要性 + 超期未访问的记忆。"""
    # 插入一条低重要性 + 超期记忆
    old = datetime.now(timezone.utc) - timedelta(days=60)
    mid = await repo.insert(Memory(
        type="fact", content="old", importance=0.2,
        source_session_id=sample_session,
    ))
    await repo._conn.execute(
        "UPDATE user_memories SET last_accessed_at = $1 WHERE id = $2",
        old, mid,
    )
    # 插入一条高重要性记忆(不应被淘汰)
    await repo.insert(Memory(
        type="fact", content="important", importance=0.8,
        source_session_id=sample_session,
    ))
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    evicted = await repo.deactivate_expired(
        user_id=1, min_importance=0.3, cutoff=cutoff,
    )
    assert len(evicted) == 1
    # 验证高重要性记忆仍在
    cnt = await repo.count_active()
    assert cnt == 1


# ── batch_update_access ─────────────────────────────────────────────────


async def test_batch_update_access(repo: MemoriesRepo, sample_session: int):
    """batch_update_access 更新 last_accessed_at 和 access_count。"""
    mid = await repo.insert(Memory(
        type="fact", content="test", importance=0.5,
        source_session_id=sample_session,
    ))
    mem = (await repo.get_by_ids([mid]))[0]
    assert mem.access_count == 0

    await repo.batch_update_access([mem])
    updated = (await repo.get_by_ids([mid]))[0]
    assert updated.access_count == 1
    assert updated.last_accessed_at is not None


# ── get_by_ids ──────────────────────────────────────────────────────────


async def test_get_by_ids(repo: MemoriesRepo, sample_session: int):
    """get_by_ids 按 id 列表查询。"""
    ids = await repo.batch_insert([
        Memory(type="fact", content=f"m{i}", importance=0.5,
               source_session_id=sample_session)
        for i in range(3)
    ])
    result = await repo.get_by_ids(ids)
    assert len(result) == 3


async def test_get_by_ids_empty(repo: MemoriesRepo):
    """get_by_ids 空列表返回空列表。"""
    result = await repo.get_by_ids([])
    assert result == []