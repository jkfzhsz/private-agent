"""阶段4(agent-upgrader 设计文档 §2.2 能力域⑥): eval_runner —— 评测运行。

无涯(monitor)自我扩展工具: 运行 PA 评测集、对比基线, 验证技能/工具改动
是否退化(阶段4 验收: 自我安装一个技能并验证)。

功能:
- eval_scenes: 列出评测集场景与任务数(只读) —— safe
- eval_run: 运行指定场景评测(mock 模式, 快速验证) —— safe(只读评测)
- eval_report: 最近评估运行 + 低分样本 + 失败模式报告(阶段5 进化沉淀入口) —— safe

安全边界:
- 评测运行复用 EvalRunner(只读: 创建 eval_run 记录 + 逐样本评估)
- mock_enabled=True 走 mock 工具(不消耗真实 LLM token)
- 全量真实评测耗时长, 建议 quick 子集
"""
from __future__ import annotations

import logging
import os

from private_agent.tools.defs import ToolDef, ToolResult

logger = logging.getLogger(__name__)

__all__ = [
    "EVAL_SCENES_TOOL",
    "EVAL_RUN_TOOL",
    "EVAL_REPORT_TOOL",
    "EVAL_MANAGER_TOOLS",
]


async def _eval_scenes_handler(args: dict) -> ToolResult:
    """列出评测集场景与任务数(只读)。"""
    try:
        from private_agent.eval import scenes_loader

        scenes = scenes_loader.load_all_scenes()
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"评测集加载失败: {type(e).__name__}: {e}")

    lines: list[str] = []
    for key, scene in scenes.items():
        tasks = scene.get("tasks", [])
        meta = scene.get("meta", {})
        lines.append(
            f"- {key} ({meta.get('scene_name', '?')}): "
            f"{len(tasks)} 个任务, skill={meta.get('skill_name', '?')}"
        )
    return ToolResult(
        output="PA 评测集场景:\n" + ("\n".join(lines) if lines else "(无评测集)")
    )


async def _eval_run_handler(args: dict) -> ToolResult:
    """运行评测(mock 模式快速验证)。safe 只读。

    Args:
        scene: 场景名(office/data_analysis/frontend_design/monitor, 必填)。
        subset: 'quick'(默认, 取回归子集) 或 'all'。
        mock: 是否 mock 模式(默认 True, 不消耗真实 token)。
    """
    scene = str(args.get("scene") or "").strip()
    if not scene:
        return ToolResult(output="", error="scene required(office/data_analysis/frontend_design/monitor)")
    subset = str(args.get("subset") or "quick").strip()
    if subset not in ("quick", "all"):
        return ToolResult(output="", error="subset 可选 quick/all")
    mock = bool(args.get("mock", True))

    try:
        from private_agent.eval import scenes_loader

        scenes = scenes_loader.load_all_scenes()
        if scene not in scenes:
            return ToolResult(output="", error=f"场景 {scene} 不存在(可用 eval_scenes 查看)")
        meta = scenes[scene].get("meta", {})
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"评测集加载失败: {type(e).__name__}: {e}")

    try:
        from private_agent.config import loader
        from private_agent.eval.hybrid_eval import HybridEvaluator
        from private_agent.eval.repos import EvalDatasetRepo, EvalRunRepo, VersionSnapshotRepo
        from private_agent.eval.runner import EvalRunner
        from private_agent.models.registry import build_default_adapter
        from private_agent.skills.loader import SkillLoader
        from private_agent.storage import db

        cfg = loader.load_config()
        conn = await db.connect(cfg)
        try:
            # 2026-08-16 修复(第二轮): yaml 已去预置化(models.providers 空),
            # provider 动态注册在 config_runtime 表 —— load_config() 不含
            # providers → build_default_adapter 抛 KeyError('providers')。
            # 合并 config_runtime(runtime > yaml)后再构造 runner。
            cfg = await loader.load_config_with_overrides(conn)
            # 2026-08-16 修复: EvalRunner 构造器要求 6 个 keyword-only 依赖,
            # 原无参调用 EvalRunner() 抛 missing 6 required args(阶段4 遗留 Bug,
            # 无涯首次调用暴露)。按 api/eval.py::_build_eval_runner 模式注入。
            model_adapter = build_default_adapter(cfg)
            if model_adapter is None:
                return ToolResult(
                    output="",
                    error="评测运行失败: fallback_chain 无可用 provider, 无法构造模型适配器",
                )
            runner = EvalRunner(
                dataset_repo=EvalDatasetRepo(conn),
                eval_repo=EvalRunRepo(conn),
                snapshot_repo=VersionSnapshotRepo(conn),
                skill_loader=SkillLoader.from_cfg(cfg),
                model_adapter=model_adapter,
                hybrid_evaluator=HybridEvaluator.from_cfg(cfg),
                cfg=cfg,
            )
            run_id = await runner.run_evaluation(
                skill_name=meta.get("skill_name", scene),
                skill_version="1.0.0",
                model_id="mock-glm" if mock else "deepseek-v4-flash",
                eval_mode="offline",
                mock_enabled=mock,
                sample_subset=subset if subset == "quick" else None,
                conn=conn,
            )
        finally:
            await conn.close()
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            output="", error=f"评测运行失败: {type(e).__name__}: {e}"
        )

    return ToolResult(
        output=(
            f"评测已启动(scene={scene}, subset={subset}, mock={mock}): "
            f"run_id={run_id}\n"
            f"可用 admin 端查询评测结果/对比基线。"
        )
    )


EVAL_SCENES_TOOL = ToolDef(
    name="eval_scenes",
    description=(
        "列出 PA 评测集场景(office/data_analysis/frontend_design/monitor)"
        "与各场景任务数。只读, 自动执行。"
    ),
    parameters_schema={"type": "object", "properties": {}},
    handler=_eval_scenes_handler,
    is_kernel=False,
    safety_level="safe",
    risk_level="low",
)

EVAL_RUN_TOOL = ToolDef(
    name="eval_run",
    description=(
        "运行 PA 评测集(mock 模式快速验证技能/工具改动无退化)。"
        "scene 必填(office/data_analysis/frontend_design/monitor), "
        "subset 可选 quick(默认)/all, mock 默认 True(不消耗真实 token)。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "scene": {"type": "string", "description": "场景名(必填)"},
            "subset": {"type": "string", "description": "quick(默认)/all"},
            "mock": {"type": "boolean", "description": "mock 模式(默认 True)"},
        },
        "required": ["scene"],
    },
    handler=_eval_run_handler,
    is_kernel=False,
    safety_level="safe",
    risk_level="low",
)


# ──────────────────────────────────────────────────────────────────────────────
# 阶段5(2026-08-16): eval_report —— 评估报告(低分样本 + 失败模式)
# 进化沉淀闭环入口: eval_report 出低分 → lessons_search 查历史经验 → 根因分析
# → optim_plan 提案 → apply_optim 执行 → pytest_run 回归 → git_commit
# ──────────────────────────────────────────────────────────────────────────────

async def _eval_report_handler(args: dict) -> ToolResult:
    """评估报告: 最近运行 + 低分样本 + 待审核失败案例(只读)。"""
    try:
        limit = int(args.get("limit") or 3)
        threshold = float(args.get("threshold") or 0.6)
    except (TypeError, ValueError):
        limit, threshold = 3, 0.6
    limit = max(1, min(10, limit))
    threshold = max(0.0, min(1.0, threshold))

    conn = None
    try:
        from private_agent.config import loader
        from private_agent.eval.repos import EvalRunRepo, ReviewQueueRepo
        from private_agent.storage import db

        cfg = loader.load_config()
        conn = await db.connect(cfg)
        eval_repo = EvalRunRepo(conn)

        lines: list[str] = []
        # 1. 最近评估运行
        runs = await eval_repo.list_runs(status="completed", limit=limit)
        if runs:
            lines.append(f"最近 {len(runs)} 次评估运行:\n")
            for r in runs:
                started = (r.get("started_at") or "").isoformat() if hasattr(r.get("started_at"), "isoformat") else str(r.get("started_at") or "?")
                mock = "mock" if r.get("mock_enabled") else "real"
                metrics = r.get("metrics") or {}
                err = metrics.get("error")
                lines.append(
                    f"- {r.get('skill_name')} v{r.get('skill_version')} [{mock}]"
                    f" {started[:19]}"
                    f"{'  ERROR: ' + str(err)[:80] if err else ''}"
                )
        else:
            lines.append("暂无已完成评估运行(可用 eval_run 先跑一次 mock 评测)。")

        # 2. 低分样本(completion_rate < threshold)
        low = await eval_repo.get_low_score_samples(threshold=threshold, limit=20)
        if low:
            lines.append(f"\n低分样本(< {threshold:.0%} 完成率, 共 {len(low)} 条):")
            for s in low[:10]:
                lines.append(
                    f"- {s['sample_id']}: {float(s['completion_rate']):.0%}"
                )
            if len(low) > 10:
                lines.append(f"... 还有 {len(low) - 10} 条")
            lines.append(
                "\n失败模式分析 → 修复闭环: 对低分样本做根因归类"
                "(提示缺陷/工具缺陷/模型限制), 用 lessons_search 查历史经验,"
                "然后 optim_plan 提案修复 → apply_optim 执行 → pytest_run 回归"
                "→ git_commit 提交。"
            )
        else:
            lines.append(f"\n无低于 {threshold:.0%} 完成率的低分样本(状态健康)。")

        # 3. 待审核失败案例
        workspace_root = cfg.get("system", {}).get("workspace_root", ".")
        queue_repo = ReviewQueueRepo(
            queue_file=os.path.join(workspace_root, ".eval_review_queue.json")
        )
        pending = await queue_repo.list_pending(limit=10)
        if pending:
            lines.append(f"\n待审核失败案例: {len(pending)} 条")
            for item in pending[:5]:
                lines.append(
                    f"- [{item.get('scope', '?')}] {str(item.get('failure_reason', ''))[:70]}"
                )
        else:
            lines.append("\n待审核队列为空。")

        return ToolResult(output="\n".join(lines))
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            output="", error=f"eval_report failed: {type(e).__name__}: {e}"
        )
    finally:
        if conn is not None:
            await conn.close()


EVAL_REPORT_TOOL = ToolDef(
    name="eval_report",
    description=(
        "评估报告: 最近评估运行汇总 + 低分样本(完成率低于阈值) + 待审核失败案例。"
        "用于识别系统性弱点, 驱动修复闭环: 低分 → 根因分析 → optim_plan 提案"
        "→ apply_optim 执行 → pytest_run 回归 → git_commit。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "最近运行条数, 默认 3, 最大 10"},
            "threshold": {"type": "number", "description": "低分阈值, 默认 0.6"},
        },
    },
    handler=_eval_report_handler,
    is_kernel=False,
    safety_level="readonly",
    risk_level="low",
)

EVAL_MANAGER_TOOLS: list[ToolDef] = [EVAL_SCENES_TOOL, EVAL_RUN_TOOL, EVAL_REPORT_TOOL]
