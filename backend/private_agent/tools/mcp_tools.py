"""蓝图 §5.1/5.2 - MCP 工具装配(双轨架构外轨)。

将 config.yaml/config_runtime 中的 MCP server 工具注册为 ToolDef,
接入 react_loop 供 Agent 调用。

设计:
- MCPToolManager 进程级单例(main.py 持有), 懒连接 + 客户端缓存 + 工具缓存
- 工具命名: mcp__{server_id}__{original_name}(前缀防冲突, handler 用原始名调用)
- handler 包装 client.call_tool: MCP result → ToolResult(文本), 支持
  isError / MRTR(inputRequired) 处理
- MCP 工具默认全量可用(不参与 skill 白名单过滤, 属扩展能力)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from private_agent.config import secrets
from private_agent.tools.defs import ToolDef, ToolResult
from private_agent.tools.mcp_client import MCPClient, MCPClientConfig
from private_agent.tools.schema_adapter import mcp_tool_to_tooldef

logger = logging.getLogger(__name__)

__all__ = ["MCPToolManager", "mcp_result_to_text"]


def mcp_result_to_text(result: dict) -> str:
    """MCP tools/call result → 可注入 LLM 上下文的文本。

    Args:
        result: MCP 工具调用结果 dict(含 content 列表 / isError 等)。

    Returns:
        拼接后的文本(空 content 时回退为剩余字段的 JSON)。
    """
    parts: list[str] = []
    for item in result.get("content", []):
        if not isinstance(item, dict):
            continue
        t = item.get("type", "")
        if t == "text":
            parts.append(str(item.get("text", "")))
        elif t == "image":
            parts.append("[图片数据]")
        elif t == "audio":
            parts.append("[音频数据]")
        elif t == "resource":
            parts.append(str(item.get("text", "") or item.get("resource", "")))
    text = "\n".join(p for p in parts if p).strip()
    if result.get("isError"):
        return f"[工具错误] {text or '无错误详情'}"
    if not text:
        remaining = {k: v for k, v in result.items() if k not in ("content",)}
        if remaining:
            text = json.dumps(remaining, ensure_ascii=False)
    return text


class MCPToolManager:
    """MCP 客户端生命周期与工具装配管理。"""

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._tools_cache: dict[str, list[ToolDef]] = {}

    # --------------------------------------------------------------------------
    # 客户端懒连接
    # --------------------------------------------------------------------------

    async def _get_client(self, svc: dict) -> MCPClient | None:
        """按 server 配置懒连接 MCPClient(已连接则复用)。失败返回 None。"""
        sid = svc.get("id") or svc.get("name")
        if not sid:
            return None
        cached = self._clients.get(sid)
        if cached is not None:
            return cached

        auth_token = ""
        if svc.get("auth_token_encrypted"):
            try:
                master = secrets.get_master_key()
                auth_token = secrets.decrypt_api_key(svc["auth_token_encrypted"], master)
            except Exception:  # noqa: BLE001 - 解密失败按无 token 处理(401 由调用方暴露)
                logger.warning("MCP '%s' auth_token decrypt failed", sid)

        client = MCPClient(
            MCPClientConfig(
                server_id=sid,
                server_type=svc.get("type", "http"),
                command=svc.get("command", ""),
                args=list(svc.get("args") or []),
                url=svc.get("url", ""),
                timeout_sec=float(svc.get("timeout_sec", 30.0)),
                protocol_version=svc.get("protocol_version", "auto"),
                auth_token=auth_token,
            )
        )
        try:
            await client.connect()
        except Exception as e:  # noqa: BLE001
            logger.warning("MCP '%s' connect failed: %s", sid, e)
            return None
        self._clients[sid] = client
        return client

    # --------------------------------------------------------------------------
    # 工具装配
    # --------------------------------------------------------------------------

    async def get_tools(self, cfg: dict) -> list[ToolDef]:
        """从配置装配全部 MCP 工具(懒连接 + 缓存)。

        Args:
            cfg: 合并后的配置 dict(含 tools.mcp.servers)。

        Returns:
            ToolDef 列表(名称为 mcp__{server_id}__{original})。
            连接/发现失败的非启用 server 被跳过, 不影响其余。
        """
        servers = cfg.get("tools", {}).get("mcp", {}).get("servers", [])
        tools: list[ToolDef] = []
        for svc in servers:
            if not svc.get("enabled", True):
                continue
            sid = svc.get("id") or svc.get("name")
            if not sid:
                continue
            if sid in self._tools_cache:
                tools.extend(self._tools_cache[sid])
                continue
            client = await self._get_client(svc)
            if client is None or not client.connected:
                continue
            try:
                mcp_tools = await client.discover_tools()
            except Exception as e:  # noqa: BLE001
                logger.warning("MCP '%s' discover_tools failed: %s", sid, e)
                continue
            defs: list[ToolDef] = []
            for mt in mcp_tools:
                base = mcp_tool_to_tooldef(mt)
                original_name = base.name
                defs.append(
                    ToolDef(
                        name=f"mcp__{sid}__{original_name}",
                        description=base.description,
                        parameters_schema=base.parameters_schema,
                        handler=self._make_handler(client, original_name),
                    )
                )
            self._tools_cache[sid] = defs
            logger.info("MCP '%s' loaded %d tools", sid, len(defs))
            tools.extend(defs)
        return tools

    def _make_handler(
        self, client: MCPClient, original_name: str
    ) -> Callable[[dict], Awaitable[ToolResult]]:
        """生成 MCP 工具 handler(包装 call_tool)。"""

        async def handler(args: dict) -> ToolResult:
            try:
                result = await client.call_tool(original_name, args)
            except Exception as e:  # noqa: BLE001
                return ToolResult(
                    output="",
                    error=f"MCP 工具调用失败: {type(e).__name__}: {e}",
                )
            if result.get("resultType") == "inputRequired":
                return ToolResult(
                    output="",
                    error=f"[MCP 需要用户确认] {json.dumps(result, ensure_ascii=False)[:300]}",
                )
            text = mcp_result_to_text(result)
            if result.get("isError"):
                return ToolResult(output="", error=text)
            return ToolResult(output=text)

        return handler

    # --------------------------------------------------------------------------
    # 清理
    # --------------------------------------------------------------------------

    async def close_all(self) -> None:
        """断开全部 MCP 客户端并清空缓存(进程退出时调用)。"""
        for client in self._clients.values():
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()
        self._tools_cache.clear()
