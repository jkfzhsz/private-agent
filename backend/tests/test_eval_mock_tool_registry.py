"""M4 m4-eval-runner-replay AC-4, AC-5 - MockToolRegistry 测试。

Source: spec/m4-eval-runner-replay AC-4, AC-5 + plan step 5, step 13
- get_mock_handler: 读 {mock_data_dir}/{tool_name}/{sample_id}.json 返回 mock handler
- list_tools_for_session: 工具列表与 real_registry 一致,handler 替换为 mock 版本
- set_sample_id: 显式切换当前样本(非 contextvars)
- mock_data 文件缺失: 返回 error="mock_data_not_found"
- 未 set_sample_id: 返回 error="sample_id_not_set"
"""
import asyncio
import json
from pathlib import Path

import pytest

from private_agent.eval.mock_tool_registry import MockToolRegistry
from private_agent.tools.defs import ECHO_TOOL, DATETIME_TOOL, ToolDef, ToolResult
from private_agent.tools.registry import ToolRegistry


def _make_real_registry() -> ToolRegistry:
    """构造含 echo + datetime 的 real_registry。"""
    reg = ToolRegistry()
    reg.register_builtin("echo", ECHO_TOOL)
    reg.register_builtin("datetime", DATETIME_TOOL)
    return reg


def _write_mock_file(
    mock_dir: Path,
    tool_name: str,
    sample_id: str,
    *,
    output: str,
    error: str | None = None,
    metadata: dict | None = None,
) -> Path:
    """写 mock JSON 文件:{mock_dir}/{tool_name}/{sample_id}.json。"""
    tool_dir = mock_dir / tool_name
    tool_dir.mkdir(parents=True, exist_ok=True)
    f = tool_dir / f"{sample_id}.json"
    f.write_text(
        json.dumps({
            "output": output,
            "error": error,
            "metadata": metadata or {},
        }),
        encoding="utf-8",
    )
    return f


# ──────────────────────────────────────────────────────────────────────────────
# AC-4: get_mock_handler
# ──────────────────────────────────────────────────────────────────────────────


def test_get_mock_handler_reads_json_and_returns_tool_result(tmp_path):
    """get_mock_handler 读 JSON 返回 handler,handler 调用返回 ToolResult(AC-4)。"""
    _write_mock_file(
        tmp_path, "echo", "office_001_normal",
        output="mocked-echo-output", error=None,
    )
    reg = _make_real_registry()
    mock_reg = MockToolRegistry(real_registry=reg, mock_data_dir=str(tmp_path))

    handler = mock_reg.get_mock_handler("office_001_normal", "echo")
    result = asyncio.run(handler({"text": "anything"}))

    assert isinstance(result, ToolResult)
    assert result.output == "mocked-echo-output"
    assert result.error is None


def test_get_mock_handler_returns_error_when_file_missing(tmp_path):
    """get_mock_handler 文件缺失时返回 error="mock_data_not_found"(AC-4, Edge cases)。"""
    reg = _make_real_registry()
    mock_reg = MockToolRegistry(real_registry=reg, mock_data_dir=str(tmp_path))

    handler = mock_reg.get_mock_handler("nonexistent_sample", "echo")
    result = asyncio.run(handler({"text": "x"}))

    assert isinstance(result, ToolResult)
    assert result.error == "mock_data_not_found"


def test_get_mock_handler_preserves_error_and_metadata(tmp_path):
    """get_mock_handler 保留 JSON 中的 error 和 metadata 字段(AC-4)。"""
    _write_mock_file(
        tmp_path, "echo", "office_002_error",
        output="", error="mocked-error", metadata={"latency_ms": 42},
    )
    reg = _make_real_registry()
    mock_reg = MockToolRegistry(real_registry=reg, mock_data_dir=str(tmp_path))

    handler = mock_reg.get_mock_handler("office_002_error", "echo")
    result = asyncio.run(handler({}))

    assert result.output == ""
    assert result.error == "mocked-error"
    assert result.metadata == {"latency_ms": 42}


# ──────────────────────────────────────────────────────────────────────────────
# AC-5: list_tools_for_session
# ──────────────────────────────────────────────────────────────────────────────


def test_list_tools_for_session_replaces_handlers_with_mock(tmp_path):
    """list_tools_for_session 工具名与 real_registry 一致,handler 替换为 mock 版本(AC-5)。"""
    _write_mock_file(
        tmp_path, "echo", "office_001_normal", output="mocked-echo",
    )
    _write_mock_file(
        tmp_path, "datetime", "office_001_normal", output="2026-08-01T00:00:00Z",
    )
    reg = _make_real_registry()
    mock_reg = MockToolRegistry(real_registry=reg, mock_data_dir=str(tmp_path))
    mock_reg.set_sample_id("office_001_normal")

    tools = mock_reg.list_tools_for_session(["echo", "datetime"])

    # 工具名一致(数量 + 名称)
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"echo", "datetime"}
    # handler 已替换为 mock 版本(调用返回 mock 数据,非真实 echo/datetime)
    echo_tool = next(t for t in tools if t.name == "echo")
    dt_tool = next(t for t in tools if t.name == "datetime")
    echo_result = asyncio.run(echo_tool.handler({"text": "real-input"}))
    dt_result = asyncio.run(dt_tool.handler({}))
    assert echo_result.output == "mocked-echo"
    assert dt_result.output == "2026-08-01T00:00:00Z"


def test_list_tools_for_session_whitelist_none_returns_all_with_mock_handlers(tmp_path):
    """whitelist=None 时返回全部工具,handler 仍替换为 mock(AC-5)。"""
    _write_mock_file(tmp_path, "echo", "s1", output="m1")
    _write_mock_file(tmp_path, "datetime", "s1", output="m2")
    reg = _make_real_registry()
    mock_reg = MockToolRegistry(real_registry=reg, mock_data_dir=str(tmp_path))
    mock_reg.set_sample_id("s1")

    tools = mock_reg.list_tools_for_session(None)

    assert len(tools) == 2
    for t in tools:
        r = asyncio.run(t.handler({}))
        assert r.output.startswith("m")


# ──────────────────────────────────────────────────────────────────────────────
# set_sample_id 行为
# ──────────────────────────────────────────────────────────────────────────────


def test_set_sample_id_switches_mock_data(tmp_path):
    """set_sample_id 切换后,list_tools_for_session 返回新样本的 mock 数据(AC-5)。"""
    _write_mock_file(tmp_path, "echo", "sample_a", output="output-a")
    _write_mock_file(tmp_path, "echo", "sample_b", output="output-b")
    reg = _make_real_registry()
    mock_reg = MockToolRegistry(real_registry=reg, mock_data_dir=str(tmp_path))

    # sample_a
    mock_reg.set_sample_id("sample_a")
    tools_a = mock_reg.list_tools_for_session(["echo"])
    res_a = asyncio.run(tools_a[0].handler({}))
    assert res_a.output == "output-a"

    # 切换到 sample_b
    mock_reg.set_sample_id("sample_b")
    tools_b = mock_reg.list_tools_for_session(["echo"])
    res_b = asyncio.run(tools_b[0].handler({}))
    assert res_b.output == "output-b"


def test_list_tools_without_set_sample_id_returns_error(tmp_path):
    """未 set_sample_id 时,mock handler 返回 error="sample_id_not_set"(spec Mitigation)。"""
    _write_mock_file(tmp_path, "echo", "sample_a", output="x")
    reg = _make_real_registry()
    mock_reg = MockToolRegistry(real_registry=reg, mock_data_dir=str(tmp_path))
    # 故意不调 set_sample_id

    tools = mock_reg.list_tools_for_session(["echo"])
    result = asyncio.run(tools[0].handler({}))
    assert result.error == "sample_id_not_set"
