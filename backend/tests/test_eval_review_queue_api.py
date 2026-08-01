"""M4 m4-continuous-evolution AC-7, AC-8 - 审核队列 API 端点测试。

Source: spec/m4-continuous-evolution AC-7, AC-8
- AC-7: GET /admin/eval/review-queue 返回审核队列列表(可按 status 过滤)
- AC-8: POST /admin/eval/review-queue/{item_id}/decide 处理审核决策,
        支持两类筛选标准(model_limitation_drop / prompt_defect_edit)
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock

import asyncpg
from fastapi.testclient import TestClient

from private_agent.eval.repos import ReviewQueueRepo
from private_agent.main import app
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


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


def _make_edited_sample_payload(sample_id: str = "weak_001") -> dict:
    """构造 POST /decide 请求体中的 edited_sample dict(符合 EvalSample schema)。"""
    return {
        "sample_id": sample_id,
        "scenario": "office",
        "skill_name": "office",
        "skill_version": "1.0.0",
        "case_type": "boundary",
        "difficulty": "medium",
        "split": "test",
        "input": "edited input",
        "expected_react_trace": {
            "tool_calls": [
                {"tool": "calculator", "args": {"expr": "1+1"}}
            ],
            "expected_output_contains": ["2"],
        },
        "expected_output": "2",
    }


# ── AC-7: GET /admin/eval/review-queue ──────────────────────────────────


def test_list_review_queue_returns_pending_items(monkeypatch, tmp_path):
    """AC-7: GET /admin/eval/review-queue 默认返回 status=pending 的项。"""
    _setup_schema()
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file)

    async def _seed():
        await repo.add({
            "source_run_id": "run-1",
            "sample_input": "case-a",
            "suggested_as": "boundary",
        })
        await repo.add({
            "source_run_id": "run-2",
            "sample_input": "case-b",
            "suggested_as": "boundary",
        })

    asyncio.run(_seed())

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    def _fake_build_repo(cfg, conn):
        return ReviewQueueRepo(queue_file=queue_file, dataset_repo=AsyncMock())

    monkeypatch.setattr("private_agent.api.eval.db.connect", _fake_connect)
    monkeypatch.setattr(
        "private_agent.api.eval._build_review_queue_repo", _fake_build_repo
    )

    client = TestClient(app)
    resp = client.get("/admin/eval/review-queue", params={"status": "pending"})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert len(data["items"]) == 2
    assert all(i["status"] == "pending" for i in data["items"])


def test_list_review_queue_filter_by_status(monkeypatch, tmp_path):
    """AC-7: GET /admin/eval/review-queue?status=rejected 只返回 rejected 项。"""
    _setup_schema()
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file, dataset_repo=AsyncMock())

    async def _seed():
        id1 = await repo.add({"sample_input": "a"})
        id2 = await repo.add({"sample_input": "b"})
        await repo.update_status(
            id1, status="rejected", decision="model_limitation_drop",
        )
        # id2 保持 pending

    asyncio.run(_seed())

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    def _fake_build_repo(cfg, conn):
        return ReviewQueueRepo(queue_file=queue_file, dataset_repo=AsyncMock())

    monkeypatch.setattr("private_agent.api.eval.db.connect", _fake_connect)
    monkeypatch.setattr(
        "private_agent.api.eval._build_review_queue_repo", _fake_build_repo
    )

    client = TestClient(app)
    resp_rejected = client.get(
        "/admin/eval/review-queue", params={"status": "rejected"}
    )
    assert resp_rejected.status_code == 200
    items = resp_rejected.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "rejected"

    resp_pending = client.get(
        "/admin/eval/review-queue", params={"status": "pending"}
    )
    assert len(resp_pending.json()["items"]) == 1
    assert resp_pending.json()["items"][0]["status"] == "pending"


def test_list_review_queue_respects_limit(monkeypatch, tmp_path):
    """AC-7: limit 参数限制返回条数。"""
    _setup_schema()
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file)

    async def _seed():
        for i in range(5):
            await repo.add({"sample_input": f"item-{i}"})

    asyncio.run(_seed())

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    def _fake_build_repo(cfg, conn):
        return ReviewQueueRepo(queue_file=queue_file, dataset_repo=AsyncMock())

    monkeypatch.setattr("private_agent.api.eval.db.connect", _fake_connect)
    monkeypatch.setattr(
        "private_agent.api.eval._build_review_queue_repo", _fake_build_repo
    )

    client = TestClient(app)
    resp = client.get("/admin/eval/review-queue", params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


# ── AC-8: POST /admin/eval/review-queue/{item_id}/decide ─────────────────


def test_decide_review_item_prompt_defect_edit_inserts_sample(monkeypatch, tmp_path):
    """AC-8: POST /decide with decision=prompt_defect_edit 将 edited_sample 入库。"""
    _setup_schema()
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file)

    item_id_holder = {}

    async def _seed():
        item_id = await repo.add({"sample_input": "low-score"})
        item_id_holder["id"] = item_id

    asyncio.run(_seed())

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    # 用真实 DB 仓储(dataset_repo 真实插入 eval_datasets)
    from private_agent.eval.repos import EvalDatasetRepo

    captured_inserts = []

    async def _capture_insert(sample):
        # 真实插入到 DB
        conn = await asyncpg.connect(TEST_DSN)
        try:
            r = await EvalDatasetRepo(conn).insert(sample)
            captured_inserts.append(sample.sample_id)
            return r
        finally:
            await conn.close()

    mock_dataset_repo = AsyncMock()
    mock_dataset_repo.insert = _capture_insert

    def _fake_build_repo(cfg, conn):
        return ReviewQueueRepo(queue_file=queue_file, dataset_repo=mock_dataset_repo)

    monkeypatch.setattr("private_agent.api.eval.db.connect", _fake_connect)
    monkeypatch.setattr(
        "private_agent.api.eval._build_review_queue_repo", _fake_build_repo
    )

    client = TestClient(app)
    resp = client.post(
        f"/admin/eval/review-queue/{item_id_holder['id']}/decide",
        json={
            "decision": "prompt_defect_edit",
            "edited_sample": _make_edited_sample_payload("weak_inserted_001"),
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["item_id"] == item_id_holder["id"]
    assert data["status"] == "approved"
    assert data["decision"] == "prompt_defect_edit"
    assert "weak_inserted_001" in captured_inserts


def test_decide_review_item_model_limitation_drop_does_not_insert(
    monkeypatch, tmp_path
):
    """AC-8: POST /decide with decision=model_limitation_drop 丢弃,不入库。"""
    _setup_schema()
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file)

    item_id_holder = {}

    async def _seed():
        item_id = await repo.add({"sample_input": "model-limit"})
        item_id_holder["id"] = item_id

    asyncio.run(_seed())

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    mock_dataset_repo = AsyncMock()
    mock_dataset_repo.insert = AsyncMock(return_value=1)

    def _fake_build_repo(cfg, conn):
        return ReviewQueueRepo(queue_file=queue_file, dataset_repo=mock_dataset_repo)

    monkeypatch.setattr("private_agent.api.eval.db.connect", _fake_connect)
    monkeypatch.setattr(
        "private_agent.api.eval._build_review_queue_repo", _fake_build_repo
    )

    client = TestClient(app)
    resp = client.post(
        f"/admin/eval/review-queue/{item_id_holder['id']}/decide",
        json={"decision": "model_limitation_drop"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert data["decision"] == "model_limitation_drop"
    mock_dataset_repo.insert.assert_not_awaited()


def test_decide_review_item_unknown_id_returns_404(monkeypatch, tmp_path):
    """AC-8: 不存在的 item_id 返回 404。"""
    _setup_schema()
    queue_file = str(tmp_path / ".eval_review_queue.json")

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    def _fake_build_repo(cfg, conn):
        return ReviewQueueRepo(queue_file=queue_file, dataset_repo=AsyncMock())

    monkeypatch.setattr("private_agent.api.eval.db.connect", _fake_connect)
    monkeypatch.setattr(
        "private_agent.api.eval._build_review_queue_repo", _fake_build_repo
    )

    client = TestClient(app)
    resp = client.post(
        "/admin/eval/review-queue/9999/decide",
        json={"decision": "model_limitation_drop"},
    )
    assert resp.status_code == 404


def test_decide_review_item_invalid_decision_returns_400(monkeypatch, tmp_path):
    """AC-8: 非法 decision 返回 400。"""
    _setup_schema()
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file)

    item_id_holder = {}

    async def _seed():
        item_id_holder["id"] = await repo.add({"sample_input": "x"})

    asyncio.run(_seed())

    async def _fake_connect(*args, **kwargs):
        return await asyncpg.connect(TEST_DSN)

    def _fake_build_repo(cfg, conn):
        return ReviewQueueRepo(queue_file=queue_file, dataset_repo=AsyncMock())

    monkeypatch.setattr("private_agent.api.eval.db.connect", _fake_connect)
    monkeypatch.setattr(
        "private_agent.api.eval._build_review_queue_repo", _fake_build_repo
    )

    client = TestClient(app)
    resp = client.post(
        f"/admin/eval/review-queue/{item_id_holder['id']}/decide",
        json={"decision": "unknown_decision"},
    )
    assert resp.status_code == 400
