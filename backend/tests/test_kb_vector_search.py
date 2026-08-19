"""B6 P0-5 AC-1..6 - schema migration + vector_search 测试。

Source: plan/b6-rag-fullstack phase 1 (AC-1, AC-2, AC-4, AC-5, AC-6)
"""
import asyncio
import os

import asyncpg

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_raw_schema() -> None:
    from private_agent.storage import migrations

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await migrations.migrate_all(conn)
        finally:
            await conn.close()

    asyncio.run(_run())


def _vec(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


def test_kb_chunks_embedding_is_vector_type():
    """AC-1: kb_chunks.embedding 为 vector 类型。"""
    _setup_raw_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            row = await conn.fetchrow(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_name='kb_chunks' AND column_name='embedding'"
            )
            assert row is not None
            assert row["udt_name"] == "vector"
        finally:
            await conn.close()

    asyncio.run(_run())


def test_kb_chunks_can_insert_vector():
    """AC-1: 可 INSERT vector 值。"""
    _setup_raw_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            doc_id = await conn.fetchval(
                "INSERT INTO kb_documents (source,scenario,content,hash) "
                "VALUES ('s','test','hello','abc') RETURNING id"
            )
            await conn.execute(
                f"INSERT INTO kb_chunks (doc_id,chunk_text,embedding) "
                f"VALUES ($1,'test chunk','{_vec([0.1] * 1024)}'::vector)",
                doc_id,
            )
            row = await conn.fetchrow("SELECT id FROM kb_chunks LIMIT 1")
            assert row is not None
        finally:
            await conn.close()

    asyncio.run(_run())


def test_hnsw_index_exists():
    """AC-2: HNSW 索引存在。"""
    _setup_raw_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            row = await conn.fetchrow(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename='kb_chunks' AND indexdef LIKE '%hnsw%'"
            )
            assert row is not None
        finally:
            await conn.close()

    asyncio.run(_run())


def test_vector_search_returns_top_k():
    """AC-4: vector_search 返回 cosine similarity 排序的 top-k。"""
    _setup_raw_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            doc_id = await conn.fetchval(
                "INSERT INTO kb_documents (source,scenario,content,hash) "
                "VALUES ('s','test','hello','abc') RETURNING id"
            )
            await conn.execute(
                f"INSERT INTO kb_chunks (doc_id,chunk_text,embedding) VALUES "
                f"($1,'similar','{_vec([1.0]*512 + [0.0]*512)}'::vector)",
                doc_id,
            )
            await conn.execute(
                f"INSERT INTO kb_chunks (doc_id,chunk_text,embedding) VALUES "
                f"($1,'medium','{_vec([0.5]*512 + [0.5]*512)}'::vector)",
                doc_id,
            )
            await conn.execute(
                f"INSERT INTO kb_chunks (doc_id,chunk_text,embedding) VALUES "
                f"($1,'far','{_vec([0.0]*512 + [1.0]*512)}'::vector)",
                doc_id,
            )
            query = _vec([1.0]*512 + [0.0]*512)
            rows = await conn.fetch(
                f"SELECT chunk_text, 1 - (embedding <=> '{query}'::vector) AS similarity "
                f"FROM kb_chunks ORDER BY embedding <=> '{query}'::vector LIMIT 3",
            )
            assert len(rows) == 3
            sim_map = {r["chunk_text"]: r["similarity"] for r in rows}
            assert sim_map["similar"] > 0.8
            assert sim_map["far"] < 0.3
        finally:
            await conn.close()

    asyncio.run(_run())


def test_vector_search_filters_by_scenario():
    """AC-5: vector_search 支持 scenario 过滤。"""
    _setup_raw_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.fetchval(
                "INSERT INTO kb_documents (source,scenario,content,hash) "
                "VALUES ('s','alpha','hello','a1') RETURNING id"
            )
            await conn.fetchval(
                "INSERT INTO kb_documents (source,scenario,content,hash) "
                "VALUES ('s','beta','hello','b1') RETURNING id"
            )
            query = _vec([1.0] + [0.0] * 1023)
            await conn.execute(
                f"INSERT INTO kb_chunks (doc_id,scenario,chunk_text,embedding) VALUES "
                f"(1,'alpha','alpha_chunk','{query}'::vector)"
            )
            await conn.execute(
                f"INSERT INTO kb_chunks (doc_id,scenario,chunk_text,embedding) VALUES "
                f"(2,'beta','beta_chunk','{query}'::vector)"
            )
            rows = await conn.fetch(
                f"SELECT chunk_text FROM kb_chunks WHERE scenario='alpha' "
                f"ORDER BY embedding <=> '{query}'::vector LIMIT 5"
            )
            assert len(rows) == 1
            assert rows[0]["chunk_text"] == "alpha_chunk"
        finally:
            await conn.close()

    asyncio.run(_run())


def test_vector_search_empty_table():
    """AC-6: 空表返回空列表。"""
    _setup_raw_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            query = _vec([1.0] + [0.0] * 1023)
            rows = await conn.fetch(
                f"SELECT * FROM kb_chunks ORDER BY embedding <=> '{query}'::vector LIMIT 5"
            )
            assert len(rows) == 0
        finally:
            await conn.close()

    asyncio.run(_run())