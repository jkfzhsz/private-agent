"""M4 m4-eval-runner-replay AC-8, AC-9 - ReplayExecutor 测试。

Source: spec/m4-eval-runner-replay AC-8, AC-9 + plan step 6, step 15
- run_replay: 创建临时评估会话(title="eval-" 前缀),执行 ReAct 循环,清理会话
- mock_enabled=True: 用 MockToolRegistry 替换 handler,actual_events 含 tool_call/tool_result
- mock_enabled=False: 真实执行 tool_def.handler(args)
- 会话清理: try/finally 确保 DELETE FROM sessions(异常时也清理)
"""
import asyncio
import json
import os
from pathlib import Path

import asyncpg
import pytest

from private_agent.core.context_manager import ContextManager
from private_agent.eval.models import EvalSample, ExpectedTrace
from private_agent.eval.replay import ReplayExecutor
from private_agent.models.base import ChatResult, ModelCapability
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

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs) -> ChatResult:
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock adapter exhausted: idx={self._idx}")
        result = self._responses[self._idx]
        self._idx += 1
        return result


def _make_tool_call_result(tool_name: str, args: dict) -> ChatResult:
    """构造含 tool_calls 的 ChatResult。"""
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
    """构造无 tool_calls 的 final ChatResult。"""
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
            tool_calls=[],
            expected_output_contains=["mocked"],
        ),
        expected_output="mocked output",
    )


def _make_skill():
    """构造 office skill(含 echo 工具白名单)。"""
    from private_agent.skills.models import Skill, SkillManifest, SkillDependencies, ToolDependency
    manifest = SkillManifest(
        name="office",
        version="0.1.0",
        description="office skill",
        scenario="office",
        dependencies=SkillDependencies(
            tools=[ToolDependency(name="echo", enabled=True)]
        ),
    )
    return Skill(manifest=manifest, system_prompt="You are office assistant.", tools_yaml=[])


def _write_mock_file(mock_dir: Path, tool_name: str, sample_id: str, output: str) -> Path:
    tool_dir = mock_dir / tool_name
    tool_dir.mkdir(parents=True, exist_ok=True)
    f = tool_dir / f"{sample_id}.json"
    f.write_text(json.dumps({"output": output, "error": None, "metadata": {}}), encoding="utf-8")
    return f


# ──────────────────────────────────────────────────────────────────────────────
# AC-9: 临时会话创建 + 清理
# ──────────────────────────────────────────────────────────────────────────────


def test_run_replay_creates_and_deletes_eval_session(tmp_path):
    """run_replay 创建临时评估会话,执行完毕后删除(AC-9)。"""
    _setup_schema()
    _write_mock_file(tmp_path, "echo", "office_001_normal", "mocked-echo-output")

    async def _run() -> tuple[str, list[dict], int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 预注册 echo 工具
            reg = ToolRegistry()
            reg.register_builtin("echo", ECHO_TOOL)

            adapter = _MockAdapter([
                _make_tool_call_result("echo", {"text": "hello"}),
                _make_final_result("final answer"),
            ])
            executor = ReplayExecutor(
                context_manager_cls=ContextManager,
                model_adapter=adapter,
                tool_registry=reg,
                mock_data_dir=str(tmp_path),
            )
            sample = _make_sample()
            skill = _make_skill()

            output, events = await executor.run_replay(
                sample=sample,
                skill=skill,
                model_id="mock-glm",
                mock_enabled=True,
                conn=conn,
            )

            # 检查 sessions 表中无 eval- 前缀残留
            residual = await conn.fetchval(
                "SELECT COUNT(*) FROM sessions WHERE title LIKE 'eval-%'"
            )
            return output, events, residual
        finally:
            await conn.close()

    output, events, residual = asyncio.run(_run())
    # 会话已清理
    assert residual == 0
    # 有事件产出
    assert len(events) > 0


def test_run_replay_session_title_has_eval_prefix(tmp_path):
    """run_replay 创建的临时会话 title 以 'eval-' 前缀(AC-9)。

    通过在 run_replay 执行期间并发查询 sessions 表验证 title 前缀。
    这里用 monkeypatch 拦截 _create_eval_session 验证传入的 title。
    """
    _setup_schema()
    _write_mock_file(tmp_path, "echo", "office_001_normal", "mocked")

    captured_titles: list[str] = []

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            reg = ToolRegistry()
            reg.register_builtin("echo", ECHO_TOOL)
            adapter = _MockAdapter([_make_final_result("done")])
            executor = ReplayExecutor(
                context_manager_cls=ContextManager,
                model_adapter=adapter,
                tool_registry=reg,
                mock_data_dir=str(tmp_path),
            )
            # monkeypatch _create_eval_session 捕获 title
            original_create = executor._create_eval_session

            async def _spy_create(c, *, title, model_id="mock-glm"):
                captured_titles.append(title)
                return await original_create(c, title=title, model_id=model_id)

            executor._create_eval_session = _spy_create

            await executor.run_replay(
                sample=_make_sample(),
                skill=_make_skill(),
                model_id="mock-glm",
                mock_enabled=False,
                conn=conn,
            )
        finally:
            await conn.close()

    asyncio.run(_run())
    assert len(captured_titles) == 1
    assert captured_titles[0].startswith("eval-")


# ──────────────────────────────────────────────────────────────────────────────
# AC-8: mock 模式 actual_events 含 tool_call/tool_result
# ──────────────────────────────────────────────────────────────────────────────


def test_run_replay_mock_mode_collects_tool_call_and_result(tmp_path):
    """run_replay mock_enabled=True 时 actual_events 含 tool_call + tool_result + final(AC-8)。"""
    _setup_schema()
    _write_mock_file(tmp_path, "echo", "office_001_normal", "mocked-echo-output")

    async def _run() -> tuple[str, list[dict]]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            reg = ToolRegistry()
            reg.register_builtin("echo", ECHO_TOOL)
            adapter = _MockAdapter([
                _make_tool_call_result("echo", {"text": "hello"}),
                _make_final_result("final based on mocked echo"),
            ])
            executor = ReplayExecutor(
                context_manager_cls=ContextManager,
                model_adapter=adapter,
                tool_registry=reg,
                mock_data_dir=str(tmp_path),
            )
            output, events = await executor.run_replay(
                sample=_make_sample(),
                skill=_make_skill(),
                model_id="mock-glm",
                mock_enabled=True,
                conn=conn,
            )
            return output, events
        finally:
            await conn.close()

    output, events = asyncio.run(_run())
    # 事件类型序列:thinking → tool_call → tool_result → final
    event_types = [e["event_type"] for e in events]
    assert "thinking" in event_types
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "final" in event_types
    # final 事件在最后
    assert event_types[-1] == "final"
    # tool_result 的 output 来自 mock 数据(非真实 echo)
    tool_result_events = [e for e in events if e["event_type"] == "tool_result"]
    assert len(tool_result_events) == 1
    assert tool_result_events[0]["payload"]["output"] == "mocked-echo-output"
    # final_output 从 final 事件提取
    assert output == "final based on mocked echo"


def test_run_replay_cleans_up_session_on_exception(tmp_path):
    """run_replay 在 ReactLoop 错误事件时仍清理临时会话(AC-9 Edge cases)。

    用 AllProvidersFailedError 触发 ReactLoop 的 error event 路径
    (ReactLoop 捕获该异常并产出 error event,run_replay 正常返回)。
    """
    _setup_schema()

    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            reg = ToolRegistry()
            reg.register_builtin("echo", ECHO_TOOL)
            # adapter 抛 AllProvidersFailedError 触发 ReactLoop error event
            from private_agent.models.base import AllProvidersFailedError

            class _ExplodingAdapter:
                provider_name = "boom"
                capability = ModelCapability(
                    streaming=False, function_calling=True, vision=False, json_mode=False
                )

                async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
                    raise AllProvidersFailedError("all providers failed: boom")

            executor = ReplayExecutor(
                context_manager_cls=ContextManager,
                model_adapter=_ExplodingAdapter(),
                tool_registry=reg,
                mock_data_dir=str(tmp_path),
            )
            # run_replay 不抛异常(ReactLoop 捕获 AllProvidersFailedError 并产出 error event)
            output, events = await executor.run_replay(
                sample=_make_sample(),
                skill=_make_skill(),
                model_id="mock-glm",
                mock_enabled=False,
                conn=conn,
            )
            residual = await conn.fetchval(
                "SELECT COUNT(*) FROM sessions WHERE title LIKE 'eval-%'"
            )
            return residual
        finally:
            await conn.close()

    residual = asyncio.run(_run())
    # ReactLoop 产出 error event,run_replay 正常返回,会话已清理
    assert residual == 0
