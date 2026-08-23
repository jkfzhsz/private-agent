"""M4 §8.9 SkillVersionListener - 版本变更自动触发评估(蓝图 §8.9,AC-10)。

Source: spec/m4-eval-runner-replay AC-10 + plan step 8
- 监听 Skill 版本保存事件,自动触发快速回归评估(offline + quick subset)
- auto_trigger_on_version_change=False 时不触发
- 失败仅记日志,不阻塞版本保存(蓝图 §8.9 Edge cases)
- 集成点由 m4-version-compare-rollback spec 确定(spec Open question)
"""
from __future__ import annotations

import asyncpg

from private_agent.eval.runner import EvalRunner
from private_agent.observability.logging import setup_logger

__all__ = ["SkillVersionListener"]


class SkillVersionListener:
    """Skill 版本变更监听器(蓝图 §8.9,AC-10)。

    Skill 保存新版本后自动触发快速回归评估:
    - eval_mode="offline"(快速回归用离线模式)
    - sample_subset="quick"(前 regression_subset 条)
    - model_id 用默认模型
    - 失败仅记日志,不阻塞版本保存

    Args:
        eval_runner: EvalRunner 实例(调用 run_evaluation)。
        cfg: 配置 dict(读 cfg["eval"]["auto_trigger_on_version_change"])。
    """

    def __init__(self, eval_runner: EvalRunner, cfg: dict) -> None:
        self._eval_runner = eval_runner
        self._cfg = cfg or {}
        self._logger = setup_logger("private_agent.eval.version_listener")

    async def on_skill_version_saved(
        self,
        skill_name: str,
        new_version: str,
        conn: asyncpg.Connection,
    ) -> str | None:
        """Skill 保存新版本后自动触发快速回归(蓝图 §8.9,AC-10)。

        仅当 cfg["eval"]["auto_trigger_on_version_change"]=True 时触发:
        - eval_mode="offline"(快速回归用离线模式,不执行工具)
        - sample_subset="quick"(前 regression_subset 条)
        - model_id="default"(用默认模型)
        - 失败仅记日志,不阻塞版本保存

        Args:
            skill_name: Skill 名。
            new_version: 新版本号。
            conn: asyncpg.Connection。

        Returns:
            run_id(str) 触发成功时返回;未触发/失败时返回 None。
        """
        auto_trigger = self._cfg.get("eval", {}).get(
            "auto_trigger_on_version_change", False
        )
        if not auto_trigger:
            self._logger.info(
                "auto_trigger_on_version_change=False,跳过快速回归(skill=%s, version=%s)",
                skill_name,
                new_version,
            )
            return None

        try:
            run_id = await self._eval_runner.run_evaluation(
                skill_name=skill_name,
                skill_version=new_version,
                model_id="default",
                eval_mode="offline",
                sample_subset="quick",
                conn=conn,
            )
            self._logger.info(
                "快速回归已触发: skill=%s, version=%s, run_id=%s",
                skill_name,
                new_version,
                run_id,
            )
            return run_id
        except Exception as e:
            # 失败仅记日志,不阻塞版本保存(蓝图 §8.9 Edge cases)
            self._logger.exception(
                "快速回归触发失败(不阻塞版本保存): skill=%s, version=%s, error=%s",
                skill_name,
                new_version,
                e,
            )
            return None
