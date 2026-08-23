"""2026-08-16(蒋先生反馈: 回答中 .png 损坏): files 端点沙箱产物回退。

覆盖:
- 回退扫描 .sandbox/{session}/outputs/ 命中(真实配置路径)
- 回退扫描 .sandbox-artifacts/{session}/ 命中
- 均未命中 → None
- 多会话同名文件 → 取最新 mtime
"""
import os
import time
from pathlib import Path

from private_agent.api.files import _search_sandbox_outputs


def _mk(tmp_path: Path) -> None:
    """把 PA_USER_DATA 指向 tmp, 让 _search_sandbox_outputs 的真实配置解析命中。"""
    os.environ["PA_USER_DATA"] = str(tmp_path)


def test_search_sandbox_session_outputs(tmp_path, monkeypatch):
    """.sandbox/{session}/outputs/ 命中(真实配置解析)。"""
    monkeypatch.setenv("PA_USER_DATA", str(tmp_path))
    sand = tmp_path / ".sandbox" / "s42" / "outputs"
    sand.mkdir(parents=True)
    (sand / "chart.png").write_bytes(b"png-data")

    result = _search_sandbox_outputs("chart.png")
    assert result is not None
    assert result.read_bytes() == b"png-data"


def test_search_sandbox_artifacts(tmp_path, monkeypatch):
    """.sandbox-artifacts/{session}/ 命中(阶段1-C 同步产物)。"""
    monkeypatch.setenv("PA_USER_DATA", str(tmp_path))
    art = tmp_path / ".sandbox-artifacts" / "s9"
    art.mkdir(parents=True)
    (art / "plot.png").write_bytes(b"plot-data")

    result = _search_sandbox_outputs("plot.png")
    assert result is not None
    assert result.read_bytes() == b"plot-data"


def test_search_miss_returns_none(tmp_path, monkeypatch):
    """均未命中 → None。"""
    monkeypatch.setenv("PA_USER_DATA", str(tmp_path))
    assert _search_sandbox_outputs("missing.png") is None


def test_search_prefers_newest_mtime(tmp_path, monkeypatch):
    """多会话同名文件 → 取最新 mtime。"""
    monkeypatch.setenv("PA_USER_DATA", str(tmp_path))
    old = tmp_path / ".sandbox" / "s1" / "outputs"
    old.mkdir(parents=True)
    old_f = old / "chart.png"
    old_f.write_bytes(b"old")
    os.utime(old_f, (time.time() - 100, time.time() - 100))

    new = tmp_path / ".sandbox" / "s2" / "outputs"
    new.mkdir(parents=True)
    new_f = new / "chart.png"
    new_f.write_bytes(b"new")
    os.utime(new_f, (time.time(), time.time()))

    result = _search_sandbox_outputs("chart.png")
    assert result is not None
    assert result.read_bytes() == b"new"
