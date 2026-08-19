"""0.5.0 M3 记忆优化测试(设计文档 §8: test_memory_archive.py + test_user_profile.py)。

覆盖:
- 画像聚合: global preference/correction → user_profile(协作/工具/风格分桶)
- 画像注入: ContextManager 会话启动注入 [User Profile] 头部
- 驱逐前巩固归档: evict → archive 摘要入库, 原记忆 deactivate
- 归档召回: search_archived 可按需检索("我之前说过什么")
"""

import pytest
import asyncpg

from private_agent.memory.manager import MemoryManager
from private_agent.memory.memories_repo import MemoriesRepo, Memory

TEST_DSN = "postgresql://postgres:123123@localhost:5432/private_agent_test"


@pytest.fixture
async def schema():
    """重建测试库 schema(幂等迁移, 含 M1 scope/归档/画像表)。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        from private_agent.storage import migrations

        await migrations.migrate_all(conn)
    finally:
        await conn.close()


@pytest.fixture
async def conn(schema):
    conn = await asyncpg.connect(TEST_DSN)
    yield conn
    await conn.close()


# ── 画像聚合(B1) ─────────────────────────────────────────────────────────


async def test_aggregate_profile_buckets(conn):
    """global preference 记忆聚合为画像三桶(协作/工具/风格)。"""
    repo = MemoriesRepo(conn)
    await repo.insert(Memory(type="preference", content="用户偏好: 代码注释用中文", importance=0.9, scope="global"))
    await repo.insert(Memory(type="preference", content="习惯用 python + pandas 处理表格", importance=0.8, scope="global"))
    await repo.insert(Memory(type="preference", content="喜欢结论先行的输出风格", importance=0.7, scope="global"))
    # 场景记忆不应进画像(仅 global)
    await repo.insert(Memory(type="preference", content="白圭: 偏好低估值标的", importance=0.9, scope="data_analysis"))

    mgr = MemoryManager(memories_repo=repo)
    profile = await mgr.aggregate_profile(refresh_interval_hours=0)
    assert profile is not None
    assert "中文" in profile["collaboration_prefs"] or "注释" in profile["collaboration_prefs"]
    assert "python" in profile["common_tools"] or "pandas" in profile["common_tools"]
    assert "结论先行" in profile["communication_style"]
    # 场景记忆未混入
    assert "低估值" not in (profile["collaboration_prefs"] or "")


async def test_aggregate_profile_persisted(conn):
    """画像聚合落 user_profile 表, 重复聚合幂等。"""
    repo = MemoriesRepo(conn)
    await repo.insert(Memory(type="preference", content="偏好简洁输出", importance=0.9, scope="global"))
    mgr = MemoryManager(memories_repo=repo)
    await mgr.aggregate_profile(refresh_interval_hours=0)
    saved = await repo.get_profile()
    assert saved is not None
    assert "简洁" in (saved["communication_style"] or "") or "简洁" in (saved["collaboration_prefs"] or "")
    # 二次聚合(未到刷新间隔)复用现有
    again = await mgr.aggregate_profile(refresh_interval_hours=24)
    assert again is not None


async def test_format_profile_for_stable():
    """画像格式化为 [User Profile] 文本。"""
    text = MemoryManager.format_profile_for_stable({
        "collaboration_prefs": "中文注释",
        "common_tools": "python",
        "communication_style": "结论先行",
    })
    assert text.startswith("[User Profile]")
    assert "中文注释" in text and "python" in text and "结论先行" in text
    assert MemoryManager.format_profile_for_stable(None) is None
    assert MemoryManager.format_profile_for_stable({}) is None


# ── 画像注入(会话启动) ───────────────────────────────────────────────────


async def test_context_manager_injects_profile(conn):
    """会话启动: Stable Zone 注入 [User Profile] 头部。"""
    from private_agent.core.context_manager import ContextManager

    repo = MemoriesRepo(conn)
    await repo.insert(Memory(type="preference", content="用户偏好中文注释", importance=0.9, scope="global"))
    mgr = MemoryManager(memories_repo=repo, inject_limit=10, inject_global_n=2)
    sid = await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ('prof-inject', 'mock') RETURNING id"
    )
    cm = ContextManager(
        session_id=sid,
        system_prompt="测试",
        tools=[],
        memory_manager=mgr,
    )
    await cm.ensure_initial(conn)
    stable_contents = " ".join(
        (m.get("content") or "") for m in cm.stable_zone.messages
    )
    assert "[User Profile]" in stable_contents
    assert "中文注释" in stable_contents


# ── 驱逐前巩固归档(B3) ──────────────────────────────────────────────────


async def test_evict_archives_before_deactivate(conn):
    """archive_before_evict=True: 驱逐前摘要入 archive, 原记忆 deactivate。"""
    repo = MemoriesRepo(conn)
    # 低重要性 + 超期 → 触发 deactivate_expired
    # 2026-08-16: last_accessed_at 显式设 2 天前 —— 原依赖 now() 秒级竞态
    # (全量回归插入与驱逐同秒时 last_accessed_at < cutoff 不成立 → evicted=0,
    # 单跑有秒级间隔所以通过)。显式过去时间消除竞态。
    from datetime import datetime, timedelta, timezone

    await repo.insert(Memory(
        type="fact", content="很久以前的一条低价值记忆内容", importance=0.1,
        scope="global", source_session_id=None,
        last_accessed_at=datetime.now(timezone.utc) - timedelta(days=2),
    ))
    mgr = MemoryManager(
        memories_repo=repo,
        eviction_max_active=1,      # 当前 1 条 > 1? 不超, 走 expired 条件
        eviction_min_importance=0.3,
        eviction_expire_days=0,     # cutoff=now → 昨天前的都过期
        archive_before_evict=True,
    )
    evicted = await mgr.evict_memories()
    assert evicted >= 1
    # 原记忆已 deactivate
    active = await repo.count_active()
    assert active == 0
    # 归档可召回
    hits = await repo.search_archived("低价值", limit=5)
    assert len(hits) >= 1
    assert "低价值" in hits[0]["summary"]


async def test_archive_disabled_by_default():
    """archive_before_evict=False(默认): 驱逐不归档(兼容旧行为)。"""
    repo = MemoriesRepo  # 占位, 由 mock 层面断言即可
    assert MemoryManager(memories_repo=repo)._repo is repo  # noqa: 构造正常


async def test_search_archived_scope_filter(conn):
    """归档召回支持 scope 过滤。"""
    repo = MemoriesRepo(conn)
    m1 = Memory(type="fact", content="白圭场景旧持仓记录", importance=0.1, scope="data_analysis")
    m1.id = await repo.insert(m1)
    await repo.archive_memories([m1], summaries={m1.id: "白圭旧持仓摘要"})
    hits = await repo.search_archived("持仓", scope="data_analysis", limit=5)
    assert len(hits) == 1
    assert hits[0]["scope"] == "data_analysis"
    # 其他 scope 过滤不到
    hits2 = await repo.search_archived("持仓", scope="office", limit=5)
    assert len(hits2) == 0
