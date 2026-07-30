"""B5.1+B5.4 - config.yaml loader + mcp.protocol_version 锁定。

Source: plan/m0-implementation step 5 (蓝图 §9.13 + §9.6 step5)
"""
import os
import textwrap
from pathlib import Path

import pytest

from private_agent.config import loader
from private_agent.errors import ConfigNotSupportedInMVP


def test_load_config_returns_dict():
    """load_config() 返回 dict。"""
    cfg = loader.load_config()
    assert isinstance(cfg, dict)


def test_config_system_app_name():
    """system.app_name == 'Private Agent'(蓝图 §9.13)。"""
    cfg = loader.load_config()
    assert cfg["system"]["app_name"] == "Private Agent"


def test_config_system_version():
    """system.version == '0.1.0'(蓝图 §9.13)。"""
    cfg = loader.load_config()
    assert cfg["system"]["version"] == "0.1.0"


def test_mcp_protocol_version_locked_to_2025_11_25():
    """tools.mcp.protocol_version == '2025-11-25'(蓝图 §9.13 MVP 锁定)。"""
    cfg = loader.load_config()
    assert cfg["tools"]["mcp"]["protocol_version"] == "2025-11-25"


def test_loader_raises_on_unsupported_protocol_2026_07_28(tmp_path, monkeypatch):
    """当 config.yaml 的 protocol_version == '2026-07-28' 时,load_config() 抛 ConfigNotSupportedInMVP。

    蓝图 §9.13:loader 对 2026-07-28 抛 ConfigNotSupportedInMVP,防止 UI 误改静默失败。
    """
    # 构造一个最小的不合法 config.yaml
    bad_config = tmp_path / "config_bad.yaml"
    bad_config.write_text(
        textwrap.dedent(
            """
            system:
              app_name: "Private Agent"
              version: "0.1.0"
            tools:
              mcp:
                protocol_version: "2026-07-28"
            """
        ).strip(),
        encoding="utf-8",
    )
    # monkeypatch 让 loader 读这个文件
    monkeypatch.setattr(loader, "CONFIG_FILE", bad_config)
    with pytest.raises(ConfigNotSupportedInMVP):
        loader.load_config()
