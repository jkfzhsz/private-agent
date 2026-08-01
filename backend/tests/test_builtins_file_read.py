"""测试 file_read 内置工具(蓝图 §5.8 + §7.9,spec AC-1/2/3)。"""
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


class TestFileReadMaxLines:
    """AC-1: max_lines 参数截断。"""

    async def test_max_lines_truncates_content(self) -> None:
        """max_lines=5 → 返回前 5 行 + 截断提示。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "many_lines.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                for i in range(20):
                    f.write(f"line {i}\n")
            result = await file_read_handler({
                "path": filepath, "data_dir": tmpdir, "max_lines": 5,
            })
            assert result.error is None
            assert "line 0" in result.output
            assert "line 4" in result.output
            assert "line 5" not in result.output
            assert "truncated" in result.output.lower()

    async def test_max_lines_default_1000(self) -> None:
        """不传 max_lines → 默认 1000 行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "small.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                for i in range(10):
                    f.write(f"line {i}\n")
            result = await file_read_handler({"path": filepath, "data_dir": tmpdir})
            assert result.error is None
            assert "line 9" in result.output
            assert "truncated" not in result.output.lower()


class TestFileReadSizeCheck:
    """AC-2: 文件大小 > max_file_size_mb 时拒绝读取(B2 P1-8: 提供 offset 时放行)。"""

    async def test_large_file_rejected_without_offset(self) -> None:
        """文件 > max_file_size_mb 且未提供 offset → 拒绝并提示用 offset/limit 分块。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "big.txt")
            # 写 2MB 文件,max_file_size_mb=1
            with open(filepath, "wb") as f:
                f.write(b"x" * (2 * 1024 * 1024))
            result = await file_read_handler({
                "path": filepath, "data_dir": tmpdir, "max_file_size_mb": 1,
            })
            assert result.error is not None
            assert "offset" in result.error

    async def test_large_file_allowed_when_offset_provided(self) -> None:
        """文件 > max_file_size_mb 但提供 offset → 允许分块读取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "big.txt")
            with open(filepath, "wb") as f:
                f.write(b"x" * (2 * 1024 * 1024))
            result = await file_read_handler({
                "path": filepath, "data_dir": tmpdir, "max_file_size_mb": 1, "offset": 0,
            })
            assert result.error is None

    async def test_small_file_allowed(self) -> None:
        """文件 < max_file_size_mb → 正常读取。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "small.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("small content")
            result = await file_read_handler({
                "path": filepath, "data_dir": tmpdir, "max_file_size_mb": 10,
            })
            assert result.error is None
            assert "small content" in result.output


class TestFileReadPagination:
    """B2 P1-8: offset/limit 分块读取 + metadata 分页信息。"""

    async def _make_lines_file(self, tmpdir: str, count: int = 2500) -> str:
        filepath = os.path.join(tmpdir, "many_lines.txt")
        with open(filepath, "w", encoding="utf-8") as f:
            for i in range(count):
                f.write(f"line {i}\n")
        return filepath

    async def test_read_with_offset_returns_partial_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = await self._make_lines_file(tmpdir)
            result = await file_read_handler({
                "path": filepath, "data_dir": tmpdir, "offset": 1000, "limit": 1000,
            })
            assert result.error is None
            assert "line 1000" in result.output
            assert "line 1999" in result.output
            assert "line 0" not in result.output

    async def test_read_with_limit_caps_at_max_lines_per_call(self) -> None:
        """limit 超过单次硬上限 1000 → 钳制为 1000。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = await self._make_lines_file(tmpdir)
            result = await file_read_handler({
                "path": filepath, "data_dir": tmpdir, "offset": 0, "limit": 5000,
            })
            assert result.error is None
            assert result.metadata["limit"] == 1000

    async def test_read_returns_has_more_true_when_not_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = await self._make_lines_file(tmpdir)
            result = await file_read_handler({
                "path": filepath, "data_dir": tmpdir, "offset": 0, "limit": 1000,
            })
            assert result.error is None
            assert result.metadata["has_more"] is True
            assert result.metadata["total_lines"] == 2500

    async def test_read_returns_next_offset_for_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = await self._make_lines_file(tmpdir)
            result = await file_read_handler({
                "path": filepath, "data_dir": tmpdir, "offset": 1000, "limit": 1000,
            })
            assert result.metadata["next_offset"] == 2000

    async def test_read_last_chunk_has_more_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = await self._make_lines_file(tmpdir)
            result = await file_read_handler({
                "path": filepath, "data_dir": tmpdir, "offset": 2000, "limit": 1000,
            })
            assert result.metadata["has_more"] is False
            assert result.metadata["next_offset"] is None

    async def test_offset_beyond_file_returns_empty(self) -> None:
        """offset 超出文件行数 → 空 content + has_more=false。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = await self._make_lines_file(tmpdir)
            result = await file_read_handler({
                "path": filepath, "data_dir": tmpdir, "offset": 9999, "limit": 1000,
            })
            assert result.error is None
            assert result.output == ""
            assert result.metadata["has_more"] is False

    async def test_full_iteration_reads_all_chunks(self) -> None:
        """循环调用 offset=0,1000,2000... 直到 has_more=False 应读完全部行。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = await self._make_lines_file(tmpdir, count=2500)
            collected: list[str] = []
            offset = 0
            for _ in range(10):
                result = await file_read_handler({
                    "path": filepath, "data_dir": tmpdir, "offset": offset, "limit": 1000,
                })
                assert result.error is None
                collected.extend(result.output.split("\n"))
                if not result.metadata["has_more"]:
                    break
                offset = result.metadata["next_offset"]
            assert result.metadata["has_more"] is False
            assert len(collected) == 2500
            assert collected[0] == "line 0"
            assert collected[-1] == "line 2499"


class TestFileReadArtifactTruncation:
    """AC-3: 读取结果 > 4000 token 时截断 + 写入 artifact。"""

    async def test_large_output_writes_artifact(self) -> None:
        """输出 > 4000 token → 截断 + 写 artifact + 返回截断内容 + artifact 路径。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "large_output.txt")
            # 写 > 16000 字符（> 4000 token by len//4）
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("A" * 20000)
            result = await file_read_handler({
                "path": filepath,
                "data_dir": tmpdir,
                "workspace": tmpdir,
            })
            assert result.error is None
            assert "truncated" in result.output.lower()
            assert "artifact" in result.output.lower()
            # artifact 文件应存在
            assert ".claude/artifacts" in result.output

    async def test_small_output_no_artifact(self) -> None:
        """输出 < 4000 token → 不截断,无 artifact。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, "small.txt")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("small content")
            result = await file_read_handler({
                "path": filepath,
                "data_dir": tmpdir,
                "workspace": tmpdir,
            })
            assert result.error is None
            assert "artifact" not in result.output.lower()