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
                "cpu_timeout_sec": 90,
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
    async def test_handler_no_config(self, monkeypatch) -> None:
        """无配置时返回错误。

        2026-08-15 修复: 全量顺序下 main 启动装配(main.py:1037
        set_sandbox_config(cfg.get("sandbox")))会设置模块级全局
        _sandbox_config → "无配置"分支不触发 → 误执行成功。此处显式
        重置全局为 None(monkeypatch 自动恢复), 测试自包含。
        """
        import private_agent.tools.builtins.code_execution as ce_mod

        monkeypatch.setattr(ce_mod, "_sandbox_config", None)
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
            "timeout": 90,  # 本机 python 冷启动 ~16s(杀软扫描), 需余量
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

class TestCodeExecutionNetwork:
    """0.5.1: network 参数 —— 显式联网放行(绕过沙箱代理隔离)。"""

    @pytest.mark.asyncio
    async def test_default_network_injects_dead_proxy(self, tmp_path: Path) -> None:
        """默认 network=false: 沙箱注入死代理(HTTP_PROXY=127.0.0.1:9)。"""
        config = _make_config(tmp_path)
        result = await code_execution_handler({
            "code": "import os; print('PROXY=' + str(os.environ.get('HTTP_PROXY')))",
            "session_id": "net-default",
            "_sandbox_config": config,
        })
        assert result.error is None
        assert "127.0.0.1:9" in result.output

    @pytest.mark.asyncio
    async def test_network_true_bypasses_proxy_isolation(self, tmp_path: Path) -> None:
        """network=true: 不注入死代理(代码可读真实环境/无代理)。"""
        config = _make_config(tmp_path)
        result = await code_execution_handler({
            "code": "import os; print('PROXY=' + str(os.environ.get('HTTP_PROXY')))",
            "session_id": "net-open",
            "_sandbox_config": config,
            "network": True,
        })
        assert result.error is None
        assert "PROXY=" in result.output
        assert "127.0.0.1:9" not in result.output

    @pytest.mark.asyncio
    async def test_utf8_mode_injected(self, tmp_path: Path) -> None:
        """0.5.1 GBK 治本: 沙箱内 PYTHONUTF8=1(用户脚本内部 subprocess 也 UTF-8)。"""
        config = _make_config(tmp_path)
        result = await code_execution_handler({
            "code": (
                "import os\n"
                "print('UTF8=' + str(os.environ.get('PYTHONUTF8')))\n"
                "print('IOENC=' + str(os.environ.get('PYTHONIOENCODING')))"
            ),
            "session_id": "utf8-test",
            "_sandbox_config": config,
        })
        assert result.error is None
        assert "UTF8=1" in result.output
        assert "IOENC=utf-8" in result.output
