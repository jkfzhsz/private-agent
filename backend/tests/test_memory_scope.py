"""0.5.0 M1 场景独立记忆测试(设计文档 §8: test_memory_scope.py)。

覆盖:
- 提取打标: [type@scope] 解析 / 会话场景兜底 / 未标 → global
- 注入过滤: 场景会话 = 全局画像(global_n) + 场景记忆(scene_n 配额)
- 全局常驻: get_global_core 只取 global+preference 高 importance
- 隔离: 场景 A 记忆不出现在场景 B 注入集; 全局记忆仅按画像子集注入
- 数据层: scope 列默认 global / 索引存在 / 归档表 + 画像表创建
- 注入排序去重: _rank_memories(importance × 时间衰减 + 内容 hash 去重)
"""

import pytest
import asyncpg

from private_agent.memory.manager import MemoryManager
from private_agent.memory.memories_repo import MemoriesRepo, Memory

TEST_DSN = "postgresql://postgres:123123@localhost:5432/private_agent_test"


@pytest.fixture
async def schema():
    """重建测试库 schema(幂等迁移, 含 0.5.0 M1 scope 列/归档表/画像表)。"""
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
    """测试库连接。"""
    conn = await asyncpg.connect(TEST_DSN)
    yield conn
    await conn.close()


# ── 提取打标 ──────────────────────────────────────────────────────────────


def test_parse_extracted_scope_explicit():
    """模型显式输出 [type@scope] → 按标注归属。"""
    text = (
        "[preference@global] 用户偏好中文注释\n"
        "[fact@data_analysis] 持有腾讯控股 1000 股\n"
        "[todo@office] 下周交付季度报告\n"
    )
    memories = MemoryManager._parse_extracted(text, source_session_id=1)
    scopes = {m.type: m.scope for m in memories}
    assert scopes == {
        "preference": "global",
        "fact": "data_analysis",
        "todo": "office",
    }


def test_parse_extracted_scope_fallback():
    """未标 scope → 按会话场景兜底; 会话无场景 → global。"""
    text = "[preference] 喜欢简洁输出\n"
    mems = MemoryManager._parse_extracted(text, 1, scope="data_analysis")
    assert mems[0].scope == "data_analysis"
    mems2 = MemoryManager._parse_extracted(text, 1, scope=None)
    assert mems2[0].scope == "global"
    mems3 = MemoryManager._parse_extracted(text, 1, scope="weird-scope")
    assert mems3[0].scope == "global"


def test_parse_extracted_legacy_format():
    """旧格式 [type] content(无 @) 与未知类型丢弃行为不变。"""
    text = (
        "[decision] 采用 Postgres 方案\n"
        "[weird] 不合法类型\n"
        "裸行无格式\n"
    )
    memories = MemoryManager._parse_extracted(text, 1)
    assert len(memories) == 1
    assert memories[0].type == "decision"
    assert memories[0].scope == "global"


# ── 数据层: scope 列 / 索引 / 新表 ───────────────────────────────────────


async def test_scope_column_default_and_index(conn):
    """scope 列默认 'global'; 部分索引存在; 新表创建。"""
    assert await conn.fetchval(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name='user_memories' AND column_name='scope'"
    ) == "'global'::character varying"
    assert await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_memories_scope')"
    ) is True
    assert await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE tablename='user_memories_archive')"
    ) is True
    assert await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE tablename='user_profile')"
    ) is True


async def test_insert_scope_and_fallback(conn):
    """insert 显式 scope; 非法 scope 回落 global。"""
    repo = MemoriesRepo(conn)
    mid = await repo.insert(Memory(type="fact", content="场景记忆", scope="office"))
    mid2 = await repo.insert(Memory(type="fact", content="非法 scope 记忆", scope="bogus"))
    assert await conn.fetchval(
        "SELECT scope FROM user_memories WHERE id=$1", mid
    ) == "office"
    assert await conn.fetchval(
        "SELECT scope FROM user_memories WHERE id=$1", mid2
    ) == "global"


# ── 注入: 场景配额 + 隔离 ────────────────────────────────────────────────


async def test_load_user_memories_scene_quota(conn):
    """场景会话: 全局画像 2 条 + 场景记忆 8 条(limit=10, inject_global_n=2)。"""
    repo = MemoriesRepo(conn)
    # 全局偏好(常驻画像候选)
    await repo.insert(Memory(type="preference", content="全局: 中文交流", importance=0.9, scope="global"))
    await repo.insert(Memory(type="preference", content="全局: 结论先行", importance=0.8, scope="global"))
    await repo.insert(Memory(type="preference", content="全局: 低重要偏好", importance=0.1, scope="global"))
    # 场景记忆(白圭)
    for i in range(12):
        await repo.insert(Memory(
            type="fact", content=f"白圭持仓: 标的{i}", importance=0.5 + i * 0.02,
            scope="data_analysis",
        ))

    mgr = MemoryManager(memories_repo=repo, inject_limit=10, inject_global_n=2)
    mems = await mgr.load_user_memories(scope="data_analysis")

    # 总数 ≤ 10; 全局只 2 条且为高 importance 偏好; 场景 8 条
    assert len(mems) == 10
    globals_ = [m for m in mems if m.scope == "global"]
    scenes = [m for m in mems if m.scope == "data_analysis"]
    assert len(globals_) == 2
    assert len(scenes) == 8
    assert all(m.content.startswith("全局:") and m.importance >= 0.8 for m in globals_)
    assert all(m.content.startswith("白圭持仓:") for m in scenes)


async def test_load_user_memories_global_session(conn):
    """全局会话: 只注入 global scope 记忆。"""
    repo = MemoriesRepo(conn)
    await repo.insert(Memory(type="preference", content="全局偏好A", importance=0.9, scope="global"))
    await repo.insert(Memory(type="fact", content="白圭私有", importance=0.9, scope="data_analysis"))
    mgr = MemoryManager(memories_repo=repo, inject_limit=10, inject_global_n=2)
    mems = await mgr.load_user_memories(scope=None)
    assert all(m.scope == "global" for m in mems)
    assert any("全局偏好A" in m.content for m in mems)
    assert all("白圭私有" not in m.content for m in mems)


async def test_scene_isolation(conn):
    """隔离: 场景 A 记忆不出现在场景 B 注入集。"""
    repo = MemoriesRepo(conn)
    await repo.insert(Memory(type="fact", content="子瞻的工作文档目录: D:\\work", importance=0.9, scope="office"))
    await repo.insert(Memory(type="fact", content="清和的健康目标: 早睡", importance=0.9, scope="frontend_design"))
    mgr = MemoryManager(memories_repo=repo, inject_limit=10, inject_global_n=2)
    mems_office = await mgr.load_user_memories(scope="office")
    mems_qinghe = await mgr.load_user_memories(scope="frontend_design")
    assert all(m.scope == "office" for m in mems_office)
    assert all(m.scope == "frontend_design" for m in mems_qinghe)
    assert all("清和" not in m.content for m in mems_office)
    assert all("子瞻" not in m.content for m in mems_qinghe)


# ── 注入排序去重(0.5.0 M3 B4) ───────────────────────────────────────────


def test_rank_memories_dedup_and_decay():
    """同内容只留一条; importance × 时间衰减排序。"""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    mems = [
        Memory(type="fact", content="重复内容", importance=0.5, last_accessed_at=now),
        Memory(type="fact", content="重复内容 ", importance=0.9, last_accessed_at=now),
        Memory(type="fact", content="新近高重要", importance=0.8, last_accessed_at=now - timedelta(days=1)),
        Memory(type="fact", content="久远低重要", importance=0.7, last_accessed_at=now - timedelta(days=180)),
    ]
    ranked = MemoryManager._rank_memories(mems, now=now)
    contents = [m.content.strip() for m in ranked]
    # 去重: "重复内容" 只留一条(importance 0.9 的优先)
    assert contents.count("重复内容") == 1
    assert ranked[0].content.strip() == "重复内容" and ranked[0].importance == 0.9
    # 时间衰减: 0.8×1天 > 0.7×180天
    assert ranked[1].importance == 0.8
    assert ranked[2].importance == 0.7


# ── 格式化: 场景标注 ─────────────────────────────────────────────────────


def test_format_memories_scope_tag():
    """format_memories_for_stable 场景记忆标注 @scope, 全局不标。"""
    mems = [
        Memory(type="fact", content="白圭持仓", scope="data_analysis"),
        Memory(type="preference", content="全局偏好", scope="global"),
    ]
    text = MemoryManager.format_memories_for_stable(mems)
    assert "[fact@data_analysis] 白圭持仓" in text
    assert "[preference] 全局偏好" in text
