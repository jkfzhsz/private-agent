"""calculator 内置工具:安全 eval 包装。

使用受限的表达式评估，仅允许基本的数学运算，
禁止访问危险内置函数和属性。
"""
from __future__ import annotations

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["calculator_handler", "CALCULATOR_TOOL"]

_SAFE_BUILTINS: dict[str, object] = {
    "abs": abs, "max": max, "min": min, "pow": pow,
    "round": round, "sum": sum, "int": int, "float": float,
    "str": str, "bool": bool, "len": len, "range": range,
    "True": True, "False": False, "None": None,
}


async def calculator_handler(args: dict) -> ToolResult:
    """执行数学表达式计算。

    Args:
        args: 包含 expression 键的 dict。

    Returns:
        计算结果或错误信息。
    """
    expression = args.get("expression", "")
    if not expression:
        return ToolResult(output="", error="No expression provided")

    try:
        # 检查是否包含危险语法
        _check_safe_expression(expression)
        result = eval(expression, {"__builtins__": {}}, _SAFE_BUILTINS)
        return ToolResult(output=str(result))
    except Exception as e:
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")


def _check_safe_expression(expression: str) -> None:
    """检查表达式是否包含危险模式。

    Raises:
        ValueError: 如果包含危险模式。
    """
    # 阻止访问 __ 开头的属性
    if "__" in expression:
        raise ValueError("Access to dunder attributes is not allowed")
    # 阻止 import
    if "import" in expression.lower().split():
        raise ValueError("Import statements are not allowed")


CALCULATOR_TOOL = ToolDef(
    name="calculator",
    description="Evaluate a mathematical expression safely. Supports basic arithmetic, numbers, and math functions.",
    parameters_schema={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The mathematical expression to evaluate.",
            }
        },
        "required": ["expression"],
    },
    handler=calculator_handler,
)