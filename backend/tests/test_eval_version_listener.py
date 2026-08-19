"""M4 m4-eval-runner-replay AC-10 - SkillVersionListener 测试。

Source: spec/m4-eval-runner-replay AC-10 + plan step 8, step 16
- auto_trigger_on_version_change=True 触发快速回归(offline + quick subset)
- auto_trigger_on_version_change=False 不触发
- 触发失败仅记日志,不抛异常
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from private_agent.eval.version_listener import SkillVersionListener


def test_on_skill_version_saved_triggers_when_auto_trigger_true():
    """AC-10: auto_trigger_on_version_change=True 时触发快速回归。"""
    mock_runner = AsyncMock()
    mock_runner.run_evaluation = AsyncMock(return_value="run-123")
    mock_conn = MagicMock()

    listener = SkillVersionListener(
        eval_runner=mock_runner,
        cfg={"eval": {"auto_trigger_on_version_change": True, "regression_subset": 5}},
    )

    async def _run() -> str | None:
        return await listener.on_skill_version_saved(
            skill_name="office",
            new_version="0.2.0",
            conn=mock_conn,
        )

    run_id = asyncio.run(_run())
    assert run_id == "run-123"
    # 验证调用参数:offline + quick + default model
    mock_runner.run_evaluation.assert_called_once_with(
        skill_name="office",
        skill_version="0.2.0",
        model_id="default",
        eval_mode="offline",
        sample_subset="quick",
        conn=mock_conn,
    )


def test_on_skill_version_saved_does_not_trigger_when_auto_trigger_false():
    """AC-10: auto_trigger_on_version_change=False 时不触发。"""
    mock_runner = AsyncMock()
    mock_runner.run_evaluation = AsyncMock(return_value="run-123")

    listener = SkillVersionListener(
        eval_runner=mock_runner,
        cfg={"eval": {"auto_trigger_on_version_change": False}},
    )

    async def _run() -> str | None:
        return await listener.on_skill_version_saved(
            skill_name="office",
            new_version="0.2.0",
            conn=MagicMock(),
        )

    run_id = asyncio.run(_run())
    assert run_id is None
    mock_runner.run_evaluation.assert_not_called()


def test_on_skill_version_saved_failure_logs_and_returns_none():
    """AC-10: 触发失败仅记日志,不抛异常,返回 None。"""
    mock_runner = AsyncMock()
    mock_runner.run_evaluation = AsyncMock(
        side_effect=RuntimeError("eval failed: DB connection lost")
    )

    listener = SkillVersionListener(
        eval_runner=mock_runner,
        cfg={"eval": {"auto_trigger_on_version_change": True}},
    )

    async def _run() -> str | None:
        # 不应抛异常
        return await listener.on_skill_version_saved(
            skill_name="office",
            new_version="0.2.0",
            conn=MagicMock(),
        )

    run_id = asyncio.run(_run())
    assert run_id is None
    # run_evaluation 被调用过,但异常被吞
    mock_runner.run_evaluation.assert_called_once()
