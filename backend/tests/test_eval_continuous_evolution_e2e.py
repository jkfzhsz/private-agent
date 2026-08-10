"""M4 m4-continuous-evolution AC-9, AC-10 - 持续进化闭环端到端测试。

Source: spec/m4-continuous-evolution AC-9, AC-10
- AC-9: 评估运行 → 低分提取 → 审核决策(prompt_defect_edit)→ 入库 → 下次评估 load_test_set 含新样本
- AC-10: 评估运行 → 低分提取 → 审核决策(model_limitation_drop)→ 不入库 → 数据集不变
"""
from __future__ import annotations

import asyncio
import json
import os

import asyncpg

from private_agent.eval.models import (
    EvalSample,
    ExpectedToolCall,
    ExpectedTrace,
)
from private_agent.eval.repos import EvalDatasetRepo, EvalRunRepo, ReviewQueueRepo
from private_agent.eval.weak_sample import WeakSampleExtractor
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


async def _insert_eval_run_with_low_score(
    conn: "asyncpg.Connection",
    *,
    skill_name: str = "office",
    skill_version: str = "1.0.0",
    sample_id: str = "low_score_sample_001",
    completion_rate: float = 0.3,
) -> str:
    """插入一条 eval_runs 记录,sample_results 含一条低分样本。"""
    run_id = await conn.fetchval(
        """
        INSERT INTO eval_runs (skill_name, skill_version, model_id, dataset_version,
                               eval_mode, mock_enabled, metrics, sample_results, finished_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, now())
        RETURNING run_id::text
        """,
        skill_name,
        skill_version,
        "mock-glm",
        "v1",
        "offline",
        False,
        json.dumps({"task_completion": {"completion_rate": completion_rate}}),
        json.dumps([
            {
                "sample_id": sample_id,
                "actual_output": "wrong answer",
                "actual_events": [],
                "metrics": {
                    "task_completion": {"completion_rate": completion_rate}
                },
            }
        ]),
    )
    return run_id


def _make_edited_sample(sample_id: str = "weak_added_001") -> EvalSample:
    """构造人工编辑后的边界样本(case_type=boundary, split=test)。"""
    return EvalSample(
        sample_id=sample_id,
        scenario="office",
        skill_name="office",
        skill_version="1.0.0",
        case_type="boundary",
        difficulty="medium",
        split="test",
        input="edited input for boundary case",
        expected_react_trace=ExpectedTrace(
            tool_calls=[ExpectedToolCall(tool="calculator", args={"expr": "1+1"})],
            expected_output_contains=["2"],
        ),
        expected_output="2",
    )


# ── AC-9: prompt_defect_edit 闭环 ───────────────────────────────────────


def test_e2e_prompt_defect_edit_closed_loop(tmp_path):
    """AC-9: 评估运行 → 低分提取 → 审核(prompt_defect_edit)→ 入库 → load_test_set 含新样本。"""
    _setup_schema()
    queue_file = str(tmp_path / ".eval_review_queue.json")

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 1. 评估运行(直接插入低分 eval_runs)
            run_id = await _insert_eval_run_with_low_score(
                conn, sample_id="low_score_001", completion_rate=0.3,
            )

            # 2. 低分提取
            eval_repo = EvalRunRepo(conn)
            dataset_repo = EvalDatasetRepo(conn)
            review_queue_repo = ReviewQueueRepo(
                queue_file=queue_file, dataset_repo=dataset_repo,
            )
            extractor = WeakSampleExtractor(
                eval_repo=eval_repo,
                review_queue_repo=review_queue_repo,
            )
            extracted = await extractor.extract_from_low_score_runs(
                threshold=0.6, limit=50,
            )
            # 应该提取到 1 条低分样本
            assert len(extracted) == 1
            assert extracted[0]["sample_id"] == "low_score_001"

            # 审核队列应有 1 条 pending
            pending = await review_queue_repo.list_pending()
            assert len(pending) == 1

            # 数据集初始应为 0 条 test 样本
            initial_samples = await dataset_repo.load_test_set(
                scenario="office", skill_version="1.0.0",
            )

            # 3. 审核决策(prompt_defect_edit)
            item_id = pending[0]["id"]
            edited_sample = _make_edited_sample("weak_added_001")
            await review_queue_repo.update_status(
                item_id,
                status="approved",
                decision="prompt_defect_edit",
                edited_sample=edited_sample,
            )

            # 4. 入库后 load_test_set 应包含新样本
            after_samples = await dataset_repo.load_test_set(
                scenario="office", skill_version="1.0.0",
            )
            sample_ids = {s.sample_id for s in after_samples}
            return {
                "initial_count": len(initial_samples),
                "after_count": len(after_samples),
                "contains_new_sample": "weak_added_001" in sample_ids,
                "queue_status": (await review_queue_repo.list_all(status="approved")),
            }
        finally:
            await conn.close()

    out = asyncio.run(_run())
    # 闭环验证
    assert out["initial_count"] == 0
    assert out["after_count"] == 1
    assert out["contains_new_sample"] is True
    # 审核项状态变更为 approved
    assert len(out["queue_status"]) == 1
    assert out["queue_status"][0]["status"] == "approved"
    assert out["queue_status"][0]["decision"] == "prompt_defect_edit"


# ── AC-10: model_limitation_drop 闭环 ──────────────────────────────────


def test_e2e_model_limitation_drop_closed_loop(tmp_path):
    """AC-10: 评估运行 → 低分提取 → 审核(model_limitation_drop)→ 不入库 → 数据集不变。"""
    _setup_schema()
    queue_file = str(tmp_path / ".eval_review_queue.json")

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 1. 评估运行(低分样本)
            await _insert_eval_run_with_low_score(
                conn, sample_id="low_score_002", completion_rate=0.2,
            )

            # 2. 低分提取
            eval_repo = EvalRunRepo(conn)
            dataset_repo = EvalDatasetRepo(conn)
            review_queue_repo = ReviewQueueRepo(
                queue_file=queue_file, dataset_repo=dataset_repo,
            )
            extractor = WeakSampleExtractor(
                eval_repo=eval_repo,
                review_queue_repo=review_queue_repo,
            )
            extracted = await extractor.extract_from_low_score_runs(
                threshold=0.6, limit=50,
            )
            assert len(extracted) == 1

            # 数据集初始为空
            initial_samples = await dataset_repo.load_test_set(
                scenario="office", skill_version="1.0.0",
            )

            # 3. 审核决策(model_limitation_drop)
            pending = await review_queue_repo.list_pending()
            assert len(pending) == 1
            item_id = pending[0]["id"]
            await review_queue_repo.update_status(
                item_id,
                status="rejected",
                decision="model_limitation_drop",
            )

            # 4. 数据集应不变(仍为空)
            after_samples = await dataset_repo.load_test_set(
                scenario="office", skill_version="1.0.0",
            )
            return {
                "initial_count": len(initial_samples),
                "after_count": len(after_samples),
                "queue_status": (await review_queue_repo.list_all(status="rejected")),
            }
        finally:
            await conn.close()

    out = asyncio.run(_run())
    # 闭环验证:数据集不变
    assert out["initial_count"] == 0
    assert out["after_count"] == 0
    # 审核项状态变更为 rejected
    assert len(out["queue_status"]) == 1
    assert out["queue_status"][0]["status"] == "rejected"
    assert out["queue_status"][0]["decision"] == "model_limitation_drop"


# ── AC-9 多样本闭环:多次低分提取 + 多次决策 ──────────────────────────


def test_e2e_multiple_weak_samples_added_to_dataset(tmp_path):
    """AC-9 扩展:多次低分提取 + 多次 prompt_defect_edit 决策 → 数据集增长多条。"""
    _setup_schema()
    queue_file = str(tmp_path / ".eval_review_queue.json")

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 1. 插入 3 条低分评估运行
            for i in range(3):
                await _insert_eval_run_with_low_score(
                    conn,
                    sample_id=f"low_score_{i}",
                    completion_rate=0.1 + 0.1 * i,  # 0.1, 0.2, 0.3 都 < 0.6
                )

            eval_repo = EvalRunRepo(conn)
            dataset_repo = EvalDatasetRepo(conn)
            review_queue_repo = ReviewQueueRepo(
                queue_file=queue_file, dataset_repo=dataset_repo,
            )
            extractor = WeakSampleExtractor(
                eval_repo=eval_repo,
                review_queue_repo=review_queue_repo,
            )

            # 2. 低分提取(3 条)
            extracted = await extractor.extract_from_low_score_runs(threshold=0.6)
            assert len(extracted) == 3

            # 3. 全部决策为 prompt_defect_edit
            pending = await review_queue_repo.list_pending()
            assert len(pending) == 3
            for i, item in enumerate(pending):
                edited = _make_edited_sample(f"weak_added_{i}")
                await review_queue_repo.update_status(
                    item["id"],
                    status="approved",
                    decision="prompt_defect_edit",
                    edited_sample=edited,
                )

            # 4. 数据集应有 3 条新样本
            after = await dataset_repo.load_test_set(
                scenario="office", skill_version="1.0.0",
            )
            return {"after_count": len(after)}
        finally:
            await conn.close()

    out = asyncio.run(_run())
    assert out["after_count"] == 3
