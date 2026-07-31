"""测试 code_execution 内置工具(AC-11)。

验证 ToolDef 定义、注册、handler 调用。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from private_agent.tools.builtins.code_execution import (
    CODE_EXECUTION_TOOL,
    code_execution_handler,
    set_sandbox_config,
)
from private_agent.tools.defs import ToolDef, ToolResult
from private_agent.tools.registry import ToolRegistry


def _make_config(tmp_path: Path) -> dict:
    """构造最小 sandbox 配置 dict。"""
    return {
        "sandbox": {
            "workspace_root": str(tmp_path),
            "retention_days": 7,
            "languages": {
                "python": {"command": sys.executable, "script_extension": ".py"},
            },
            "limits": {
                "cpu_timeout_sec": 10,
                "memory_limit_mb": 512,
                "disk_limit_mb": 100,
            },
            "security": {
                "code_scan_enabled": True,
                "env_sanitization_enabled": True,
            },
            "output": {
                "stdout_artifact_threshold": 2000,
                "code_artifact_threshold": 4000,
            },
        }
    }


class TestCodeExecutionToolDef:
    """AC-11: ToolDef 定义正确。"""

    def test_tool_def_name(self) -> None:
        assert CODE_EXECUTION_TOOL.name == "code_execution"

    def test_tool_def_description(self) -> None:
        assert isinstance(CODE_EXECUTION_TOOL.description, str)
        assert len(CODE_EXECUTION_TOOL.description) > 0

    def test_tool_def_parameters_schema(self) -> None:
        schema = CODE_EXECUTION_TOOL.parameters_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "code" in props
        assert "timeout" in props
        assert "session_id" in props
        assert "code" in schema["required"]
        assert schema["properties"]["code"]["type"] == "string"
        assert schema["properties"]["timeout"]["type"] == "integer"

    def test_tool_def_is_tooldef_instance(self) -> None:
        assert isinstance(CODE_EXECUTION_TOOL, ToolDef)

    def test_tool_def_to_openai_schema(self) -> None:
        schema = CODE_EXECUTION_TOOL.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "code_execution"


class TestCodeExecutionHandler:
    """AC-11: handler 调用正确。"""

    @pytest.mark.asyncio
    async def test_handler_no_code(self) -> None:
        """无 code 参数时返回错误。"""
        result = await code_execution_handler({})
        assert isinstance(result, ToolResult)
        assert result.error is not None
        assert "No code provided" in result.error

    @pytest.mark.asyncio
    async def test_handler_no_config(self) -> None:
        """无配置时返回错误。"""
        result = await code_execution_handler({"code": "print('hello')"})
        assert isinstance(result, ToolResult)
        assert result.error is not None
        assert "not configured" in result.error or "Sandbox not configured" in result.error

    @pytest.mark.asyncio
    async def test_handler_execute_code(self, tmp_path: Path) -> None:
        """通过 _sandbox_config 参数执行代码。"""
        config = _make_config(tmp_path)
        result = await code_execution_handler({
            "code": "print('hello from tool')",
            "timeout": 10,
            "session_id": "tool-test",
            "_sandbox_config": config,
        })
        assert isinstance(result, ToolResult)
        assert result.error is None
        assert "hello from tool" in result.output
        assert "Exit code: 0" in result.output

    @pytest.mark.asyncio
    async def test_handler_with_timeout(self, tmp_path: Path) -> None:
        """timeout 参数传递正确。"""
        config = _make_config(tmp_path)
        result = await code_execution_handler({
            "code": "import time; time.sleep(0.1)",
            "timeout": 30,
            "session_id": "timeout-test",
            "_sandbox_config": config,
        })
        assert result.error is None
        assert result.output is not None

    @pytest.mark.asyncio
    async def test_handler_syntax_error(self, tmp_path: Path) -> None:
        """语法错误时返回 ToolResult(含 error)。"""
        config = _make_config(tmp_path)
        result = await code_execution_handler({
            "code": "print(hello",
            "session_id": "syntax-test",
            "_sandbox_config": config,
        })
        assert isinstance(result, ToolResult)
        # 语法错误 exit_code != 0, 输出含错误信息
        assert "Error" in result.output or "SyntaxError" in result.output


class TestCodeExecutionToolRegistry:
    """AC-11: 工具注册到 ToolRegistry。"""

    def test_register_to_registry(self) -> None:
        """注册后可通过 get_tool 查询。"""
        registry = ToolRegistry()
        registry.register_builtin("code_execution", CODE_EXECUTION_TOOL)
        retrieved = registry.get_tool("code_execution")
        assert retrieved is not None
        assert retrieved.name == "code_execution"
        assert retrieved.handler is not None

    def test_list_tools_includes_code_execution(self) -> None:
        """list_tools 包含 code_execution。"""
        registry = ToolRegistry()
        registry.register_builtin("code_execution", CODE_EXECUTION_TOOL)
        tools = registry.list_tools()
        names = [t.name for t in tools]
        assert "code_execution" in names