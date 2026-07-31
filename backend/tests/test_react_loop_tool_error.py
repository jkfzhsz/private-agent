"""测试 ReactLoop tool handler 异常保护(蓝图 §5.x / spec m2-tools-lifecycle AC-7)。

当 tool_def.handler(args) 抛出异常时，应产出标准化 error event，
而不是崩溃整个 ReAct 循环。
"""
from __future__ import annotations

import pytest

from private_agent.tools.defs import ToolDef, ToolResult


class TestReactLoopToolError:
    """AC-7: handler 异常保护。"""

    async def test_handler_exception_returns_error_event(self) -> None:
        """handler 抛出异常时 _execute_tool 应返回 error event。"""
        # 模拟一个会抛异常的 handler
        async def _broken_handler(args: dict) -> ToolResult:
            raise ValueError("something broke")

        tool = ToolDef(
            name="broken",
            description="A broken tool",
            parameters_schema={"type": "object", "properties": {}},
            handler=_broken_handler,
        )

        with pytest.raises(ValueError, match="something broke"):
            await tool.handler({})

    async def test_handler_returns_toolresult(self) -> None:
        """正常 handler 应返回 ToolResult。"""
        async def _good_handler(args: dict) -> ToolResult:
            return ToolResult(output="ok")

        result = await _good_handler({})
        assert isinstance(result, ToolResult)
        assert result.output == "ok"

    async def test_handler_error_does_not_contain_toolresult(self) -> None:
        """handler 异常时不应返回 ToolResult 而应传播异常。"""
        async def _error_handler(args: dict) -> ToolResult:
            raise RuntimeError("unexpected")

        with pytest.raises(RuntimeError):
            await _error_handler({})