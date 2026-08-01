"""M4 §8.11 + §8.12 + §8.16 api/eval.py - eval API 端点(蓝图 §8.11, §8.12, §8.16)。

Source: plan/m4-version-compare-rollback step 3
Source: spec/m4-continuous-evolution §E (AC-7, AC-8)
- POST /admin/eval/runs: 触发评估运行
- GET /admin/eval/runs: 评估运行列表
- GET /admin/eval/runs/{run_id}: 单次评估详情
- GET /admin/eval/datasets: 数据集列表
- GET /admin/eval/versions/compare: 版本对比
- POST /admin/eval/rollback: 触发回滚
- GET /admin/eval/review-queue: 审核队列列表(AC-7)
- POST /admin/eval/review-queue/{item_id}/decide: 审核决策(AC-8)
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from private_agent.config import loader
from private_agent.eval.models import EvalSample, InvalidSampleFormatError
from private_agent.eval.repos import (
    EvalDatasetRepo,
    EvalRunRepo,
    ReviewQueueRepo,
    VersionSnapshotRepo,
)
from private_agent.eval.rollback import SkillRollbackManager, VersionNotFoundError
from private_agent.eval.runner import EvalRunner
from private_agent.eval.version_compare import EvalComparator, InsufficientDataError
from private_agent.observability.logging import setup_logger
from private_agent.skills.loader import SkillLoader
from private_agent.storage import db

router = APIRouter(prefix="/admin/eval", tags=["eval"])
_logger = setup_logger("private_agent.api.eval")


class EvalRunRequest(BaseModel):
    """POST /admin/eval/runs 请求体。"""

    skill_name: str
    skill_version: str
    model_id: str
    eval_mode: str = "offline"
    mock_enabled: bool = False
    sample_subset: str | None = None


class RollbackRequest(BaseModel):
    """POST /admin/eval/rollback 请求体。"""

    skill_name: str
    target_version: str
    scope: str = "skill"  # "prompt" | "skill" | "harness"
    target_commit: str | None = None


class ReviewDecisionRequest(BaseModel):
    """POST /admin/eval/review-queue/{item_id}/decide 请求体(spec §E)。"""

    decision: str  # "model_limitation_drop" | "prompt_defect_edit"
    edited_sample: EvalSample | None = None


def _build_eval_runner(cfg, conn) -> EvalRunner:
    """构造 EvalRunner(测试可 monkeypatch)。"""
    return EvalRunner(
        dataset_repo=EvalDatasetRepo(conn),
        eval_repo=EvalRunRepo(conn),
        snapshot_repo=VersionSnapshotRepo(conn),
        skill_loader=SkillLoader.from_cfg(cfg),
        model_adapter=_build_default_adapter(cfg),
        hybrid_evaluator=_build_hybrid_evaluator(cfg),
        cfg=cfg,
    )


def _build_default_adapter(cfg):
    """构造默认模型适配器(蓝图 §8.11)。"""
    from private_agent.models.registry import build_default_adapter

    return build_default_adapter(cfg)


def _build_hybrid_evaluator(cfg):
    """构造 HybridEvaluator。"""
    from private_agent.eval.hybrid_eval import HybridEvaluator

    return HybridEvaluator.from_cfg(cfg)


def _build_review_queue_repo(cfg, conn) -> ReviewQueueRepo:
    """构造 ReviewQueueRepo(spec §B,测试可 monkeypatch)。

    queue_file 路径: {workspace_root}/.eval_review_queue.json(蓝图 §8.16)。
    """
    workspace_root = cfg.get("system", {}).get("workspace_root", ".")
    queue_file = os.path.join(workspace_root, ".eval_review_queue.json")
    return ReviewQueueRepo(
        queue_file=queue_file,
        dataset_repo=EvalDatasetRepo(conn),
    )


@router.post("/runs", response_model=None)
async def trigger_eval_run(request: EvalRunRequest):
    """AC-8: 触发评估运行,返回 run_id。

    MVP 同步执行(单人开发场景可接受);V2 改为后台任务。
    """
    try:
        cfg = loader.load_config()
        conn = await db.connect(cfg)
        try:
            runner = _build_eval_runner(cfg, conn)
            run_id = await runner.run_evaluation(
                skill_name=request.skill_name,
                skill_version=request.skill_version,
                model_id=request.model_id,
                eval_mode=request.eval_mode,
                mock_enabled=request.mock_enabled,
                sample_subset=request.sample_subset,
                conn=conn,
            )
            return {"run_id": run_id}
        finally:
            await conn.close()
    except Exception as e:
        _logger.exception("trigger_eval_run 失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/runs", response_model=None)
async def list_eval_runs(
    skill_name: str | None = None,
    skill_version: str | None = None,
    model_id: str | None = None,
    status: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    """评估运行列表。"""
    try:
        conn = await db.connect()
        try:
            repo = EvalRunRepo(conn)
            runs = await repo.list_runs(
                skill_version=skill_version,
                model_id=model_id,
                status=status,
                limit=limit,
            )
            return {"runs": runs}
        finally:
            await conn.close()
    except Exception as e:
        _logger.exception("list_eval_runs 失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/runs/{run_id}", response_model=None)
async def get_eval_run(run_id: str):
    """单次评估详情 + metrics + sample_results。"""
    try:
        conn = await db.connect()
        try:
            repo = EvalRunRepo(conn)
            run = await repo.get_run(run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="run_not_found")
            return run
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception as e:
        _logger.exception("get_eval_run 失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/datasets", response_model=None)
async def list_eval_datasets(scenario: str | None = None, limit: int = Query(50, ge=1, le=200)):
    """数据集列表。"""
    try:
        conn = await db.connect()
        try:
            if scenario:
                rows = await conn.fetch(
                    "SELECT sample_id, scenario, skill_name, skill_version, "
                    "case_type, difficulty, split, input, expected_output "
                    "FROM eval_datasets WHERE scenario=$1 LIMIT $2",
                    scenario,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    "SELECT sample_id, scenario, skill_name, skill_version, "
                    "case_type, difficulty, split, input, expected_output "
                    "FROM eval_datasets LIMIT $1",
                    limit,
                )
            return {"datasets": [dict(r) for r in rows]}
        finally:
            await conn.close()
    except Exception as e:
        _logger.exception("list_eval_datasets 失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/versions/compare", response_model=None)
async def compare_versions(
    skill_name: str,
    base_version: str,
    target_version: str,
    model_id: str | None = None,
):
    """AC-9: 版本对比,返回 diff。"""
    try:
        conn = await db.connect()
        try:
            repo = EvalRunRepo(conn)
            comparator = EvalComparator(repo)
            result = await comparator.compare_versions(
                skill_name=skill_name,
                base_version=base_version,
                target_version=target_version,
                model_id=model_id,
            )
            return result
        finally:
            await conn.close()
    except InsufficientDataError as e:
        return JSONResponse(status_code=404, content={"error": "insufficient_data", "detail": str(e)})
    except Exception as e:
        _logger.exception("compare_versions 失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/rollback", response_model=None)
async def trigger_rollback(request: RollbackRequest):
    """AC-10: 触发回滚,返回 rolled_back_to + affected_sessions。"""
    try:
        conn = await db.connect()
        try:
            repo = VersionSnapshotRepo(conn)
            manager = SkillRollbackManager(snapshot_repo=repo)
            if request.scope == "prompt":
                result = await manager.rollback_prompt(
                    skill_name=request.skill_name,
                    target_version=request.target_version,
                    conn=conn,
                )
            elif request.scope == "skill":
                result = await manager.rollback_skill(
                    skill_name=request.skill_name,
                    target_version=request.target_version,
                    conn=conn,
                )
            elif request.scope == "harness":
                if not request.target_commit:
                    raise HTTPException(
                        status_code=400, detail="harness_scope_requires_target_commit"
                    )
                result = manager.rollback_harness(target_commit=request.target_commit)
            else:
                raise HTTPException(status_code=400, detail="invalid_scope")
            return result
        finally:
            await conn.close()
    except VersionNotFoundError as e:
        return JSONResponse(status_code=404, content={"error": "version_not_found", "detail": str(e)})
    except HTTPException:
        raise
    except Exception as e:
        _logger.exception("trigger_rollback 失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── M4 §8.16 审核队列 API (spec m4-continuous-evolution §E) ──────────────


@router.get("/review-queue", response_model=None)
async def list_review_queue(
    status: str = "pending",
    limit: int = Query(20, ge=1, le=200),
):
    """AC-7: 列出审核队列(可按 status 过滤)。

    Args:
        status: pending/approved/rejected/edited;默认 pending。
        limit: 返回条数(1-200,默认 20)。
    """
    try:
        cfg = loader.load_config()
        conn = await db.connect(cfg)
        try:
            repo = _build_review_queue_repo(cfg, conn)
            if status == "all":
                items = await repo.list_all(limit=limit)
            elif status == "pending":
                items = await repo.list_pending(limit=limit)
            else:
                items = await repo.list_all(status=status, limit=limit)
            return {"items": items, "count": len(items)}
        finally:
            await conn.close()
    except Exception as e:
        _logger.exception("list_review_queue 失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/review-queue/{item_id}/decide", response_model=None)
async def decide_review_item(item_id: int, request: ReviewDecisionRequest):
    """AC-8: 处理审核决策(支持两类筛选标准)。

    - decision='prompt_defect_edit': edited_sample 入库(split=test, case_type=boundary)
    - decision='model_limitation_drop': 丢弃,不入库
    """
    try:
        cfg = loader.load_config()
        conn = await db.connect(cfg)
        try:
            repo = _build_review_queue_repo(cfg, conn)
            # 根据决策映射 status(prompt_defect_edit → approved, model_limitation_drop → rejected)
            if request.decision == "prompt_defect_edit":
                status = "approved"
                if request.edited_sample is None:
                    raise HTTPException(
                        status_code=400,
                        detail="prompt_defect_edit_requires_edited_sample",
                    )
            elif request.decision == "model_limitation_drop":
                status = "rejected"
            else:
                raise HTTPException(status_code=400, detail="invalid_decision")

            try:
                await repo.update_status(
                    item_id,
                    status=status,
                    decision=request.decision,
                    edited_sample=request.edited_sample,
                )
            except KeyError:
                raise HTTPException(status_code=404, detail="review_item_not_found")
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

            return {
                "item_id": item_id,
                "status": status,
                "decision": request.decision,
            }
        finally:
            await conn.close()
    except HTTPException:
        raise
    except InvalidSampleFormatError as e:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_sample_format", "detail": str(e)},
        )
    except Exception as e:
        _logger.exception("decide_review_item 失败")
        return JSONResponse(status_code=500, content={"error": str(e)})
