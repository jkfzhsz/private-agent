"""经验管理 API 测试（Task 2.2）。

Source: plan/2026-08-11-dialogue-self-evolution-improvement Task 2.2
覆盖:
- GET /admin/lessons: 按 scope/lesson_type 列出经验(含 lesson_category)
- DELETE /admin/lessons/{id}: 软删除经验
- POST /admin/lessons/merge: 合并两条经验(内容写入 target, source 软删)
"""
import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)
_ADMIN_HEADERS = {"X-Admin-Token": "test-admin-token"}


def _setup_schema() -> None:
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
        finally:
            await conn.close()

    asyncio.run(_run())


@pytest.fixture(scope="module", autouse=True)
def _schema_fixture():
    _setup_schema()


async def _fake_connect(cfg=None):
    return await asyncpg.connect(TEST_DSN)


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    from private_agent.storage import db

    monkeypatch.setattr(db, "connect", _fake_connect)


def _client() -> TestClient:
    from private_agent.main import app

    return TestClient(app)


async def _add_lesson(conn, **kwargs) -> int:
    from private_agent.skills.evolution_repo import EvolutionRepo, SkillLesson

    repo = EvolutionRepo(conn)
    return await repo.add(SkillLesson(
        scope=kwargs.get("scope", "office"),
        task_summary=kwargs.get("task_summary", "test"),
        lesson_type=kwargs.get("lesson_type", "success"),
        lesson_content=kwargs.get("lesson_content", "content"),
        importance=kwargs.get("importance", 0.5),
    ))


def test_list_lessons_by_scope():
    """GET /admin/lessons?scope=: 只返回该场景经验且含 lesson_category。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _add_lesson(conn, scope="office", task_summary="office_lesson")
            await _add_lesson(conn, scope="data_analysis", task_summary="da_lesson")
        finally:
            await conn.close()

        client = _client()
        resp = client.get("/admin/lessons?scope=office", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(i["scope"] == "office" for i in items)
        assert any(i["task_summary"] == "office_lesson" for i in items)
        assert "lesson_category" in items[0]

        all_resp = client.get("/admin/lessons", headers=_ADMIN_HEADERS)
        assert all_resp.status_code == 200
        assert len(all_resp.json()["items"]) >= 2

    asyncio.run(_run())


def test_list_lessons_filters_by_type():
    """GET /admin/lessons&lesson_type=: 按经验类型过滤。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _add_lesson(conn, scope="office", lesson_type="success", task_summary="s1")
            await _add_lesson(conn, scope="office", lesson_type="failure", task_summary="f1")
        finally:
            await conn.close()

        client = _client()
        resp = client.get(
            "/admin/lessons?scope=office&lesson_type=failure",
            headers=_ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert all(i["lesson_type"] == "failure" for i in items)
        assert [i["task_summary"] for i in items] == ["f1"]

    asyncio.run(_run())


def test_discard_lesson():
    """DELETE /admin/lessons/{id}: 软删除后不再出现在列表中。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            lesson_id = await _add_lesson(conn, scope="office", task_summary="to_discard")
        finally:
            await conn.close()

        client = _client()
        resp = client.delete(f"/admin/lessons/{lesson_id}", headers=_ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        list_resp = client.get("/admin/lessons?scope=office", headers=_ADMIN_HEADERS)
        summaries = [i["task_summary"] for i in list_resp.json()["items"]]
        assert "to_discard" not in summaries

    asyncio.run(_run())


def test_merge_lessons():
    """POST /admin/lessons/merge: 合并内容写入 target, source 软删除。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            id1 = await _add_lesson(conn, scope="office", task_summary="src")
            id2 = await _add_lesson(conn, scope="office", task_summary="tgt")
        finally:
            await conn.close()

        client = _client()
        resp = client.post(
            "/admin/lessons/merge",
            headers=_ADMIN_HEADERS,
            json={
                "source_id": id1,
                "target_id": id2,
                "merged_content": "merged lesson",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        conn = await asyncpg.connect(TEST_DSN)
        try:
            from private_agent.skills.evolution_repo import EvolutionRepo

            repo = EvolutionRepo(conn)
            lessons = await repo.search_by_scope("office", limit=10)
        finally:
            await conn.close()
        active_ids = [l.id for l in lessons if l.is_active]
        assert id2 in active_ids
        assert id1 not in active_ids
        merged = [l for l in lessons if l.id == id2][0]
        assert "merged lesson" in merged.lesson_content

    asyncio.run(_run())
