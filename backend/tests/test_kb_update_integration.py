"""KB 增量重灌集成测试(2026-08-27, P1-C / D2 技术债务落实)。

设计文档验收 V4: update_document 后旧 chunk deactivate、新 chunk 检索命中、
无残留 —— 真实 PostgreSQL + 真实检索路径(keyword_search ILIKE + is_active 过滤)。

覆盖:
- update 后旧 doc/chunk 停用, 新 doc/chunk 激活(状态机)
- 无残留: active chunks 只属于新 doc
- 检索: 旧内容不再命中, 新内容命中(V4 核心)
- 幂等: 相同内容重复 update 不新增文档
"""
import os

import asyncpg
import pytest

from private_agent.knowledge.kb_repo import KnowledgeBaseRepo
from private_agent.knowledge.kb_service import KnowledgeBaseService
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


async def _setup_schema() -> None:
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
def svc(conn: "asyncpg.Connection") -> KnowledgeBaseService:
    # EmbeddingService 默认 worker_pool=None → mock 全 0 向量(conftest 亦
    # 强制 PA_EMBEDDING_MOCK=1); 本测试聚焦增量重灌的状态机与 keyword 检索,
    # 向量真实性由 test_embedding_assembly 覆盖。
    return KnowledgeBaseService(kb_repo=KnowledgeBaseRepo(conn))


@pytest.mark.asyncio
async def test_update_document_deactivates_old_activates_new(svc, conn):
    """V4: update 后旧 doc/chunk 停用, 新 doc/chunk 激活, 无残留。"""
    doc_id, chunks_v1 = await svc.process_document(
        content="商业银行信贷政策与风险管理要点",
        filename="credit.md",
        scenario="office",
    )
    assert len(chunks_v1) > 0
    row = await conn.fetchrow(
        "SELECT is_active FROM kb_documents WHERE id = $1", doc_id
    )
    assert row["is_active"] is True
    # v1 内容可检索
    hits = await svc._kb_repo.keyword_search("信贷", filters={"scenario": "office"})
    assert any("信贷" in c.text for c in hits)

    new_doc_id, chunks_v2 = await svc.update_document(
        doc_id,
        content="人工智能大模型在企业知识管理中的应用",
        filename="credit.md",
        scenario="office",
    )
    assert len(chunks_v2) > 0
    assert new_doc_id != doc_id  # 新版本 = 新 doc 行(旧行保留 inactive)

    # 旧 doc 与旧 chunks 停用
    old = await conn.fetchrow(
        "SELECT is_active FROM kb_documents WHERE id = $1", doc_id
    )
    assert old["is_active"] is False
    old_chunks = await conn.fetch(
        "SELECT is_active FROM kb_chunks WHERE doc_id = $1", doc_id
    )
    assert len(old_chunks) > 0
    assert all(r["is_active"] is False for r in old_chunks)

    # 新 doc 激活
    new = await conn.fetchrow(
        "SELECT is_active FROM kb_documents WHERE id = $1", new_doc_id
    )
    assert new["is_active"] is True

    # 无残留: active chunks 只属于新 doc
    active = await conn.fetch(
        "SELECT doc_id, COUNT(*) AS n FROM kb_chunks "
        "WHERE is_active = TRUE GROUP BY doc_id"
    )
    assert len(active) == 1, f"active chunks 应只属于新 doc, 实际 {active}"
    assert active[0]["doc_id"] == new_doc_id

    # 检索: 旧内容不再命中, 新内容命中
    hits_old = await svc._kb_repo.keyword_search(
        "信贷", filters={"scenario": "office"}
    )
    assert not any("信贷" in c.text for c in hits_old), (
        "旧 chunk 已停用, 不应再命中"
    )
    hits_new = await svc._kb_repo.keyword_search(
        "人工智能", filters={"scenario": "office"}
    )
    assert any("人工智能" in c.text for c in hits_new), (
        "新 chunk 应可检索命中"
    )


@pytest.mark.asyncio
async def test_update_document_same_content_idempotent(svc, conn):
    """幂等: 相同内容重复 update 不新增文档(unchanged 分支命中 active doc)。"""
    doc_id, _ = await svc.process_document(content="v1 内容", filename="doc.md")
    await svc.update_document(
        doc_id, content="v2 内容", filename="doc.md"
    )
    n_before = await conn.fetchval("SELECT COUNT(*) FROM kb_documents")

    d2, chunks = await svc.update_document(
        doc_id, content="v2 内容", filename="doc.md"
    )
    n_after = await conn.fetchval("SELECT COUNT(*) FROM kb_documents")

    assert n_after == n_before, "相同内容重复 update 不应新增文档"
    assert len(chunks) > 0, "unchanged 分支应返回现有 chunks"


@pytest.mark.asyncio
async def test_update_document_scenario_preserved(svc, conn):
    """update 保留 scenario(旧 doc 已停用后, 新 doc 继承原场景)。"""
    doc_id, _ = await svc.process_document(
        content="家庭资产配置", filename="family.md", scenario="data_analysis"
    )
    new_doc_id, _ = await svc.update_document(
        doc_id, content="家庭现金流管理", filename="family.md", scenario="data_analysis"
    )
    row = await conn.fetchrow(
        "SELECT scenario FROM kb_documents WHERE id = $1", new_doc_id
    )
    assert row["scenario"] == "data_analysis"


@pytest.mark.asyncio
async def test_embedding_metrics_land_in_system_metrics(conn):
    """V5(D4): factory 装配的 embedding metrics sink 落库 system_metrics。

    走真实装配路径(factory.build_kb_service + process_document), 验证
    kind='kb' 的 embed_* 指标出现(kind/name/value 结构, 供
    system_metrics_query 消费)。PA_EMBEDDING_MOCK=1 时 emit mock stats
    (embed_worker_ok=0), 落库链路本身不受 mock 影响。
    """
    from private_agent.knowledge import factory as kb_factory

    svc = kb_factory.build_kb_service(conn=conn, cfg={})
    await svc.process_document(
        content="监控链路验证文档", filename="metrics.md", scenario="office"
    )

    rows = await conn.fetch(
        "SELECT name, value, kind FROM system_metrics "
        "WHERE kind = 'kb' ORDER BY ts DESC"
    )
    names = {r["name"] for r in rows}
    # 核心验收: 维度/存储维/worker 状态必须出现
    assert "embed_dim" in names, f"system_metrics 缺 embed_dim, 实际 {names}"
    assert "embed_storage_dim" in names
    assert "embed_worker_ok" in names
    # 结构: value 数值、kind='kb'
    assert all(r["kind"] == "kb" for r in rows)
    assert all(isinstance(r["value"], float) for r in rows)
