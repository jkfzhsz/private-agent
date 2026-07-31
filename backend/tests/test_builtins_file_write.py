"""测试 file_write 内置工具。"""
from __future__ import annotations

import os
import tempfile

import pytest

from private_agent.tools.builtins.file_write import file_write_handler


class TestFileWrite:
    """file_write 工具:限制 PA_DATA_DIR 内写入。"""

    async def test_writes_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "output.txt")
            result = await file_write_handler({"path": filepath, "content": "test data", "data_dir": tmpdir})
            assert result.error is None
            assert os.path.exists(filepath)
            with open(filepath, encoding="utf-8") as f:
                assert f.read() == "test data"

    async def test_path_traversal_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await file_write_handler({"path": "../../outside.txt", "content": "x", "data_dir": tmpdir})
            assert result.error is not None
            assert "traversal" in result.error.lower() or "outside" in result.error.lower()

    async def test_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "sub", "nested", "file.txt")
            result = await file_write_handler({"path": nested, "content": "nested", "data_dir": tmpdir})
            assert result.error is None
            assert os.path.exists(nested)