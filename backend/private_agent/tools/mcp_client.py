"""蓝图 §5.x / spec m2-tools-lifecycle - MCPClient MCP 协议客户端。

MCP 2026-07-28 无状态协议实现(Phase 2 协议升级):
- 移除 initialize/initialized 握手与 Mcp-Session-Id(无会话, 每请求自包含)
- HTTP 请求携带 MCP-Protocol-Version / Mcp-Method / Mcp-Name 头
- JSON-RPC body 注入 _meta(clientInfo + protocolVersion + capabilities)
- HTTP POST 直接发往 config.url(MCP 端点, 不再拼接 /rpc)
- 支持 MRTR 检测: resultType == "inputRequired" 时透传 inputRequests/requestState
- stdio 模式同步注入 _meta(子进程管理 + JSON-RPC 通信保持)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from private_agent.errors import McpConnectError

logger = logging.getLogger(__name__)

__all__ = ["MCPClient", "MCPClientConfig", "McpHealthStatus"]

# 2026-07-28 无状态协议版本
PROTOCOL_VERSION = "2026-07-28"
# 旧协议(兼容, 未接入具体 server 时保留)
LEGACY_PROTOCOL_VERSION = "2025-11-25"
# 客户端信息(_meta 中携带)
CLIENT_INFO = {"name": "private-agent", "version": "0.1.0"}


@dataclass
class MCPClientConfig:
    """MCP 服务配置(对应 config.yaml tools.mcp.servers[] 条目)。

    Attributes:
        server_id: 服务唯一标识。
        server_type: 通信类型(stdio/http)。
        command: 启动命令(stdio 模式)。
        args: 启动参数(stdio 模式)。
        url: HTTP 服务地址(http 模式, 为完整 MCP 端点, 如 http://127.0.0.1:3000/mcp)。
        tags: 服务标签列表(用于 ManualRouter 路由)。
        timeout_sec: 通信超时秒数。
        protocol_version: MCP 协议版本(默认 2026-07-28 无状态)。
        health_check_interval_sec: liveness_loop 探活间隔秒数。
    """

    server_id: str
    server_type: str  # "stdio" | "http"
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    tags: list[str] = field(default_factory=list)
    timeout_sec: float = 30.0
    protocol_version: str = PROTOCOL_VERSION
    health_check_interval_sec: float = 30.0
    auth_token: str = ""  # Bearer token(http 模式认证, 请求带 Authorization 头)


@dataclass
class McpHealthStatus:
    """MCP 服务器健康状态(B2 P1-6)。

    Attributes:
        ping_ok: ping 探活是否成功。
        tools_count: 通过 discover_tools 发现的工具数量(失败为 0)。
        latency_ms: ping 往返耗时毫秒。
        detail: 失败时的错误摘要(成功可为空)。
    """

    ping_ok: bool
    tools_count: int = 0
    latency_ms: float = 0.0
    detail: str = ""


class MCPClient:
    """MCP 2026-07-28 无状态协议客户端。

    管理 MCP 服务器子进程的生命周期和 JSON-RPC 通信。
    """

    def __init__(self, config: MCPClientConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._connected: bool = False
        self._request_id: int = 0
        self._reconnect_count: int = 0
        self._read_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._latency_ms: float = 0.0

    # --------------------------------------------------------------------------
    # Properties
    # --------------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    @property
    def config(self) -> MCPClientConfig:
        return self._config

    # --------------------------------------------------------------------------
    # Lifecycle: connect / disconnect
    # --------------------------------------------------------------------------

    async def connect(self) -> None:
        """建立与 MCP 服务器的连接。

        stdio: 启动子进程 + 后台读循环。
        http: 创建 httpx.AsyncClient + 立即 ping 探活(无状态协议无握手,
            请求自包含, connect 仅验证可达性)。

        Raises:
            McpConnectError: HTTP 模式 ping 失败。
            asyncio.TimeoutError: 超时未启动。
        """
        if self._config.server_type == "http":
            await self._connect_http()
            return

        logger.info(
            "Connecting to MCP server '%s': %s %s",
            self._config.server_id,
            self._config.command,
            " ".join(self._config.args),
        )
        self._process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                self._config.command,
                *self._config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=self._config.timeout_sec,
        )
        self._connected = True
        self._reconnect_count = 0
        self._read_task = asyncio.create_task(self._read_loop())
        logger.info("MCP server '%s' connected (pid=%d)", self._config.server_id, self._process.pid)

    async def _connect_http(self) -> None:
        """HTTP 模式:创建 httpx client + 立即 ping 探活(2026-07-28 无状态)。"""
        logger.info(
            "Connecting to MCP HTTP server '%s': %s (protocol %s)",
            self._config.server_id,
            self._config.url,
            self._config.protocol_version,
        )
        if self._http_client is None:
            # 允许测试预注入 mock client;默认创建真实 httpx client
            self._http_client = httpx.AsyncClient(timeout=self._config.timeout_sec)
        try:
            ok = await self.ping()
        except Exception as e:  # noqa: BLE001 - ping 内部已收敛异常,此处兜底
            ok = False
            logger.warning("MCP HTTP ping error for '%s': %s", self._config.server_id, e)
        if not ok:
            await self._close_http_client()
            raise McpConnectError(
                f"MCP HTTP server '{self._config.server_id}' unreachable at {self._config.url}"
            )
        self._connected = True
        self._reconnect_count = 0
        logger.info("MCP HTTP server '%s' connected", self._config.server_id)

    async def disconnect(self) -> None:
        """断开与 MCP 服务器的连接。幂等操作。"""
        if self._config.server_type == "http":
            await self._close_http_client()
            self._connected = False
            return
        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None

        if self._process is not None:
            logger.info("Disconnecting MCP server '%s'", self._config.server_id)
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.communicate(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._process.kill()
                await self._process.communicate()
            self._process = None

        self._connected = False

    async def _close_http_client(self) -> None:
        """关闭 http client(幂等)。"""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def __aenter__(self) -> MCPClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.disconnect()

    # --------------------------------------------------------------------------
    # MCP Protocol: discover / call (2026-07-28 无状态)
    # --------------------------------------------------------------------------

    async def discover_tools(self) -> list[dict]:
        """调用 MCP tools/list 发现服务器工具列表。

        Returns:
            工具描述 dict 列表(原始 MCP schema 格式)。

        Raises:
            RuntimeError: 未连接时调用。
        """
        if not self._connected:
            raise RuntimeError(f"MCP server '{self._config.server_id}' not connected")

        if self._config.server_type == "http":
            result = await self._http_post("tools/list")
            return result.get("tools", [])

        response = await self._send_request({"method": "tools/list"})
        return response.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict:
        """调用 MCP tools/call 执行工具(2026-07-28 无状态)。

        Args:
            name: 工具名称。
            arguments: 工具参数字典。

        Returns:
            MCP 工具调用结果 dict。若服务器返回 MRTR(InputRequiredResult),
            result 含 resultType="inputRequired" + inputRequests/requestState, 原样透传。

        Raises:
            RuntimeError: 未连接时调用。
        """
        if not self._connected:
            raise RuntimeError(f"MCP server '{self._config.server_id}' not connected")

        if self._config.server_type == "http":
            return await self._http_post(
                "tools/call", {"name": name, "arguments": arguments}, name=name
            )

        response = await self._send_request({
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        return response.get("result", {})

    # --------------------------------------------------------------------------
    # 双探活:ping / health_check / liveness_loop (B2 P1-6)
    # --------------------------------------------------------------------------

    async def ping(self) -> bool:
        """MCP JSON-RPC ping 探活(2026-07-28 下 ping 仍受支持)。

        stdio: 发送 {"method":"ping"} 请求,收到 result 视为健康。
        http: POST 端点 ping,200 + 有 result 视为健康。

        Returns:
            True 表示服务器可达且响应正常。
        """
        try:
            if self._config.server_type == "http":
                assert self._http_client is not None
                start = time.monotonic()
                payload = self._build_request("ping")
                resp = await self._http_client.post(
                    self._endpoint(),
                    json=payload,
                    headers=self._auth_headers({
                        "MCP-Protocol-Version": self._config.protocol_version,
                        "Mcp-Method": "ping",
                    }),
                    timeout=self._config.timeout_sec,
                )
                self._latency_ms = (time.monotonic() - start) * 1000
                if resp.status_code >= 400:
                    return False
                data = resp.json()
                return "result" in data
            if not self._connected:
                return False
            response = await self._send_request({"method": "ping"})
            return "result" in response
        except Exception:  # noqa: BLE001 - 探活失败即视为不健康
            return False

    async def health_check(self) -> McpHealthStatus:
        """组合探活:ping + discover_tools。

        Returns:
            McpHealthStatus 包含 ping 结果、工具数、延迟与失败摘要。
        """
        start = time.monotonic()
        ping_ok = await self.ping()
        latency_ms = (time.monotonic() - start) * 1000
        detail = ""
        tools_count = 0
        if ping_ok:
            try:
                tools = await self.discover_tools()
                tools_count = len(tools)
            except Exception as e:  # noqa: BLE001 - 探活不因 discover 失败而中断
                detail = f"discover_tools failed: {e}"
        else:
            detail = "ping failed"
        return McpHealthStatus(
            ping_ok=ping_ok,
            tools_count=tools_count,
            latency_ms=latency_ms,
            detail=detail,
        )

    async def liveness_loop(
        self,
        interval_sec: float | None = None,
        on_unhealthy: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """后台定期 ping 探活循环。

        服务器不健康时调用 on_unhealthy(detail) 回调(若提供)。
        调用方需负责取消本 task。

        Args:
            interval_sec: 探活间隔秒数,默认用 config.health_check_interval_sec。
            on_unhealthy: 探活失败时触发的异步回调。
        """
        interval = interval_sec if interval_sec is not None else self._config.health_check_interval_sec
        while True:
            status = await self.health_check()
            if not status.ping_ok:
                logger.warning(
                    "MCP server '%s' unhealthy: %s", self._config.server_id, status.detail
                )
                if on_unhealthy is not None:
                    await on_unhealthy(status.detail)
            await asyncio.sleep(interval)

    # --------------------------------------------------------------------------
    # Internal: JSON-RPC over HTTP (2026-07-28 无状态)
    # --------------------------------------------------------------------------

    def _build_request(self, method: str, params: dict[str, Any] | None = None) -> dict:
        """构造 JSON-RPC 2.0 请求体, 注入 2026-07-28 无状态 _meta。"""
        self._request_id += 1
        request: dict[str, Any] = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
        if params is not None:
            request["params"] = params
        return request

    def _inject_meta(self, params: dict[str, Any] | None) -> dict[str, Any] | None:
        """向 params 注入 2026-07-28 无状态 _meta(协议版本/客户端信息/能力)。"""
        if params is None:
            params = {}
        meta = params.get("_meta")
        if meta is None:
            meta = {}
            params["_meta"] = meta
        meta.setdefault("protocolVersion", self._config.protocol_version)
        meta.setdefault("io.modelcontextprotocol/clientInfo", CLIENT_INFO)
        meta.setdefault("capabilities", {})
        return params

    def _endpoint(self) -> str:
        """HTTP MCP 端点: 2026-07-28 无状态协议直接使用 config.url(不拼接 /rpc)。"""
        return self._config.url.rstrip("/")

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """组合请求头, 有 auth_token 时附加 Authorization: Bearer。"""
        headers = dict(extra or {})
        if self._config.auth_token:
            headers["Authorization"] = f"Bearer {self._config.auth_token}"
        return headers

    async def _http_post(
        self, method: str, params: dict[str, Any] | None = None, name: str | None = None
    ) -> dict:
        """POST MCP 端点发送 JSON-RPC 请求, 携带无状态协议头, 返回 result dict。

        Args:
            method: JSON-RPC method(如 tools/call), 同时作为 Mcp-Method 头。
            params: 请求参数(注入 _meta)。
            name: 具名目标(工具名), 作为 Mcp-Name 头(tools/call 等)。
        """
        assert self._http_client is not None
        params = self._inject_meta(params)
        payload = self._build_request(method, params)
        headers: dict[str, str] = {
            "MCP-Protocol-Version": self._config.protocol_version,
            "Mcp-Method": method,
        }
        if name is not None:
            headers["Mcp-Name"] = name
        resp = await self._http_client.post(
            self._endpoint(),
            json=payload,
            headers=self._auth_headers(headers),
            timeout=self._config.timeout_sec,
        )
        resp.raise_for_status()
        return resp.json().get("result", {})

    # --------------------------------------------------------------------------
    # Internal: JSON-RPC communication (stdio, 注入 _meta)
    # --------------------------------------------------------------------------

    async def _send_request(self, msg: dict) -> dict:
        """发送 JSON-RPC 请求并等待响应(stdio, body 注入 _meta)。"""
        self._request_id += 1
        # 注入 2026-07-28 无状态 _meta 到 params
        if "params" in msg and isinstance(msg["params"], dict):
            msg = {**msg, "params": self._inject_meta(dict(msg["params"]))}
        elif msg.get("method") in {"tools/list", "tools/call", "ping"}:
            msg = {**msg, "params": self._inject_meta({})}
        request = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            **msg,
        }
        future: asyncio.Future[dict] = asyncio.Future()
        self._pending[self._request_id] = future

        try:
            assert self._process is not None and self._process.stdin is not None
            data = (json.dumps(request) + "\n").encode("utf-8")
            self._process.stdin.write(data)
            await self._process.stdin.drain()

            return await asyncio.wait_for(future, timeout=self._config.timeout_sec)
        finally:
            self._pending.pop(self._request_id, None)

    async def _read_loop(self) -> None:
        """后台读取子进程 stdout 的 JSON-RPC 响应并分发到对应的 Future。"""
        try:
            assert self._process is not None and self._process.stdout is not None
            buffer = b""
            while True:
                chunk = await self._process.stdout.readuntil(b"\n")
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        response = json.loads(line)
                        self._handle_response(response)
                    except json.JSONDecodeError:
                        logger.warning("Invalid JSON from MCP server: %s", line[:200])
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("MCP read loop error for server '%s'", self._config.server_id)

    def _handle_response(self, response: dict) -> None:
        """将 JSON-RPC 响应分发到对应的 pending Future。

        Args:
            response: JSON-RPC 响应 dict。
        """
        rid = response.get("id")
        if rid is not None and rid in self._pending:
            self._pending[rid].set_result(response)
        else:
            logger.debug("Unmatched response: id=%s", rid)
