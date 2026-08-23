"""测试 read_artifact 内置工具。"""
from __future__ import annotations

import os
import tempfile

import pytest

from private_agent.tools.builtins.read_artifact import read_artifact_handler


class TestReadArtifact:
    """read_artifact 工具:本地 artifact 读取。"""

    async def test_reads_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = os.path.join(tmpdir, ".claude", "artifacts")
            os.makedirs(artifact_dir, exist_ok=True)
            artifact_path = os.path.join(artifact_dir, "design.md")
            with open(artifact_path, "w", encoding="utf-8") as f:
                f.write("# Design Doc")
            result = await read_artifact_handler({"path": artifact_path, "workspace": tmpdir})
            assert result.error is None
            assert "# Design Doc" in result.output
            assert ".claude" in result.output
            assert "artifacts" in result.output

    async def test_outside_artifacts_dir_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outside = os.path.join(tmpdir, "secret.txt")
            with open(outside, "w", encoding="utf-8") as f:
                f.write("secret")
            result = await read_artifact_handler({"path": outside, "workspace": tmpdir})
            assert result.error is not None
            assert "outside" in result.error.lower() or "artifacts" in result.error.lower()

    async def test_nonexistent_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = await read_artifact_handler({"path": "/nonexistent/artifact.md", "workspace": tmpdir})
            assert result.error is not None