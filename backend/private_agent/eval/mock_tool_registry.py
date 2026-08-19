"""M4 §8.10 MockToolRegistry - mock 模式工具替换(蓝图 §8.10,AC-4, AC-5)。

Source: spec/m4-eval-runner-replay AC-4, AC-5 + plan step 5
- 组合(持有 real_registry 引用),不继承 ToolRegistry,避免破坏现有继承链
- mock 数据匹配规则:sample_id + tool_name 二级索引
- mock JSON 格式: {"output": str, "error": str | None, "metadata": {}}
- set_sample_id 显式方法(非 contextvars,spec Mitigation)
- 文件缺失返回 error="mock_data_not_found"
- 未 set_sample_id 返回 error="sample_id_not_set"
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Awaitable, Callable

from private_agent.tools.defs import ToolDef, ToolResult
from private_agent.tools.registry import ToolRegistry

__all__ = ["MockToolRegistry"]


class MockToolRegistry:
    """mock 模式下替换真实工具 handler,返回预设结果(蓝图 §8.10)。

    组合 ToolRegistry:持有 real_registry 引用,list_tools_for_session 时
    将每个 ToolDef 的 handler 替换为 mock 版本(读 JSON 文件返回 ToolResult)。

    mock 数据目录结构:
        {mock_data_dir}/
        ├── file_read/
        │   └── {sample_id}.json
        ├── code_execution/
        │   └── {sample_id}.json
        └── web_search/
            └── {sample_id}.json

    mock JSON 格式: {"output": str, "error": str | None, "metadata": {}}
    """

    def __init__(
        self,
        real_registry: ToolRegistry,
        mock_data_dir: str,
    ) -> None:
        self._real_registry = real_registry
        self._mock_data_dir = Path(mock_data_dir)
        self._current_sample_id: str | None = None

    def set_sample_id(self, sample_id: str) -> None:
        """设置当前样本 ID(spec Mitigation:显式方法,非 contextvars)。

        ReplayExecutor 在每个 sample 循环开始时显式调用,
        MockToolRegistry 在未设置 sample_id 时返回 error="sample_id_not_set"。
        """
        self._current_sample_id = sample_id

    def get_mock_handler(
        self,
        sample_id: str,
        tool_name: str,
    ) -> Callable[[dict], Awaitable[ToolResult]]:
        """返回 mock handler:读取 {mock_data_dir}/{tool_name}/{sample_id}.json(AC-4)。

        mock JSON 格式: {"output": str, "error": str | None, "metadata": {}}
        文件缺失返回 error="mock_data_not_found"。

        Args:
            sample_id: 样本 ID(如 'office_001_normal')。
            tool_name: 工具名(如 'file_read')。

        Returns:
            async handler(args: dict) -> ToolResult。
        """
        mock_file = self._mock_data_dir / tool_name / f"{sample_id}.json"

        async def _handler(args: dict) -> ToolResult:
            if not mock_file.exists():
                return ToolResult(
                    output="",
                    error="mock_data_not_found",
                    metadata={"sample_id": sample_id, "tool_name": tool_name},
                )
            try:
                data = json.loads(mock_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                return ToolResult(
                    output="",
                    error=f"mock_data_invalid: {type(e).__name__}: {e}",
                    metadata={"sample_id": sample_id, "tool_name": tool_name},
                )
            return ToolResult(
                output=data.get("output", ""),
                error=data.get("error"),
                metadata=data.get("metadata", {}),
            )

        return _handler

    def list_tools_for_session(
        self, whitelist: list[str] | None
    ) -> list[ToolDef]:
        """代理 real_registry.list_tools_for_session,handler 替换为 mock 版本(AC-5)。

        Args:
            whitelist: 允许的工具名列表;None 时返回全部(保 ToolRegistry 行为)。

        Returns:
            ToolDef 列表(与 real_registry 一致,handler 替换为 mock)。
        """
        real_tools = self._real_registry.list_tools_for_session(whitelist)
        if self._current_sample_id is None:
            # 未设置 sample_id:返回 error="sample_id_not_set" 的占位 handler
            return [
                replace(tool_def, handler=self._make_not_set_handler(tool_def.name))
                for tool_def in real_tools
            ]
        sample_id = self._current_sample_id
        return [
            replace(
                tool_def,
                handler=self.get_mock_handler(sample_id, tool_def.name),
            )
            for tool_def in real_tools
        ]

    @staticmethod
    def _make_not_set_handler(
        tool_name: str,
    ) -> Callable[[dict], Awaitable[ToolResult]]:
        """未 set_sample_id 时返回 error="sample_id_not_set" 的占位 handler。"""

        async def _handler(args: dict) -> ToolResult:
            return ToolResult(
                output="",
                error="sample_id_not_set",
                metadata={"tool_name": tool_name},
            )

        return _handler
