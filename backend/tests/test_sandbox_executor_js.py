"""B2 P1-7 - sandbox/executor.py JavaScript 支持。

Source: plan/b2-remaining-features step 9-12 (修复计划 §2 P1-7)
- _build_command("javascript") 返回 node 命令
- 真实执行 JS 脚本,stdout 返回结果
"""
from __future__ import annotations

import asyncio
import shutil
from unittest.mock import patch

import pytest

from private_agent.sandbox.executor import SandboxExecutor

NODE_AVAILABLE = shutil.which("node") is not None


class TestSandboxExecutorJs:
    """P1-7: JS 语言支持。"""

    def test_build_command_javascript_returns_node(self, tmp_path) -> None:
        """_build_command("javascript") 应返回 node + 脚本路径。"""
        executor = SandboxExecutor()
        script = str(tmp_path / "script.js")
        with patch.object(executor, "_find_node_cmd", return_value="node"):
            cmd = executor._build_command("javascript", script)
        assert cmd == ["node", script]

    def test_build_command_javascript_uses_node_command(self, tmp_path) -> None:
        """node_command 参数应传入命令构造。"""
        executor = SandboxExecutor(node_command="node18")
        script = str(tmp_path / "script.js")
        with patch.object(executor, "_find_node_cmd", return_value="node18"):
            cmd = executor._build_command("javascript", script)
        assert cmd == ["node18", script]

    def test_find_node_cmd_raises_when_missing(self) -> None:
        """node 不在 PATH 时应抛 ValueError 且报错清晰。"""
        executor = SandboxExecutor()
        with patch("shutil.which", return_value=None):
            with pytest.raises(ValueError, match="node"):
                executor._find_node_cmd()

    def test_write_script_uses_js_extension(self, tmp_path) -> None:
        """JS 脚本应写入 .js 扩展名文件。"""
        executor = SandboxExecutor()
        script = asyncio.run(executor._write_script("console.log(1)", "javascript", str(tmp_path)))
        assert script.endswith(".js")
        assert (tmp_path / "scripts").exists()

    @pytest.mark.skipif(not NODE_AVAILABLE, reason="node not installed")
    def test_execute_javascript_end_to_end(self, tmp_path) -> None:
        """真实执行 JS console.log('hello'),stdout 应包含 hello。"""
        executor = SandboxExecutor()
        result = asyncio.run(
            executor.execute(
                code="console.log('hello')",
                language="javascript",
                timeout=10,
                workspace=str(tmp_path),
            )
        )
        assert result.exit_code == 0
        assert "hello" in result.stdout
