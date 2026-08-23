"""阶段4(agent-upgrader 设计文档 §2.2 能力域⑥): mcp_config_manager —— MCP 配置管理。

无涯(monitor)自我扩展工具: 查看/新增/修改 MCP server 配置,
使无涯能"自我接入一个 MCP 服务并验证"(阶段4 验收标准)。

功能:
- mcp_server_list: 列出所有 MCP server(配置摘要, 不泄 token) —— safe
- mcp_server_add: 新增 MCP server(stdin/stdio 或 sse/http) —— elevated
- mcp_browse(2026-08-23, skill_binding 语义重构 §2.2-B): 按需浏览/执行/
  装配/移除 MCP 全局工具(不受起始清单限制) —— list/exec safe,
  assemble/remove 由 react_loop 识别标记后走 elevated 确认并入

安全边界:
- 只读列出 safe; 新增/修改 elevated(WS 确认)
- token 密文不返回明文(has_auth 布尔)
- 新增后复用 admin 的校验语义(protocol_version / type 合法性)
- mcp_browse assemble/remove 的"并入动作"由 react_loop 触发 elevated
  确认(WS 60s), 装配日志入 react_events 可回放
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from private_agent.tools.defs import ToolDef, ToolResult

logger = logging.getLogger(__name__)

__all__ = [
    "MCP_SERVER_LIST_TOOL",
    "MCP_SERVER_ADD_TOOL",
    "MCP_BROWSE_TOOL",
    "MCP_MANAGER_TOOLS",
]

# MCP server 类型合法性(与 loader._validate_mcp_servers_config 同源)
_VALID_TYPES = {"stdio", "sse", "http"}
# 类型 → 必填字段
_REQUIRED_FIELDS = {
    "stdio": ("command",),
    "sse": ("url",),
    "http": ("url",),
}


def _load_mcp_cfg() -> dict:
    """从 config.yaml 读 MCP 配置。"""
    from private_agent.config import loader as cfg_loader

    cfg = cfg_loader.load_config()
    return cfg.get("tools", {}).get("mcp", {}) or {}


async def _mcp_server_list_handler(args: dict) -> ToolResult:
    """列出所有 MCP server 配置(只读, 不泄 token 明文)。"""
    mcp_cfg = _load_mcp_cfg()
    servers = mcp_cfg.get("servers", [])
    lines: list[str] = []
    for s in servers:
        sid = s.get("id") or s.get("name") or "?"
        stype = s.get("type", "?")
        has_auth = bool(s.get("auth_token_encrypted"))
        target = ""
        if stype == "stdio":
            target = s.get("command", "")
        else:
            target = s.get("url", "")
        lines.append(
            f"- {sid} [type={stype}] target={target} auth={'✓' if has_auth else '✗'}"
        )
    return ToolResult(
        output=(
            f"MCP servers 共 {len(servers)} 个"
            f"(protocol_version={mcp_cfg.get('protocol_version', '')}):\n"
            + ("\n".join(lines) if lines else "(无 MCP server 配置)")
        )
    )


async def _mcp_server_add_handler(args: dict) -> ToolResult:
    """新增 MCP server 配置。elevated 需确认。

    Args:
        id: server 标识符(^[A-Za-z0-9_.-]+$)。
        type: stdio / sse / http。
        command: stdio 类型的启动命令(必填)。
        args: stdio 类型参数列表(可选)。
        url: sse/http 类型的端点 URL(必填)。
        env_token: 可选, Bearer token 明文(加密后落库)。
        tags: 可选, 用途标签列表。
    """
    import json
    import re

    sid = str(args.get("id") or "").strip()
    if not sid or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", sid):
        return ToolResult(
            output="", error="id required(^[A-Za-z0-9][A-Za-z0-9_.-]*$)"
        )
    stype = str(args.get("type") or "").strip()
    if stype not in _VALID_TYPES:
        return ToolResult(output="", error=f"type 非法(可选 {sorted(_VALID_TYPES)})")

    # 必填字段校验
    for field in _REQUIRED_FIELDS.get(stype, ()):
        if not str(args.get(field) or "").strip():
            return ToolResult(output="", error=f"type={stype} 需提供 {field}")

    # 读取现有配置
    mcp_cfg = _load_mcp_cfg()
    servers = list(mcp_cfg.get("servers", []))
    for s in servers:
        if (s.get("id") or s.get("name")) == sid:
            return ToolResult(output="", error=f"MCP server {sid} 已存在(先修改再删)")

    entry: dict = {
        "id": sid,
        "type": stype,
    }
    if stype == "stdio":
        entry["command"] = str(args.get("command") or "").strip()
        raw_args = args.get("args")
        if raw_args:
            if isinstance(raw_args, str):
                import shlex

                entry["args"] = shlex.split(raw_args)
            elif isinstance(raw_args, list):
                entry["args"] = [str(a) for a in raw_args]
    else:
        entry["url"] = str(args.get("url") or "").strip()

    env_token = str(args.get("env_token") or "").strip()
    if env_token:
        # 复用 config_runtime 加密通道(admin 同源)
        entry["auth_token_encrypted"] = env_token  # 占位, 落库时加密
    tags = args.get("tags")
    if tags:
        if isinstance(tags, str):
            entry["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
        elif isinstance(tags, list):
            entry["tags"] = [str(t) for t in tags]

    # 写回 config.yaml(通过 config_runtime 持久化? 此处直接写 yaml 需谨慎)
    # 2026-08-16 阶段4: 复用 admin 的 config_runtime 通道 —— 通过 admin API
    # 语义: MCP server 配置在 config.yaml, 新增需写回 yaml + 运行时热加载。
    # 为安全与可回滚, 此处仅返回待写入条目, 由 apply_optim 或用户手动落库。
    return ToolResult(
        output=(
            f"MCP server {sid} 配置已构造(待落库):\n"
            + json.dumps(entry, ensure_ascii=False, indent=1)
            + "\n\n安全提示: 新增 MCP server 是核心改动, 建议先向用户说明"
              "该 server 的用途/数据流向, 经确认后写入 config.yaml 并重启生效。"
        )
    )


MCP_SERVER_LIST_TOOL = ToolDef(
    name="mcp_server_list",
    description=(
        "列出 PA 已配置的 MCP servers(类型/地址/是否带鉴权, 不泄 token)。"
        "只读, 自动执行。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {},
    },
    handler=_mcp_server_list_handler,
    is_kernel=False,
    safety_level="safe",
    risk_level="low",
)

MCP_SERVER_ADD_TOOL = ToolDef(
    name="mcp_server_add",
    description=(
        "构造新的 MCP server 配置(stdio/sse/http), 供接入评估后落库。"
        "会触发权限确认。新增 MCP 属核心改动, 应说明用途后经确认。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "server 标识符(^[A-Za-z0-9_.-]+$)"},
            "type": {"type": "string", "description": "stdio / sse / http"},
            "command": {"type": "string", "description": "stdio 启动命令(必填)"},
            "args": {"type": "array", "items": {"type": "string"}, "description": "stdio 参数(可选)"},
            "url": {"type": "string", "description": "sse/http 端点 URL(必填)"},
            "env_token": {"type": "string", "description": "Bearer token 明文(可选)"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "用途标签(可选)"},
        },
        "required": ["id", "type"],
    },
    handler=_mcp_server_add_handler,
    is_kernel=False,
    safety_level="elevated",
    risk_level="medium",
)

MCP_MANAGER_TOOLS: list[ToolDef] = [
    MCP_SERVER_LIST_TOOL,
    MCP_SERVER_ADD_TOOL,
]

# assemble/remove 的"装配请求"标记(react_loop 识别后 elevated 确认并入)。
_ASSEMBLY_MARKER = "__mcp_assembly"


def _wrap_mcp_cfg() -> dict:
    """把 tools.mcp 配置包装为完整 cfg 形状(get_all_server_summary 等需
    cfg.tools.mcp.servers 路径)。仅 config.yaml —— 已废弃, 见 _wrap_merged_mcp_cfg。"""
    return {"tools": {"mcp": _load_mcp_cfg()}}


async def _wrap_merged_mcp_cfg() -> dict:
    """config.yaml + config_runtime 合并后的 tools.mcp(与 admin._load_cfg 一致)。

    MCP server 配置实际存 config_runtime(设置页动态管理), config.yaml 的
    servers 为空 —— mcp_browse 必须读合并后配置才能看到真实 server。
    延迟 import admin 避免模块级循环依赖(admin import MCP_MANAGER_TOOLS)。
    """
    from private_agent.api import admin as _admin

    cfg = await _admin._load_cfg()
    return cfg.get("tools", {}).get("mcp", {}) or {}


def _split_ids(raw) -> list[str]:
    if isinstance(raw, str):
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(raw, list):
        return [str(s) for s in raw if str(s).strip()]
    return []


async def _mcp_browse_list_handler() -> ToolResult:
    """list: 返回全部 enabled server 的工具索引(渐进披露第一级, 不全 schema)。"""
    from private_agent.tools.mcp_tools import get_mcp_manager

    mcp_cfg = await _wrap_merged_mcp_cfg()
    summary = await get_mcp_manager().get_all_server_summary(
        {"tools": {"mcp": mcp_cfg}}
    )
    if not summary:
        return ToolResult(output="(无已启用且可装配的 MCP server)")
    lines: list[str] = []
    for s in summary:
        if s["tools"]:
            tools_txt = ", ".join(s["tools"])
            lines.append(f"- {s['id']} [type={s['type']}] 工具: {tools_txt}")
        else:
            lines.append(
                f"- {s['id']} [type={s['type']}] 工具数未知(未连接; 可用 "
                "exec 直接调用或 assemble 装配)"
            )
    return ToolResult(
        output=(
            "MCP 全局工具索引(全部已启用 server, 不受起始清单限制):\n"
            + "\n".join(lines)
            + "\n\n用法:\n"
            "  exec <server_id> <tool> <args> —— 直接调用, 不占上下文\n"
            "  assemble <server_ids> —— 装配进本会话工具池(需确认)\n"
            "  remove <server_ids> —— 从会话池移除(需确认)"
        )
    )


async def _mcp_browse_exec_handler(args: dict) -> ToolResult:
    """exec: 按 server_id + tool 名中转执行(工具定义不进上下文)。"""
    from private_agent.tools.mcp_tools import get_mcp_manager

    sid = str(args.get("server_id") or "").strip()
    tool = str(args.get("tool") or "").strip()
    if not sid or not tool:
        return ToolResult(output="", error="exec 需 server_id + tool")
    mcp_cfg = await _wrap_merged_mcp_cfg()
    return await get_mcp_manager().exec_tool(
        {"tools": {"mcp": mcp_cfg}}, sid, tool, args.get("args") or {}
    )


async def _mcp_browse_assemble_handler(args: dict) -> ToolResult:
    """assemble: 返回装配请求标记, 由 react_loop 识别后 elevated 确认并入。"""
    server_ids = _split_ids(args.get("server_ids"))
    if not server_ids:
        return ToolResult(output="", error="assemble 需 server_ids(逗号分隔或数组)")
    return ToolResult(
        output=json.dumps(
            {
                _ASSEMBLY_MARKER: {
                    "action": "assemble",
                    "server_ids": server_ids,
                }
            },
            ensure_ascii=False,
        )
    )


async def _mcp_browse_remove_handler(args: dict) -> ToolResult:
    """remove: 返回装配移除标记, 由 react_loop 识别后 elevated 确认移除。"""
    server_ids = _split_ids(args.get("server_ids"))
    if not server_ids:
        return ToolResult(output="", error="remove 需 server_ids(逗号分隔或数组)")
    return ToolResult(
        output=json.dumps(
            {
                _ASSEMBLY_MARKER: {
                    "action": "remove",
                    "server_ids": server_ids,
                }
            },
            ensure_ascii=False,
        )
    )


async def _mcp_browse_handler(args: dict) -> ToolResult:
    """mcp_browse 总入口: list / exec / assemble / remove。"""
    action = str(args.get("action") or "list").strip().lower()
    if action == "list":
        return await _mcp_browse_list_handler()
    if action == "exec":
        return await _mcp_browse_exec_handler(args)
    if action == "assemble":
        return await _mcp_browse_assemble_handler(args)
    if action == "remove":
        return await _mcp_browse_remove_handler(args)
    return ToolResult(
        output="", error=f"action 非法({action}); 可选 list/exec/assemble/remove"
    )


MCP_BROWSE_TOOL = ToolDef(
    name="mcp_browse",
    description=(
        "按需浏览/执行/装配 PA 的 MCP 全局工具(不受 skill_binding 起始清单限制)。\n"
        "action=list: 列出全部已启用 MCP server 的工具索引(名称+类型, 不全 schema)。\n"
        "action=exec: 直接调用某 server 的工具(server_id + tool + args), "
        "工具不进上下文。\n"
        "action=assemble: 将 server 的工具装配进当前会话工具池(会触发权限确认)。\n"
        "action=remove: 从会话池移除已装配的 server(会触发权限确认)。\n"
        "适用: 未绑定到本会话的 server(如 codegraph)按需访问全局能力。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "exec", "assemble", "remove"],
                "description": "list=浏览索引 / exec=中转执行 / assemble=装配进池 / remove=移除",
            },
            "server_id": {"type": "string", "description": "exec 用: MCP server id"},
            "tool": {"type": "string", "description": "exec 用: server 内工具名"},
            "args": {"type": "object", "description": "exec 用: 工具参数"},
            "server_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "assemble/remove 用: server id 列表(或逗号分隔字符串)",
            },
        },
        "required": ["action"],
    },
    handler=_mcp_browse_handler,
    is_kernel=False,
    safety_level="safe",
    risk_level="low",
)


MCP_MANAGER_TOOLS: list[ToolDef] = [
    MCP_SERVER_LIST_TOOL,
    MCP_SERVER_ADD_TOOL,
    MCP_BROWSE_TOOL,
]
