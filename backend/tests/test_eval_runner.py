"""M4 m4-eval-runner-replay AC-7, AC-8 - EvalRunner 测试。

Source: spec/m4-eval-runner-replay AC-7, AC-8 + plan step 7, step 14
- AC-7: run_evaluation(eval_mode="offline") 离线批量,每样本仅调模型,actual_events=[]
- AC-8: run_evaluation(eval_mode="replay", mock_enabled=True) 交互式回放,actual_events 含 tool_call/tool_result
- sample_subset="quick" 取前 regression_subset 条
- 失败时 fail_run
"""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.eval.hybrid_eval import HybridEvaluator
from private_agent.eval.models import EvalSample, ExpectedTrace, ExpectedToolCall
from private_agent.eval.repos import EvalDatasetRepo, EvalRunRepo, VersionSnapshotRepo
from private_agent.eval.runner import EvalRunner
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.skills.loader import SkillLoader
from private_agent.storage import migrations
from private_agent.tools.defs import ECHO_TOOL
from private_agent.tools.registry import ToolRegistry

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


class _MockAdapter:
    """两步响应:第一次返回 tool_call,第二次返回 final。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses: list[ChatResult]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def chat(self, messages, tools=None) -> ChatResult:
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock adapter exhausted: idx={self._idx}")
        result = self._responses[self._idx]
        self._idx += 1
        return result


class _OfflineAdapter:
    """离线模式:直接返回 final,无 tool_calls。"""

    provider_name = "mock-offline"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    async def chat(self, messages, tools=None) -> ChatResult:
        return ChatResult(content="offline final answer", used_provider="mock-offline")


def _make_tool_call_result(tool_name: str, args: dict) -> ChatResult:
    return ChatResult(
        content="",
        used_provider="mock",
        tool_calls=[{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(args),
            },
        }],
    )


def _make_final_result(content: str) -> ChatResult:
    return ChatResult(content=content, used_provider="mock")


def _make_sample(sample_id: str = "office_001_normal") -> EvalSample:
    return EvalSample(
        sample_id=sample_id,
        scenario="office",
        skill_name="office",
        skill_version="0.1.0",
        case_type="normal",
        difficulty="easy",
        split="test",
        input="echo hello",
        expected_react_trace=ExpectedTrace(
            tool_calls=[ExpectedToolCall(tool="echo", args={"text": "hello"})],
            expected_output_contains=["mocked"],
        ),
        expected_output="mocked output",
    )


def _write_mock_file(mock_dir: Path, tool_name: str, sample_id: str, output: str) -> Path:
    tool_dir = mock_dir / tool_name
    tool_dir.mkdir(parents=True, exist_ok=True)
    f = tool_dir / f"{sample_id}.json"
    f.write_text(json.dumps({"output": output, "error": None, "metadata": {}}), encoding="utf-8")
    return f


def _make_mock_judge():
    """构造 mock LLMJudge(返回固定 1.0 分)。"""
    judge = AsyncMock()
    judge.judge = AsyncMock(return_value={"score": 1.0, "reasoning": "mock judge"})
    return judge


async def _insert_samples(conn: asyncpg.Connection, samples: list[EvalSample]) -> None:
    """批量插入 EvalSample 到 eval_datasets 表。"""
    repo = EvalDatasetRepo(conn)
    for s in samples:
        await repo.insert(s)


# ──────────────────────────────────────────────────────────────────────────────
# AC-7: 离线批量评估
# ──────────────────────────────────────────────────────────────────────────────


def test_run_evaluation_offline_mode_actual_events_empty(tmp_path):
    """AC-7: offline 模式每样本仅调模型,actual_events=[],返回 run_id。"""
    _setup_schema()

    async def _run() -> tuple[str, dict]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sample = _make_sample()
            await _insert_samples(conn, [sample])

            dataset_repo = EvalDatasetRepo(conn)
            eval_repo = EvalRunRepo(conn)
            snapshot_repo = VersionSnapshotRepo(conn)
            skill_loader = SkillLoader(dev_dir=str(tmp_path))
            hybrid = HybridEvaluator(judge=_make_mock_judge())

            runner = EvalRunner(
                dataset_repo=dataset_repo,
                eval_repo=eval_repo,
                snapshot_repo=snapshot_repo,
                skill_loader=skill_loader,
                model_adapter=_OfflineAdapter(),
                hybrid_evaluator=hybrid,
                cfg={"eval": {"regression_subset": 5}},
            )
            run_id = await runner.run_evaluation(
                skill_name="office",
                skill_version="0.1.0",
                model_id="mock-glm",
                eval_mode="offline",
                conn=conn,
            )
            run = await eval_repo.get_run(run_id)
            return run_id, run
        finally:
            await conn.close()

    run_id, run = asyncio.run(_run())
    assert run_id is not None
    assert run["eval_mode"] == "offline"
    assert run["mock_enabled"] is False
    assert run["finished_at"] is not None
    # sample_results 含每条样本的 metrics
    assert run["sample_results"] is not None
    assert len(run["sample_results"]) == 1
    sr = run["sample_results"][0]
    assert sr["actual_events"] == []
    assert sr["actual_output"] == "offline final answer"
    # 五类指标存在
    assert "metrics" in sr
    assert "task_completion" in sr["metrics"]
    assert "tool_calls" in sr["metrics"]
    assert "efficiency" in sr["metrics"]
    assert "security" in sr["metrics"]
    assert "llm_judge" in sr["metrics"]


def test_run_evaluation_offline_sample_subset_quick(tmp_path):
    """AC-7: sample_subset="quick" 取前 regression_subset 条。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 插入 8 条样本,regression_subset=5,quick 应只取 5 条
            samples = [
                _make_sample(f"office_00{i}_normal") for i in range(8)
            ]
            repo = EvalDatasetRepo(conn)
            for s in samples:
                await repo.insert(s)

            dataset_repo = EvalDatasetRepo(conn)
            eval_repo = EvalRunRepo(conn)
            snapshot_repo = VersionSnapshotRepo(conn)
            skill_loader = SkillLoader(dev_dir=str(tmp_path))
            hybrid = HybridEvaluator(judge=_make_mock_judge())

            runner = EvalRunner(
                dataset_repo=dataset_repo,
                eval_repo=eval_repo,
                snapshot_repo=snapshot_repo,
                skill_loader=skill_loader,
                model_adapter=_OfflineAdapter(),
                hybrid_evaluator=hybrid,
                cfg={"eval": {"regression_subset": 5}},
            )
            run_id = await runner.run_evaluation(
                skill_name="office",
                skill_version="0.1.0",
                model_id="mock-glm",
                eval_mode="offline",
                sample_subset="quick",
                conn=conn,
            )
            run = await eval_repo.get_run(run_id)
            return run
        finally:
            await conn.close()

    run = asyncio.run(_run())
    assert len(run["sample_results"]) == 5


# ──────────────────────────────────────────────────────────────────────────────
# AC-8: 交互式回放(mock 模式)
# ──────────────────────────────────────────────────────────────────────────────


def test_run_evaluation_replay_mock_mode_collects_tool_events(tmp_path):
    """AC-8: replay+mock_enabled=True,actual_events 含 tool_call/tool_result。"""
    _setup_schema()
    _write_mock_file(tmp_path, "echo", "office_001_normal", "mocked-echo-output")

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sample = _make_sample()
            repo = EvalDatasetRepo(conn)
            await repo.insert(sample)

            # 写 skill 文件到 tmp_path(供 SkillLoader 文件回退)
            skill_dir = tmp_path / "office"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "skill.yaml").write_text(
                "name: office\nversion: 0.1.0\nscenario: office\n"
                "dependencies:\n  tools:\n    - name: echo\n      enabled: true\n",
                encoding="utf-8",
            )
            (skill_dir / "system_prompt.md").write_text(
                "You are office assistant.", encoding="utf-8"
            )

            dataset_repo = EvalDatasetRepo(conn)
            eval_repo = EvalRunRepo(conn)
            snapshot_repo = VersionSnapshotRepo(conn)
            skill_loader = SkillLoader(dev_dir=str(tmp_path))
            hybrid = HybridEvaluator(judge=_make_mock_judge())

            reg = ToolRegistry()
            reg.register_builtin("echo", ECHO_TOOL)

            runner = EvalRunner(
                dataset_repo=dataset_repo,
                eval_repo=eval_repo,
                snapshot_repo=snapshot_repo,
                skill_loader=skill_loader,
                model_adapter=_MockAdapter([
                    _make_tool_call_result("echo", {"text": "hello"}),
                    _make_final_result("final based on mocked echo"),
                ]),
                hybrid_evaluator=hybrid,
                cfg={"eval": {"regression_subset": 5}},
                context_manager_cls=ContextManager,
                tool_registry=reg,
                mock_data_dir=str(tmp_path),
            )
            run_id = await runner.run_evaluation(
                skill_name="office",
                skill_version="0.1.0",
                model_id="mock-glm",
                eval_mode="replay",
                mock_enabled=True,
                conn=conn,
            )
            run = await eval_repo.get_run(run_id)
            return run
        finally:
            await conn.close()

    run = asyncio.run(_run())
    assert run["eval_mode"] == "replay"
    assert run["mock_enabled"] is True
    assert run["finished_at"] is not None
    assert len(run["sample_results"]) == 1
    sr = run["sample_results"][0]
    event_types = [e["event_type"] for e in sr["actual_events"]]
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    # tool_result output 来自 mock 数据
    tool_result_events = [e for e in sr["actual_events"] if e["event_type"] == "tool_result"]
    assert len(tool_result_events) == 1
    assert tool_result_events[0]["payload"]["output"] == "mocked-echo-output"


# ──────────────────────────────────────────────────────────────────────────────
# 失败路径
# ──────────────────────────────────────────────────────────────────────────────


def test_run_evaluation_marks_sample_failed_and_continues_run(tmp_path):
    """单样本异常时标记该样本 failed,run 正常完成(spec Edge cases: 继续 n 下一条)。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sample = _make_sample()
            repo = EvalDatasetRepo(conn)
            await repo.insert(sample)

            from private_agent.models.base import AllProvidersFailedError

            class _ExplodingAdapter:
                provider_name = "boom"
                capability = ModelCapability(
                    streaming=False, function_calling=True, vision=False, json_mode=False
                )

                async def chat(self, messages, tools=None):
                    raise AllProvidersFailedError("all providers failed: boom")

            dataset_repo = EvalDatasetRepo(conn)
            eval_repo = EvalRunRepo(conn)
            snapshot_repo = VersionSnapshotRepo(conn)
            skill_loader = SkillLoader(dev_dir=str(tmp_path))
            hybrid = HybridEvaluator(judge=_make_mock_judge())

            runner = EvalRunner(
                dataset_repo=dataset_repo,
                eval_repo=eval_repo,
                snapshot_repo=snapshot_repo,
                skill_loader=skill_loader,
                model_adapter=_ExplodingAdapter(),
                hybrid_evaluator=hybrid,
                cfg={"eval": {"regression_subset": 5}},
            )
            run_id = await runner.run_evaluation(
                skill_name="office",
                skill_version="0.1.0",
                model_id="mock-glm",
                eval_mode="offline",
                conn=conn,
            )
            run = await eval_repo.get_run(run_id)
            return run
        finally:
            await conn.close()

    run = asyncio.run(_run())
    # run 正常完成(单样本失败不阻塞整轮,spec Edge cases)
    assert run["finished_at"] is not None
    # sample_results 中该样本标记 metrics.error
    assert run["sample_results"] is not None
    assert len(run["sample_results"]) == 1
    sr = run["sample_results"][0]
    assert sr["actual_output"] == ""
    assert sr["actual_events"] == []
    assert "error" in sr["metrics"]
    assert "AllProvidersFailedError" in sr["metrics"]["error"]


def test_run_evaluation_replay_without_tool_registry_raises(tmp_path):
    """replay 模式未配置 tool_registry 时抛 ValueError。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sample = _make_sample()
            repo = EvalDatasetRepo(conn)
            await repo.insert(sample)

            dataset_repo = EvalDatasetRepo(conn)
            eval_repo = EvalRunRepo(conn)
            snapshot_repo = VersionSnapshotRepo(conn)
            skill_loader = SkillLoader(dev_dir=str(tmp_path))
            hybrid = HybridEvaluator(judge=_make_mock_judge())

            runner = EvalRunner(
                dataset_repo=dataset_repo,
                eval_repo=eval_repo,
                snapshot_repo=snapshot_repo,
                skill_loader=skill_loader,
                model_adapter=_OfflineAdapter(),
                hybrid_evaluator=hybrid,
                cfg={"eval": {"regression_subset": 5}},
            )
            with pytest.raises(ValueError, match="tool_registry"):
                await runner.run_evaluation(
                    skill_name="office",
                    skill_version="0.1.0",
                    model_id="mock-glm",
                    eval_mode="replay",
                    mock_enabled=False,
                    conn=conn,
                )
        finally:
            await conn.close()

    asyncio.run(_run())
