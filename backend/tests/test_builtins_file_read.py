"""测试 file_read 内置工具。"""
from __future__ import annotations

import os
import tempfile

import pytest

from private_agent.tools.builtins.file_read import file_read_handler


class TestFileRead:
    """file_read 工具:限制 PA_DATA_DIR 内读取。"""

    async def test_reads_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "test.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("hello world")
            result = await file_read_handler({"path": filepath, "data_dir": tmpdir})
            assert result.error is None
            assert "hello world" in result.output

    async def test_path_traversal_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await file_read_handler({"path": "../../etc/passwd", "data_dir": tmpdir})
            assert result.error is not None
            assert "traversal" in result.error.lower() or "outside" in result.error.lower()

    async def test_nonexistent_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await file_read_handler({"path": "/nonexistent/file.txt", "data_dir": tmpdir})
            assert result.error is not None