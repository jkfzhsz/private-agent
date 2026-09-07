"""mcp_config_manager 配置读取源回归测试。

背景(2026-08-31 session-76):
MCP server 实际存 **config_runtime**(设置页动态管理), config.yaml 的
`servers` 恒为空数组。`mcp_browse` 读的是合并配置(`_wrap_merged_mcp_cfg`,
正确), 但 `mcp_server_list` / `mcp_server_add` 仍在用旧的 `_load_mcp_cfg()`
(只读 yaml) —— 导致:

1. `mcp_server_list` **恒报 0 个 server**, 与 mcp_browse 口径打架,
   误导智能体判断 MCP 现状(session-76 中无涯据此得出"无 MCP server 配置")。
2. `mcp_server_add` 的重复 id 检测形同虚设 —— 检测源恒空, 可构造出与
   config_runtime 中已有 server 同名的条目。

本文件锁定: 两个 handler 一律读合并配置。
纯 mock 测试, 不依赖数据库。
"""
from __future__ import annotations

import pytest

import private_agent.api.admin as admin
from private_agent.tools.builtins.mcp_config_manager import (
    _mcp_server_add_handler,
    _mcp_server_list_handler,
)
from private_agent.tools.defs import ToolResult


def _cfg_with_servers(servers: list[dict]) -> dict:
    """构造 admin._load_cfg 返回形状(模拟 config_runtime 中的 server)。"""
    return {
        "tools": {
            "mcp": {
                "protocol_version": "2026-07-28",
                "servers": servers,
            }
        }
    }


@pytest.fixture
def _mock_admin_cfg(monkeypatch):
    """把 admin._load_cfg 替换为可控配置源。"""

    def _install(servers: list[dict]):
        async def _fake_load_cfg() -> dict:
            return _cfg_with_servers(servers)

        monkeypatch.setattr(admin, "_load_cfg", _fake_load_cfg)

    return _install


# --- mcp_server_list: 必须反映 config_runtime 中的 server -------------------

async def test_server_list_reads_merged_config(_mock_admin_cfg):
    """config_runtime 中的 server 必须被列出(修复前恒报 0 个)。"""
    _mock_admin_cfg(
        [
            {"id": "mempalace", "type": "stdio", "command": "mempalace"},
            {"id": "codegraph", "type": "stdio", "command": "codegraph"},
            {"id": "ifind", "type": "http", "url": "http://127.0.0.1:9000/mcp"},
        ]
    )

    result = await _mcp_server_list_handler({})

    assert result.error is None
    assert "共 3 个" in result.output, result.output
    assert "mempalace" in result.output
    assert "codegraph" in result.output
    # http 类型显示 url 而非 command
    assert "http://127.0.0.1:9000/mcp" in result.output
    assert "protocol_version=2026-07-28" in result.output


async def test_server_list_empty_when_no_servers(_mock_admin_cfg):
    """确实没有 server 时才报 0 个。"""
    _mock_admin_cfg([])

    result = await _mcp_server_list_handler({})

    assert result.error is None
    assert "共 0 个" in result.output
    assert "(无 MCP server 配置)" in result.output


async def test_server_list_marks_auth_presence(_mock_admin_cfg):
    """带鉴权的 server 显示 auth=✓, 不泄露 token 明文。"""
    _mock_admin_cfg(
        [
            {"id": "sec", "type": "http", "url": "https://x/mcp",
             "auth_token_encrypted": "ENCRYPTED_BLOB"},
            {"id": "plain", "type": "stdio", "command": "plain"},
        ]
    )

    result = await _mcp_server_list_handler({})

    assert "sec [type=http]" in result.output
    assert "auth=✓" in result.output
    assert "auth=✗" in result.output
    assert "ENCRYPTED_BLOB" not in result.output, "不得泄露 token 密文/明文"


# --- mcp_server_add: 重复 id 检测必须覆盖 config_runtime --------------------

async def test_server_add_rejects_duplicate_in_config_runtime(_mock_admin_cfg):
    """config_runtime 中已存在的 id 必须被拒(修复前检测源恒空, 检测失效)。"""
    _mock_admin_cfg([{"id": "mempalace", "type": "stdio", "command": "mempalace"}])

    result = await _mcp_server_add_handler(
        {"id": "mempalace", "type": "stdio", "command": "other"}
    )

    assert result.error is not None
    assert "已存在" in result.error
    assert "mempalace" in result.error


async def test_server_add_allows_new_id(_mock_admin_cfg, monkeypatch):
    """不与现有 server 冲突的新 id 可以落库(2026-09-07 F2 起真实落库)。"""
    _mock_admin_cfg([{"id": "mempalace", "type": "stdio", "command": "mempalace"}])
    written = _mock_persistence(monkeypatch)

    result = await _mcp_server_add_handler(
        {"id": "new-server", "type": "stdio", "command": "new-cmd",
         "args": ["--flag", "v"]}
    )

    assert result.error is None
    assert "new-server" in result.output
    assert "已写入 config_runtime" in result.output
    # F2: 真实落库 —— _write_servers_runtime 被调用且 entry 结构正确
    assert len(written) == 1
    entry = written[0][0]
    assert entry["id"] == "new-server"
    assert entry["type"] == "stdio"
    assert entry["command"] == "new-cmd"
    assert entry["args"] == ["--flag", "v"]
    assert entry["enabled"] is True


# --- 2026-09-07 F2: mcp_server_add 真实落库 config_runtime -------------------


def _mock_persistence(monkeypatch):
    """mock db.connect + admin._write_servers_runtime, 返回写入记录列表。"""
    import private_agent.storage.db as db_mod

    written: list[list[dict]] = []

    class _FakeConn:
        async def close(self) -> None:
            pass

    async def _fake_connect(cfg=None) -> _FakeConn:
        return _FakeConn()

    async def _fake_write(conn, servers: list[dict]) -> None:
        written.append(servers)

    monkeypatch.setattr(db_mod, "connect", _fake_connect)
    monkeypatch.setattr(admin, "_write_servers_runtime", _fake_write)
    return written


async def test_server_add_persists_http_type(_mock_admin_cfg, monkeypatch):
    """http 类型落库保留 url 与原始 type(不走 admin 的 http/stdio 二分)。"""
    _mock_admin_cfg([])
    written = _mock_persistence(monkeypatch)

    result = await _mcp_server_add_handler(
        {"id": "remote-mcp", "type": "http", "url": "https://x.example/mcp"}
    )

    assert result.error is None
    entry = written[0][0]
    assert entry["type"] == "http"
    assert entry["url"] == "https://x.example/mcp"
    assert "command" not in entry


async def test_server_add_encrypts_token(_mock_admin_cfg, monkeypatch):
    """env_token 经 admin 同源加密通道落库, 明文不出现在输出/落库条目。"""
    _mock_admin_cfg([])
    written = _mock_persistence(monkeypatch)
    monkeypatch.setattr(admin, "_ensure_master_key", lambda: b"k" * 32)

    result = await _mcp_server_add_handler(
        {"id": "sec-server", "type": "http", "url": "https://x/mcp",
         "env_token": "PLAIN_SECRET_TOKEN"}
    )

    assert result.error is None, result.error
    entry = written[0][0]
    blob = entry.get("auth_token_encrypted", "")
    assert blob and blob != "PLAIN_SECRET_TOKEN"
    assert "PLAIN_SECRET_TOKEN" not in result.output


async def test_server_add_db_failure_returns_error(_mock_admin_cfg, monkeypatch):
    """落库异常 → 明确错误(不静默, 不含半截状态)。"""
    import private_agent.storage.db as db_mod

    _mock_admin_cfg([])

    async def _boom(cfg=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_mod, "connect", _boom)

    result = await _mcp_server_add_handler(
        {"id": "x-server", "type": "stdio", "command": "x"}
    )

    assert result.error is not None
    assert "落库失败" in result.error


async def test_server_add_validates_id_and_type(_mock_admin_cfg):
    """非法 id / type 仍在读配置之前被拒。"""
    _mock_admin_cfg([])

    bad_id = await _mcp_server_add_handler({"id": "bad id!", "type": "stdio"})
    assert bad_id.error is not None and "id required" in bad_id.error

    bad_type = await _mcp_server_add_handler({"id": "ok-id", "type": "grpc"})
    assert bad_type.error is not None and "type 非法" in bad_type.error

    missing_cmd = await _mcp_server_add_handler({"id": "ok-id", "type": "stdio"})
    assert missing_cmd.error is not None and "command" in missing_cmd.error
