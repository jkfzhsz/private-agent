"""B5.1+B5.4 - config.yaml loader + mcp.protocol_version 锁定 + mcp.servers 校验。

Source: plan/m0-implementation step 5 (蓝图 §9.13 + §9.6 step5)
         spec m2-tools-lifecycle AC-11
"""
import textwrap

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


def test_mcp_servers_defaults_to_empty_list():
    """tools.mcp.servers 默认值为空列表(蓝图 §5.x / spec m2-tools-lifecycle)。"""
    cfg = loader.load_config()
    assert cfg["tools"]["mcp"]["servers"] == []


def test_mcp_servers_valid_entry_accepts(tmp_path, monkeypatch):
    """AC-11: 合法的 mcp.servers 条目应通过校验。"""
    valid_config = tmp_path / "config_valid.yaml"
    valid_config.write_text(
        textwrap.dedent(
            """
            system:
              app_name: "Private Agent"
              version: "0.1.0"
            tools:
              mcp:
                servers:
                  - id: "filesystem"
                    type: "stdio"
                    command: "npx"
                    args: ["-y", "@modelcontextprotocol/filesystem"]
                    tags: ["utility"]
                    timeout: 30
                protocol_version: "2025-11-25"
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "CONFIG_FILE", valid_config)
    cfg = loader.load_config()
    servers = cfg["tools"]["mcp"]["servers"]
    assert len(servers) == 1
    assert servers[0]["id"] == "filesystem"
    assert servers[0]["type"] == "stdio"


def test_mcp_servers_missing_id_raises(tmp_path, monkeypatch):
    """AC-11: mcp.servers[] 条目缺少 id 应抛 ValueError。"""
    bad_config = tmp_path / "config_no_id.yaml"
    bad_config.write_text(
        textwrap.dedent(
            """
            system:
              app_name: "Private Agent"
              version: "0.1.0"
            tools:
              mcp:
                servers:
                  - type: "stdio"
                    command: "npx"
                protocol_version: "2025-11-25"
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "CONFIG_FILE", bad_config)
    with pytest.raises(ValueError, match="mcp.servers.*id"):
        loader.load_config()


def test_mcp_servers_missing_type_raises(tmp_path, monkeypatch):
    """AC-11: mcp.servers[] 条目缺少 type 应抛 ValueError。"""
    bad_config = tmp_path / "config_no_type.yaml"
    bad_config.write_text(
        textwrap.dedent(
            """
            system:
              app_name: "Private Agent"
              version: "0.1.0"
            tools:
              mcp:
                servers:
                  - id: "filesystem"
                    command: "npx"
                protocol_version: "2025-11-25"
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "CONFIG_FILE", bad_config)
    with pytest.raises(ValueError, match="mcp.servers.*type"):
        loader.load_config()


def test_mcp_servers_invalid_type_raises(tmp_path, monkeypatch):
    """AC-11: mcp.servers[] type 不是 stdio/http 应抛 ValueError。"""
    bad_config = tmp_path / "config_bad_type.yaml"
    bad_config.write_text(
        textwrap.dedent(
            """
            system:
              app_name: "Private Agent"
              version: "0.1.0"
            tools:
              mcp:
                servers:
                  - id: "bad"
                    type: "tcp"
                protocol_version: "2025-11-25"
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "CONFIG_FILE", bad_config)
    with pytest.raises(ValueError, match="mcp.servers.*type"):
        loader.load_config()


def test_mcp_servers_stdio_without_command_raises(tmp_path, monkeypatch):
    """AC-11: stdio 类型缺少 command 应抛 ValueError。"""
    bad_config = tmp_path / "config_no_cmd.yaml"
    bad_config.write_text(
        textwrap.dedent(
            """
            system:
              app_name: "Private Agent"
              version: "0.1.0"
            tools:
              mcp:
                servers:
                  - id: "filesystem"
                    type: "stdio"
                protocol_version: "2025-11-25"
            """
        ).strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "CONFIG_FILE", bad_config)
    with pytest.raises(ValueError, match="mcp.servers.*command"):
        loader.load_config()


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