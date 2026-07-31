"""测试 calculator 内置工具。"""
from __future__ import annotations

import pytest

from private_agent.tools.builtins.calculator import calculator_handler


class TestCalculator:
    """calculator 工具:安全 eval 包装。"""

    async def test_add(self) -> None:
        result = await calculator_handler({"expression": "1 + 2"})
        assert result.output == "3"
        assert result.error is None

    async def test_mul(self) -> None:
        result = await calculator_handler({"expression": "3 * 4"})
        assert result.output == "12"
        assert result.error is None

    async def test_float(self) -> None:
        result = await calculator_handler({"expression": "3.5 * 2"})
        assert result.output == "7.0"
        assert result.error is None

    async def test_syntax_error(self) -> None:
        result = await calculator_handler({"expression": "1 +"})
        assert result.error is not None
        assert "SyntaxError" in result.error

    async def test_unsafe_builtin_blocked(self) -> None:
        """禁止使用 __import__ 等危险内置函数。"""
        result = await calculator_handler({"expression": "__import__('os').system('ls')"})
        assert result.error is not None

    async def test_unsafe_attribute_blocked(self) -> None:
        """禁止访问 __ 开头的属性。"""
        result = await calculator_handler({"expression": "(1).__class__"})
        assert result.error is not None