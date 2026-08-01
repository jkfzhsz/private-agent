"""M4 m4-eval-runner-replay AC-12 - 端到端测试。

Source: spec/m4-eval-runner-replay AC-12 + plan step 17
- 离线评估端到端:load_test_set → run_evaluation(offline) → eval_runs 记录完整
- 交互式回放端到端:run_evaluation(replay, mock_enabled=True) → actual_events 含 tool_call/tool_result
- metrics 含五类指标(task_completion/tool_calls/efficiency/security/llm_judge)
- eval_runs 表记录完整(metrics + sample_results + finished_at)
"""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg

from private_agent.core.context_manager import ContextManager
from private_agent.eval.hybrid_eval import HybridEvaluator
from private_agent.eval.models import EvalSample, ExpectedTrace
from private_agent.eval.repos import EvalDatasetRepo, EvalRunRepo, VersionSnapshotRepo
from private_agent.eval.runner import EvalRunner
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.skills.loader import SkillLoader
from private_agent.storage import migrations
from private_agent.tools.defs import ToolDef, ToolResult
from private_agent.tools.registry import ToolRegistry

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

# office skill 路径(含 mock_data)
SKILLS_DIR = str(Path(__file__).resolve().parent.parent / "skills")
MOCK_DATA_DIR = str(Path(__file__).resolve().parent.parent / "skills" / "office" / "examples" / "test" / "mock_data")


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


def _load_office_sample(sample_id: str = "office_001_normal") -> EvalSample:
    """从 JSON 文件加载 office 种子样本。"""
    sample_file = Path(SKILLS_DIR) / "office" / "examples" / "test" / f"{sample_id}.json"
    data = json.loads(sample_file.read_text(encoding="utf-8"))
    return EvalSample(
        sample_id=data["sample_id"],
        scenario=data["scenario"],
        skill_name=data["skill_name"],
        skill_version=data["skill_version"],
        case_type=data["case_type"],
        difficulty=data["difficulty"],
        split=data["split"],
        input=data["input"],
        expected_react_trace=ExpectedTrace.model_validate(data["expected_react_trace"]),
        expected_output=data.get("expected_output"),
    )


def _make_mock_judge():
    """构造 mock LLMJudge(返回固定评分)。"""
    judge = AsyncMock()
    judge.judge = AsyncMock(return_value={"score": 0.85, "reasoning": "mock judge e2e"})
    return judge


def _make_placeholder_tool(name: str) -> ToolDef:
    """构造占位 ToolDef(handler 不会被调用,mock 模式下被替换)。"""
    async def _handler(args: dict) -> ToolResult:
        return ToolResult(output=f"placeholder:{name}")

    return ToolDef(
        name=name,
        description=f"Placeholder tool for {name}",
        parameters_schema={"type": "object", "properties": {}},
        handler=_handler,
    )


def _register_office_tools(reg: ToolRegistry) -> None:
    """注册 office skill 用到的全部工具。"""
    for name in ["file_read", "code_execution", "file_write", "web_search", "http_request"]:
        reg.register_builtin(name, _make_placeholder_tool(name))


def _make_tool_call(tool_name: str, args: dict) -> ChatResult:
    """构造含 tool_calls 的 ChatResult。"""
    return ChatResult(
        content="",
        used_provider="mock",
        tool_calls=[{
            "id": f"call_{tool_name}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        }],
    )


def _make_final(content: str) -> ChatResult:
    """构造无 tool_calls 的 final ChatResult。"""
    return ChatResult(content=content, used_provider="mock")


class _SequentialAdapter:
    """按预设序列返回 ChatResult(offline 模式用)。"""

    provider_name = "mock-seq"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses: list[ChatResult]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def chat(self, messages, tools=None) -> ChatResult:
        if self._idx >= len(self._responses):
            # 超出预设序列时返回 final(防止无限循环)
            return ChatResult(content="fallback final", used_provider="mock-seq")
        result = self._responses[self._idx]
        self._idx += 1
        return result


def _make_office_001_replay_adapter() -> _SequentialAdapter:
    """构造 office_001_normal 回放适配器(file_read → code_execution → file_write → final)。"""
    return _SequentialAdapter([
        _make_tool_call("file_read", {"path": "sales_q4.xlsx"}),
        _make_tool_call("code_execution", {"language": "python", "code": "import pandas as pd\ndf = pd.read_excel('sales_q4.xlsx')\nsummary = df.groupby('产品')['销售额'].sum().reset_index()\nsummary.to_excel('outputs/sales_q4_summary.xlsx', index=False)"}),
        _make_tool_call("file_write", {"path": "outputs/sales_q4_summary.xlsx"}),
        _make_final("已汇总各产品 Q4 总销售额,结果保存至 outputs/sales_q4_summary.xlsx。"),
    ])


# ──────────────────────────────────────────────────────────────────────────────
# AC-12: 离线评估端到端
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_offline_evaluation_eval_runs_record_complete():
    """AC-12: 离线评估端到端,eval_runs 记录完整(metrics + sample_results + finished_at)。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 加载 office_001_normal 样本
            sample = _load_office_sample("office_001_normal")
            repo = EvalDatasetRepo(conn)
            await repo.insert(sample)

            dataset_repo = EvalDatasetRepo(conn)
            eval_repo = EvalRunRepo(conn)
            snapshot_repo = VersionSnapshotRepo(conn)
            skill_loader = SkillLoader(dev_dir=SKILLS_DIR)
            hybrid = HybridEvaluator(judge=_make_mock_judge())

            runner = EvalRunner(
                dataset_repo=dataset_repo,
                eval_repo=eval_repo,
                snapshot_repo=snapshot_repo,
                skill_loader=skill_loader,
                model_adapter=_SequentialAdapter([_make_final("已汇总各产品 Q4 总销售额。")]),
                hybrid_evaluator=hybrid,
                cfg={"eval": {"regression_subset": 5}},
            )
            run_id = await runner.run_evaluation(
                skill_name="office",
                skill_version=sample.skill_version,
                model_id="mock-glm",
                eval_mode="offline",
                conn=conn,
            )
            run = await eval_repo.get_run(run_id)
            return run
        finally:
            await conn.close()

    run = asyncio.run(_run())
    # eval_runs 记录完整
    assert run["eval_mode"] == "offline"
    assert run["finished_at"] is not None
    assert run["metrics"] is not None
    assert run["sample_results"] is not None
    assert len(run["sample_results"]) == 1

    # 五类指标存在(metrics 汇总层)
    assert "sample_count" in run["metrics"]

    # 每条 sample_results 含五类指标
    sr = run["sample_results"][0]
    assert sr["actual_events"] == []
    metrics = sr["metrics"]
    assert "task_completion" in metrics
    assert "tool_calls" in metrics
    assert "efficiency" in metrics
    assert "security" in metrics
    assert "llm_judge" in metrics


# ──────────────────────────────────────────────────────────────────────────────
# AC-12: 交互式回放端到端(mock 模式)
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_replay_evaluation_mock_mode_with_office_mock_data():
    """AC-12: 交互式回放端到端,mock 模式 + office mock_data,actual_events 含 tool_call/tool_result。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sample = _load_office_sample("office_001_normal")
            repo = EvalDatasetRepo(conn)
            await repo.insert(sample)

            dataset_repo = EvalDatasetRepo(conn)
            eval_repo = EvalRunRepo(conn)
            snapshot_repo = VersionSnapshotRepo(conn)
            skill_loader = SkillLoader(dev_dir=SKILLS_DIR)
            hybrid = HybridEvaluator(judge=_make_mock_judge())

            reg = ToolRegistry()
            _register_office_tools(reg)

            runner = EvalRunner(
                dataset_repo=dataset_repo,
                eval_repo=eval_repo,
                snapshot_repo=snapshot_repo,
                skill_loader=skill_loader,
                model_adapter=_make_office_001_replay_adapter(),
                hybrid_evaluator=hybrid,
                cfg={"eval": {"regression_subset": 5}},
                context_manager_cls=ContextManager,
                tool_registry=reg,
                mock_data_dir=MOCK_DATA_DIR,
            )
            run_id = await runner.run_evaluation(
                skill_name="office",
                skill_version=sample.skill_version,
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
    # eval_runs 记录完整
    assert run["eval_mode"] == "replay"
    assert run["mock_enabled"] is True
    assert run["finished_at"] is not None
    assert run["sample_results"] is not None
    assert len(run["sample_results"]) == 1

    sr = run["sample_results"][0]
    # actual_events 含 tool_call + tool_result
    event_types = [e["event_type"] for e in sr["actual_events"]]
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    # mock 数据被正确读取(file_read 的 mock output 含 "日期,产品,销售额")
    file_read_results = [
        e for e in sr["actual_events"]
        if e["event_type"] == "tool_result" and e["payload"]["tool_name"] == "file_read"
    ]
    assert len(file_read_results) >= 1
    assert "日期" in file_read_results[0]["payload"]["output"]

    # 五类指标存在
    metrics = sr["metrics"]
    assert "task_completion" in metrics
    assert "tool_calls" in metrics
    assert "efficiency" in metrics
    assert "security" in metrics
    assert "llm_judge" in metrics
