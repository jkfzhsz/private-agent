"""2026-08-15(M2 P2-15): KB 版本快照 / 对比 / 回滚 测试。

验证闭环: 保存快照(含 embedding_text) → 对比(diff) → 回滚(事务清空重建)。
依赖: 真实 DB(TEST_DSN) + PA_EMBEDDING_MOCK=1(conftest 强制, 全 0 向量)。
asyncio_mode=auto(pyproject), async 测试直接运行。
"""
from __future__ import annotations

import os

import asyncpg
import pytest

from private_agent.eval.repos import VersionSnapshotRepo
from private_agent.knowledge.factory import build_kb_service
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


@pytest.fixture
async def conn():
    c = await asyncpg.connect(TEST_DSN)
    try:
        await c.execute("DROP SCHEMA public CASCADE")
        await c.execute("CREATE SCHEMA public")
        await c.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await migrations.migrate_all(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
def svc(conn: "asyncpg.Connection"):
    # processor=None → factory 内默认 DocumentProcessor; embedding mock
    return build_kb_service(conn, {}, processor=None)


async def _upload(svc, source: str, content: str, scenario: str | None = None) -> int:
    doc_id, _ = await svc.process_document(
        content=content, filename=source, scenario=scenario
    )
    return doc_id


class TestKbSnapshot:
    async def test_save_snapshot_contains_documents_and_embeddings(
        self, conn, svc
    ):
        await _upload(svc, "doc-a.md", "知识库文档 A 内容", "office")
        await _upload(svc, "doc-b.md", "知识库文档 B 内容", "data_analysis")
        repo = VersionSnapshotRepo(conn)

        result = await svc.save_snapshot(repo, version="v1", note="test")
        assert result["documents"] == 2
        assert result["chunks"] >= 2

        snap = await repo.get(scope="kb", version="v1")
        assert snap is not None
        payload = snap  # VersionSnapshotRepo.get 返回 payload 本身
        assert payload["note"] == "test"
        assert len(payload["documents"]) == 2
        all_chunks = [c for d in payload["documents"] for c in d["chunks"]]
        assert all_chunks, "expect chunks in snapshot"
        assert all(
            isinstance(c["embedding_text"], str)
            and c["embedding_text"].startswith("[")
            for c in all_chunks
        ), "embedding_text must be pgvector text"

    async def test_compare_versions_detects_added_modified(self, conn, svc):
        repo = VersionSnapshotRepo(conn)
        await _upload(svc, "doc-a.md", "原始内容 A", "office")
        await svc.save_snapshot(repo, version="v1", note="base")

        # 新增 doc-b + 修改 doc-a
        await _upload(svc, "doc-b.md", "新增文档 B", "office")
        await _upload(svc, "doc-a.md", "修改后的内容 A", "office")
        await svc.save_snapshot(repo, version="v2", note="changed")

        diff = await svc.compare_versions(repo, "v1", "v2")
        assert "doc-b.md" in diff["added"], diff
        assert "doc-a.md" in diff["modified"], diff
        assert "doc-a.md" not in diff["removed"]
        assert "新增 1" in diff["summary"]

    async def test_rollback_restores_previous_content(self, conn, svc):
        repo = VersionSnapshotRepo(conn)
        await _upload(svc, "doc-a.md", "版本一内容", "office")
        await svc.save_snapshot(repo, version="v1", note="base")
        await _upload(svc, "doc-b.md", "版本二新增", "office")
        await svc.save_snapshot(repo, version="v2", note="v2")

        # 回滚到 v1 → 只剩 doc-a(事务内清空重建)
        async with conn.transaction():
            rollback = await svc.rollback_to(repo, "v1", conn=conn)
        assert rollback["documents"] == 1
        assert rollback["chunks"] >= 1

        docs = await svc._kb_repo.list_all_documents()
        sources = [d["source"] for d in docs]
        assert sources == ["doc-a.md"], sources

    async def test_rollback_missing_version_raises(self, conn, svc):
        repo = VersionSnapshotRepo(conn)
        with pytest.raises(ValueError, match="snapshot_not_found"):
            await svc.rollback_to(repo, "nope")
