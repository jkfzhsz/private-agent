"""蓝图 §2.12/§2.15 config/loader.py - 配置分层加载。

B5.1:加载 config.yaml 静态配置。
B5.4:对 MVP 不支持的 mcp.protocol_version 抛 ConfigNotSupportedInMVP。
B5.2:合并 config_runtime 表运行时覆盖(蓝图 §2.12 优先级:runtime > yaml)。
AC-11:校验 mcp.servers[] 配置合法性。
AC-13:校验 sandbox 配置段合法性。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg
import yaml

from private_agent.errors import ConfigNotSupportedInMVP

# 蓝图 §9.13 config.yaml 位置:backend/config/config.yaml
CONFIG_FILE = Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# 蓝图 §9.13:MVP 锁定的 protocol_version
MVP_SUPPORTED_PROTOCOL_VERSIONS = {"2025-11-25"}

# MVP 支持的 MCP 通信类型
MCP_SUPPORTED_TYPES = {"stdio", "http"}


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """加载 config.yaml 并校验 MVP 不支持的配置项(蓝图 §2.12 静态层)。

    Args:
        config_path: 指定配置文件路径(测试用);默认用 CONFIG_FILE。

    Returns:
        解析后的配置 dict。

    Raises:
        ConfigNotSupportedInMVP: 当 tools.mcp.protocol_version 不在 MVP 支持列表时。
        ValueError: 当 mcp.servers[] 或 sandbox 配置不合法时。
    """
    path = config_path if config_path is not None else CONFIG_FILE
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    _validate_mvp_constraints(cfg)
    return cfg


async def load_config_with_overrides(
    conn: asyncpg.Connection,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """加载 config.yaml 并合并 config_runtime 运行时覆盖(蓝图 §2.12)。

    优先级:config_runtime > config.yaml。
    config_runtime 表结构:key TEXT(点分路径,如 system.sidecar.log_level),value JSONB。

    Args:
        conn: Postgres 连接(用于查询 config_runtime 表)。
        config_path: 指定配置文件路径(测试用);默认用 CONFIG_FILE。

    Returns:
        合并后的配置 dict。

    Raises:
        ConfigNotSupportedInMVP: 合并后的 protocol_version 不在 MVP 支持列表时。
        ValueError: 当 mcp.servers[] 或 sandbox 配置不合法时。
    """
    cfg = load_config(config_path)
    overrides = await _get_runtime_overrides(conn)
    _deep_merge(cfg, overrides)
    _validate_mvp_constraints(cfg)
    return cfg


async def _get_runtime_overrides(conn: asyncpg.Connection) -> dict[str, Any]:
    """查询 config_runtime 表,将点分 key 展开为嵌套 dict。

    例:{"system.sidecar.log_level": "DEBUG"} → {"system": {"sidecar": {"log_level": "DEBUG"}}}
    """
    rows = await conn.fetch("SELECT key, value FROM config_runtime")
    result: dict[str, Any] = {}
    for row in rows:
        keys = row["key"].split(".")
        # asyncpg 对 JSONB 返回 JSON 字符串,需 json.loads 解析为 Python 原生类型
        value = json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]
        d = result
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
    return result


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    """深度合并:override 的值覆盖 base 的同名 key(原地修改 base)。"""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def _validate_mvp_constraints(cfg: dict[str, Any]) -> None:
    """校验 MVP 阶段不支持的配置项(蓝图 §9.13)。

    蓝图 §9.13:loader 对 mcp.protocol_version='2026-07-28' 抛 ConfigNotSupportedInMVP,
    防止 UI 误改导致静默失败。
    AC-11:校验 mcp.servers[] 配置合法性。
    AC-13:校验 sandbox 配置段(蓝图 §6.14)合法性。
    """
    _validate_mcp_protocol_version(cfg)
    _validate_mcp_servers_config(cfg)
    _validate_sandbox_config(cfg)


def _validate_mcp_protocol_version(cfg: dict[str, Any]) -> None:
    """校验 mcp.protocol_version 是否在 MVP 支持列表中。"""
    protocol_version = (
        cfg.get("tools", {}).get("mcp", {}).get("protocol_version")
    )
    if (
        protocol_version is not None
        and protocol_version not in MVP_SUPPORTED_PROTOCOL_VERSIONS
    ):
        raise ConfigNotSupportedInMVP(
            f"mcp.protocol_version='{protocol_version}' is not supported in MVP. "
            f"Supported: {sorted(MVP_SUPPORTED_PROTOCOL_VERSIONS)}. "
            f"See blueprint §9.13."
        )


def _validate_mcp_servers_config(cfg: dict[str, Any]) -> None:
    """校验 mcp.servers[] 配置合法性(AC-11)。

    Raises:
        ValueError: 当配置不合法时。
    """
    servers = cfg.get("tools", {}).get("mcp", {}).get("servers", [])
    for i, entry in enumerate(servers):
        if not isinstance(entry, dict):
            raise ValueError(
                f"mcp.servers[{i}] must be a dict, got {type(entry).__name__}"
            )
        if "id" not in entry or not entry["id"]:
            raise ValueError(
                f"mcp.servers[{i}] missing required field 'id'"
            )
        if "type" not in entry or not entry["type"]:
            raise ValueError(
                f"mcp.servers[{i}] missing required field 'type'"
            )
        if entry["type"] not in MCP_SUPPORTED_TYPES:
            raise ValueError(
                f"mcp.servers[{i}].type='{entry['type']}' is not supported. "
                f"Supported: {sorted(MCP_SUPPORTED_TYPES)}"
            )
        if entry["type"] == "stdio" and not entry.get("command"):
            raise ValueError(
                f"mcp.servers[{i}] type='stdio' requires 'command' field"
            )


def _validate_sandbox_config(cfg: dict[str, Any]) -> None:
    """校验 sandbox 配置段合法性(AC-13, 蓝图 §6.14)。

    Raises:
        ValueError: 当配置不合法时。
    """
    sandbox = cfg.get("sandbox")
    if sandbox is None:
        return  # sandbox 段可选,缺失时跳过校验

    if not isinstance(sandbox, dict):
        raise ValueError("sandbox config must be a dict")

    # enabled 可选,默认为 bool
    if "enabled" in sandbox and not isinstance(sandbox["enabled"], bool):
        raise ValueError("sandbox.enabled must be a boolean")

    # languages.python.command 校验
    python_cfg = sandbox.get("languages", {}).get("python", {})
    if python_cfg and "command" in python_cfg:
        if not isinstance(python_cfg["command"], str) or not python_cfg["command"]:
            raise ValueError("sandbox.languages.python.command must be a non-empty string")

    # limits 校验
    limits = sandbox.get("limits", {})
    if limits:
        for key in ("cpu_timeout_sec", "memory_limit_mb", "disk_limit_mb"):
            if key in limits and (not isinstance(limits[key], (int, float)) or limits[key] <= 0):
                raise ValueError(f"sandbox.limits.{key} must be a positive number")

    # security.code_scan_enabled 和 env_sanitization_enabled 校验
    security = sandbox.get("security", {})
    if security:
        for key in ("code_scan_enabled", "env_sanitization_enabled"):
            if key in security and not isinstance(security[key], bool):
                raise ValueError(f"sandbox.security.{key} must be a boolean")

    # output.*_threshold 校验
    output = sandbox.get("output", {})
    if output:
        for key in ("stdout_artifact_threshold", "code_artifact_threshold"):
            if key in output and (not isinstance(output[key], int) or output[key] < 0):
                raise ValueError(f"sandbox.output.{key} must be a non-negative integer")