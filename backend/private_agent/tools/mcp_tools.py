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

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

from private_agent.config import secrets
from private_agent.tools.defs import ToolDef, ToolResult
from private_agent.tools.mcp_client import MCPClient, MCPClientConfig
from private_agent.tools.schema_adapter import mcp_tool_to_tooldef

logger = logging.getLogger(__name__)

__all__ = ["MCPToolManager", "get_mcp_manager", "mcp_result_to_text", "build_tools_guide"]


# 2026-08-23(skill_binding 语义重构): 模块级单例 —— main.py 与 mcp_browse
# 工具共享同一 MCPToolManager(避免双连接/双缓存)。main.py 的 _get_mcp_manager
# 改为引用本单例(一行改动, 行为不变)。
_manager_instance: "MCPToolManager | None" = None


def get_mcp_manager() -> "MCPToolManager":
    """进程级 MCPToolManager 单例(懒创建)。"""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = MCPToolManager()
    return _manager_instance


def build_tools_guide(
    manager: "MCPToolManager", servers: list[dict]
) -> str:
    """生成 MCP 工具速查指南(注入 system prompt, 蓝图 L6585 工具选择优先级)。

    只列 server 分类 + 工具名(完整 schema 已在 tools 字段, 重复描述浪费 token)。
    内容静态稳定 → KV Cache 友好(不改前缀)。

    Args:
        manager: 已装配的 MCPToolManager(读 _tools_cache, 避免重复连接)。
        servers: MCP server 配置列表。

    Returns:
        指南文本;无可用工具时返回空字符串。
    """
    sections: list[str] = []
    for svc in servers:
        sid = svc.get("id") or svc.get("name")
        if not sid:
            continue
        defs = manager._tools_cache.get(sid)
        if not defs:
            continue
        names = [d.name.split("__")[-1] for d in defs if getattr(d, "name", "")]
        if not names:
            continue
        sections.append(
            f"- {sid}({len(names)} 个工具): {', '.join(names)}"
        )
    if not sections:
        return ""
    return "## MCP 工具速查(按 server 分类, 完整参数见 tools 字段)\n" + "\n".join(
        sections
    )


def mcp_result_to_text(result: dict) -> str:
    """MCP tools/call result → 可注入 LLM 上下文的文本。

    Args:
        result: MCP 工具调用结果 dict(含 content 列表 / isError 等)。

    Returns:
        拼接后的文本(空 content 时回退为剩余字段的 JSON; 完全空时给出
        明确占位, 避免 LLM 把 "空串" 误读为"工具正常但没结果"——尤其
        stdio server(协议未传 content)+ 并发 write_lock 阻塞场景)。
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
    # 2026-08-07: 完全空 result(无 content 且无其他字段)常见于 stdio 并发写
    # 入交错或 server 协议不返 content, 给 LLM 一个明确占位而非空串, 让模型
    # 能识别"这是工具结构问题, 不是没有数据"并可主动重试/换工具。
    if not text:
        return "[工具返回空] 该工具未回传 content 字段, 请检查 server 协议或换个工具重试"
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
                timeout_sec=float(svc.get("timeout_sec", 12.0)),  # 30→12s 快速失败
                protocol_version=svc.get("protocol_version", "auto"),
                auth_token=auth_token,
                # V1.2-6.2: server 级环境变量(stdio 子进程注入)
                env=dict(svc.get("env") or {}),
            )
        )
        try:
            await asyncio.wait_for(client.connect(), timeout=8)
        except Exception as e:  # noqa: BLE001
            logger.warning("MCP '%s' connect failed: %s", sid, e)
            return None
        self._clients[sid] = client
        return client

    # --------------------------------------------------------------------------
    # 工具装配
    # --------------------------------------------------------------------------

    async def get_tools(self, cfg: dict, server_ids: list[str] | None = None) -> list[ToolDef]:
        """从配置装配 MCP 工具(懒连接 + 缓存 + 并发装配)。

        所有启用的 server **并发**连接与发现(单个失败/超时跳过, 不阻塞其余),
        避免 17 个 server 串行连接拖慢首条消息处理。

        Args:
            cfg: 合并后的配置 dict(含 tools.mcp.servers)。
            server_ids: 仅装配匹配的 server(skill 绑定过滤, 方向一)。
                支持通配后缀/前缀(如 "hexin-ifind-ds-*"); None 时装配全部。

        Returns:
            ToolDef 列表(名称为 mcp__{server_id}__{original})。
        """
        servers = cfg.get("tools", {}).get("mcp", {}).get("servers", [])
        # V2 P2: assemble=false 的 server 不装配工具(设置页开关, 默认 True)
        enabled = [
            s for s in servers
            if s.get("enabled", True)
            and s.get("assemble", True) is not False
            and (s.get("id") or s.get("name"))
        ]
        # 流畅度优化(方向一): skill 绑定过滤 —— 只装配绑定到当前 skill 的 server
        if server_ids is not None:
            enabled = [s for s in enabled if _match_server_ids(s, server_ids)]
        if not enabled:
            return []

        results = await asyncio.gather(
            *(self._load_server_tools(svc) for svc in enabled),
            return_exceptions=True,
        )
        tools: list[ToolDef] = []
        for r in results:
            if isinstance(r, BaseException):
                continue
            tools.extend(r)
        return tools

    async def _load_server_tools(self, svc: dict) -> list[ToolDef]:
        """单个 server 的工具加载(带缓存与超时, 供并发装配调用)。"""
        sid = svc.get("id") or svc.get("name")
        if sid in self._tools_cache:
            return self._tools_cache[sid]
        client = await self._get_client(svc)
        if client is None or not client.connected:
            return []
        try:
            mcp_tools = await asyncio.wait_for(
                client.discover_tools(), timeout=10
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("MCP '%s' discover_tools failed: %s", sid, e)
            return []
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
        return defs

    # --------------------------------------------------------------------------
    # 2026-08-23(skill_binding 语义重构, 设计 §2.2-A): 全量发现 / 按需装配 / 中转执行
    # --------------------------------------------------------------------------

    async def get_all_server_summary(
        self, cfg: dict, bound_ids: list[str] | None = None
    ) -> list[dict]:
        """返回全部 enabled 且 assemble 的 server 摘要(含未绑定)——按需浏览用。

        Args:
            cfg: 合并后配置 dict(含 tools.mcp.servers)。
            bound_ids: 当前会话已装配(绑定)的 server id 列表, 用于标记 bound。

        Returns:
            [{id, type, bound, tool_count, tools[]}] —— 工具索引(仅已连接缓存的
            名称, 不全 schema; 未连接 server tool_count=None, 仿 Anthropic
            Skills 渐进披露第一级)。
        """
        import fnmatch

        servers = cfg.get("tools", {}).get("mcp", {}).get("servers", [])
        out: list[dict] = []
        for s in servers:
            sid = s.get("id") or s.get("name")
            if not sid:
                continue
            if not s.get("enabled", True):
                continue
            if s.get("assemble", True) is False:
                continue
            cached = self._tools_cache.get(sid)
            out.append(
                {
                    "id": sid,
                    "type": s.get("type", "?"),
                    "bound": bool(
                        bound_ids
                        and any(fnmatch.fnmatch(sid, p) for p in bound_ids)
                    ),
                    "tool_count": len(cached) if cached else None,
                    "tools": (
                        [d.name.split("__")[-1] for d in cached] if cached else []
                    ),
                }
            )
        return out

    async def load_tools_by_server(
        self, cfg: dict, server_ids: list[str]
    ) -> tuple[list[ToolDef], list[str]]:
        """按需装配指定 server 的工具(复用 _load_server_tools 缓存)。

        Args:
            cfg: 合并后配置 dict。
            server_ids: 要装配的 server id 列表(必须存在于配置且 enabled)。

        Returns:
            (tools, missing): 装配到的工具列表 + 未命中/禁用的 server id。
        """
        servers = cfg.get("tools", {}).get("mcp", {}).get("servers", [])
        want = {str(s) for s in server_ids}
        matched = [
            s
            for s in servers
            if (s.get("id") or s.get("name")) in want
            and s.get("enabled", True)
            and s.get("assemble", True) is not False
        ]
        matched_ids = {s.get("id") or s.get("name") for s in matched}
        missing = sorted(want - matched_ids)
        tools: list[ToolDef] = []
        if not matched:
            return tools, missing
        results = await asyncio.gather(
            *(self._load_server_tools(svc) for svc in matched),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BaseException):
                continue
            tools.extend(r)
        return tools, missing

    async def exec_tool(
        self, cfg: dict, server_id: str, tool_name: str, tool_args: dict
    ) -> ToolResult:
        """按 server + 工具名中转执行(不装配, 工具定义不进上下文)。

        2026-08-23(借鉴 Docker mcp-exec): 低频/临时工具(如 codegraph 偶发
        查询)经此直接调用, 上下文零占用; 无需 assemble 进池。

        Args:
            cfg: 合并后配置 dict。
            server_id: MCP server id。
            tool_name: server 内原始工具名(不带 mcp__ 前缀)。
            tool_args: 工具参数 dict。

        Returns:
            ToolResult(文本结果或错误)。
        """
        servers = cfg.get("tools", {}).get("mcp", {}).get("servers", [])
        svc = next(
            (
                s
                for s in servers
                if (s.get("id") or s.get("name")) == server_id
                and s.get("enabled", True)
            ),
            None,
        )
        if svc is None:
            return ToolResult(output="", error=f"MCP server '{server_id}' 不存在或未启用")
        client = await self._get_client(svc)
        if client is None or not client.connected:
            return ToolResult(output="", error=f"MCP server '{server_id}' 连接失败")
        try:
            timeout = float(svc.get("timeout_sec", 12.0))
            result = await asyncio.wait_for(
                client.call_tool(tool_name, tool_args or {}), timeout=timeout
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(output="", error=f"mcp_browse exec 失败: {type(e).__name__}: {e}")
        return ToolResult(output=mcp_result_to_text(result))

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


def _match_server_ids(svc: dict, server_ids: list[str]) -> bool:
    """判断 server 是否命中 skill 绑定列表(支持通配后缀/前缀, 方向一)。

    Args:
        svc: MCP server 配置项(含 id)。
        server_ids: 绑定列表, 如 ["hexin-ifind-ds-*", "mempalace"]。

    Returns:
        True 表示应装配该 server。
    """
    sid = str(svc.get("id") or svc.get("name") or "")
    import fnmatch

    return any(fnmatch.fnmatch(sid, pat) for pat in server_ids)
