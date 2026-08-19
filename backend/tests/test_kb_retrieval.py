"""0.5.0 M2 KB 检索与 auto_retrieve 测试(设计文档 §8 场景专业强化验证)。

覆盖:
- keyword_search 分词 OR 匹配(多词查询"估值 PE PB"不再整串返回空)
- vector_search 全 0 向量降级(embedding Worker 未配置 → 返回空, 不污染 RRF)
- hybrid_search 串行执行(asyncpg 单连接无并发冲突)
- ContextManager._inject_auto_retrieve_kb 场景会话自动注入该场景 KB 片段
"""

import pytest
import asyncpg

TEST_DSN = "postgresql://postgres:123123@localhost:5432/private_agent_test"


@pytest.fixture
async def schema():
    """重建测试库 schema(幂等迁移)。"""
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


async def _seed_kb(conn: "asyncpg.Connection") -> None:
    """写入两条测试文档(白圭投资类 + 清和设计类)。"""
    from private_agent.knowledge.kb_service import KnowledgeBaseService

    svc = KnowledgeBaseService(kb_repo=__import__(
        "private_agent.knowledge.kb_repo", fromlist=["KnowledgeBaseRepo"]
    ).KnowledgeBaseRepo(conn))
    await svc.process_document(
        content=(
            "# 估值指标说明\n\n"
            "PE 市盈率:市值/净利润,用于成熟公司估值。\n"
            "PB 市净率:市值/净资产,用于周期股与金融股。\n"
            "股息率:每股分红/股价,价值型公司长期收益锚。\n" * 5
        ),
        filename="估值指标.md",
        scenario="data_analysis",
    )
    await svc.process_document(
        content=(
            "# FlowSpace 设计系统\n\n"
            "玻璃面板:rgba(255,255,255,0.55) + backdrop-filter blur(20px)。\n"
            "主色渐变:indigo #818cf8 → violet #c084fc → pink #f472b6。\n"
            "圆角:--radius-lg 24px / --radius-md 16px / --radius-sm 12px。\n" * 5
        ),
        filename="设计系统.md",
        scenario="frontend_design",
    )


# ── keyword 分词 OR ──────────────────────────────────────────────────────


async def test_keyword_search_tokenized_or(conn):
    """多词查询按空白分词 OR 匹配(0.5.0 M2: 原整串 ILIKE 过严返回空)。"""
    await _seed_kb(conn)
    repo = __import__(
        "private_agent.knowledge.kb_repo", fromlist=["KnowledgeBaseRepo"]
    ).KnowledgeBaseRepo(conn)
    # 单词(旧行为)
    one = await repo.keyword_search("估值", limit=5, filters={"scenario": "data_analysis"})
    assert len(one) > 0
    # 多词 OR(新行为): "PE" 或 "股息率" 任一命中
    multi = await repo.keyword_search("PE 股息率", limit=5, filters={"scenario": "data_analysis"})
    assert len(multi) > 0
    # 跨场景过滤仍生效
    other = await repo.keyword_search("估值", limit=5, filters={"scenario": "frontend_design"})
    assert len(other) == 0


# ── vector 全 0 降级 ─────────────────────────────────────────────────────


async def test_vector_search_zero_vector_fallback(conn):
    """全 0 查询向量(embedding Worker 未配置的 mock)→ 返回空, 不抛错。"""
    await _seed_kb(conn)
    repo = __import__(
        "private_agent.knowledge.kb_repo", fromlist=["KnowledgeBaseRepo"]
    ).KnowledgeBaseRepo(conn)
    zero_vec = [0.0] * 1024
    result = await repo.vector_search(
        zero_vec, limit=5, filters={"scenario": "data_analysis"}
    )
    assert result == []


async def test_hybrid_search_serial_no_conflict(conn):
    """hybrid_search 串行执行 vector + keyword(asyncpg 单连接无并发冲突)。"""
    await _seed_kb(conn)
    repo = __import__(
        "private_agent.knowledge.kb_repo", fromlist=["KnowledgeBaseRepo"]
    ).KnowledgeBaseRepo(conn)
    chunks = await repo.hybrid_search(
        "玻璃面板", [0.0] * 1024, limit=5, filters={"scenario": "frontend_design"}
    )
    # vector 降级空 + keyword 命中 → 融合后仍有结果
    assert len(chunks) > 0


# ── auto_retrieve 注入 ──────────────────────────────────────────────────


async def test_context_manager_auto_retrieve_injects_kb(conn):
    """kb_auto_retrieve=True 时, ensure_initial 自动注入该场景 KB 片段。"""
    await _seed_kb(conn)
    from private_agent.core.context_manager import ContextManager

    sid = await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ('auto-ret', 'mock') RETURNING id"
    )
    cm = ContextManager(
        session_id=sid,
        system_prompt="测试系统提示词",
        tools=[],
        scene="frontend_design",
        kb_auto_retrieve=True,
        kb_scenario="frontend_design",
    )
    await cm.ensure_initial(conn)
    # Stable Zone 应含 [KB Context] 自动检索片段
    stable_contents = " ".join(
        (m.get("content") or "") for m in cm.stable_zone.messages
    )
    assert "[KB Context]" in stable_contents
    assert "设计系统" in stable_contents or "FlowSpace" in stable_contents
    # DB 中 stable 消息落库(无记忆注入时 ≥1 = KB 片段)
    db_count = await conn.fetchval(
        "SELECT COUNT(*) FROM messages WHERE session_id=$1 AND zone='stable'",
        sid,
    )
    assert db_count >= 1


async def test_context_manager_no_auto_retrieve(conn):
    """kb_auto_retrieve=False 时不注入 KB 片段(默认行为不变)。"""
    await _seed_kb(conn)
    from private_agent.core.context_manager import ContextManager

    sid = await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ('no-auto', 'mock') RETURNING id"
    )
    cm = ContextManager(
        session_id=sid,
        system_prompt="测试",
        tools=[],
        scene="data_analysis",
        kb_auto_retrieve=False,
        kb_scenario=None,
    )
    await cm.ensure_initial(conn)
    stable_contents = " ".join(
        (m.get("content") or "") for m in cm.stable_zone.messages
    )
    assert "[KB Context]" not in stable_contents
