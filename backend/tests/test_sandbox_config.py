"""测试 sandbox 配置段加载和校验(AC-13)。

验证 config.yaml 的 sandbox 配置段被正确解析,load_config 通过校验,
非法配置抛 ValueError。
"""
from __future__ import annotations

import textwrap

import pytest

from private_agent.config import loader


def test_config_sandbox_section_exists() -> None:
    """AC-13: load_config 返回的 dict 包含 sandbox 段。"""
    cfg = loader.load_config()
    assert "sandbox" in cfg
    assert isinstance(cfg["sandbox"], dict)


def test_config_sandbox_enabled() -> None:
    """AC-13: sandbox.enabled 是 boolean。"""
    cfg = loader.load_config()
    assert isinstance(cfg["sandbox"]["enabled"], bool)


def test_config_sandbox_workspace_root() -> None:
    """AC-13: sandbox.workspace_root 是字符串。"""
    cfg = loader.load_config()
    assert isinstance(cfg["sandbox"]["workspace_root"], str)


def test_config_sandbox_languages_python() -> None:
    """AC-13: sandbox.languages.python 包含 command。"""
    cfg = loader.load_config()
    python_cfg = cfg["sandbox"]["languages"]["python"]
    assert isinstance(python_cfg["command"], str)
    assert python_cfg["command"] == "python"


def test_config_sandbox_limits() -> None:
    """AC-13: sandbox.limits 包含 cpu_timeout_sec/memory_limit_mb/disk_limit_mb。"""
    cfg = loader.load_config()
    limits = cfg["sandbox"]["limits"]
    assert limits["cpu_timeout_sec"] > 0
    assert limits["memory_limit_mb"] > 0
    assert limits["disk_limit_mb"] > 0


def test_config_sandbox_security() -> None:
    """AC-13: sandbox.security 包含 dangerous_patterns。"""
    cfg = loader.load_config()
    security = cfg["sandbox"]["security"]
    assert isinstance(security["code_scan_enabled"], bool)
    assert isinstance(security["env_sanitization_enabled"], bool)
    assert isinstance(security["dangerous_patterns"], list)


def test_config_sandbox_output_thresholds(tmp_path, monkeypatch) -> None:
    """AC-13: sandbox.output 阈值配置正确。"""
    cfg = loader.load_config()
    output = cfg.get("sandbox", {}).get("output", {})
    # output 段在 config.yaml 中可能不存在,存在则校验
    if output:
        for key in ("stdout_artifact_threshold", "code_artifact_threshold"):
            assert key in output, f"sandbox.output.{key} missing"
            assert isinstance(output[key], int), f"sandbox.output.{key} must be int"


def test_sandbox_config_missing_section_ok(tmp_path, monkeypatch) -> None:
    """AC-13: sandbox 段缺失时不抛异常。"""
    minimal = tmp_path / "config_no_sandbox.yaml"
    minimal.write_text(
        textwrap.dedent(
            """
            system:
              app_name: "Private Agent"
              version: "0.1.0"
            tools:
              mcp:
                protocol_version: "2025-11-25"
                servers: []
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "CONFIG_FILE", minimal)
    cfg = loader.load_config()
    assert "sandbox" not in cfg


def test_sandbox_config_bad_enabled_raises(tmp_path, monkeypatch) -> None:
    """AC-13: sandbox.enabled 非 boolean 抛 ValueError。"""
    bad = tmp_path / "config_bad_enabled.yaml"
    bad.write_text(
        textwrap.dedent(
            """
            system:
              app_name: "Private Agent"
              version: "0.1.0"
            tools:
              mcp:
                protocol_version: "2025-11-25"
                servers: []
            sandbox:
              enabled: "yes"
              languages:
                python:
                  command: "python"
              limits:
                cpu_timeout_sec: 300
                memory_limit_mb: 512
                disk_limit_mb: 100
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "CONFIG_FILE", bad)
    with pytest.raises(ValueError, match="sandbox.enabled"):
        loader.load_config()


def test_sandbox_config_bad_command_raises(tmp_path, monkeypatch) -> None:
    """AC-13: sandbox.languages.python.command 空字符串抛 ValueError。"""
    bad = tmp_path / "config_bad_cmd.yaml"
    bad.write_text(
        textwrap.dedent(
            """
            system:
              app_name: "Private Agent"
              version: "0.1.0"
            tools:
              mcp:
                protocol_version: "2025-11-25"
                servers: []
            sandbox:
              enabled: true
              languages:
                python:
                  command: ""
              limits:
                cpu_timeout_sec: 300
                memory_limit_mb: 512
                disk_limit_mb: 100
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "CONFIG_FILE", bad)
    with pytest.raises(ValueError, match="sandbox.languages.python.command"):
        loader.load_config()


def test_sandbox_config_bad_limit_raises(tmp_path, monkeypatch) -> None:
    """AC-13: sandbox.limits.cpu_timeout_sec <= 0 抛 ValueError。"""
    bad = tmp_path / "config_bad_limit.yaml"
    bad.write_text(
        textwrap.dedent(
            """
            system:
              app_name: "Private Agent"
              version: "0.1.0"
            tools:
              mcp:
                protocol_version: "2025-11-25"
                servers: []
            sandbox:
              enabled: true
              languages:
                python:
                  command: "python"
              limits:
                cpu_timeout_sec: -1
                memory_limit_mb: 512
                disk_limit_mb: 100
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "CONFIG_FILE", bad)
    with pytest.raises(ValueError, match="sandbox.limits.cpu_timeout_sec"):
        loader.load_config()