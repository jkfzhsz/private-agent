"""蓝图 §5.x / spec m2-tools-lifecycle - MCPClient MCP 协议客户端。

双协议支持 + 自动协商(方案 A):
- 新协议 2026-07-28(无状态): 无握手/会话, 请求带 MCP-Protocol-Version/Mcp-Method/Mcp-Name 头,
  body 注入 _meta, HTTP POST 直连 config.url
- 旧协议 2025-11-25(有状态): initialize 握手 + Mcp-Session-Id, 响应可能为 SSE 流式
- 自动协商(auto, 默认): 先尝试新协议, 收到 "Unsupported protocol version"(400) 时
  自动降级为旧协议并持久化协商结果(进程内), 后续请求直接用旧协议
- SSE 流式响应解析: text/event-stream → 逐事件提取 data: 行 JSON
- 支持 MRTR 检测: resultType == "inputRequired" 时透传
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from private_agent.errors import McpConnectError

logger = logging.getLogger(__name__)

__all__ = ["MCPClient", "MCPClientConfig", "McpHealthStatus"]

# 协议版本常量
PROTOCOL_VERSION = "2026-07-28"   # 无状态(默认)
LEGACY_PROTOCOL_VERSION = "2025-11-25"  # 旧有状态(自动协商降级目标)
PROTOCOL_AUTO = "auto"            # 自动协商
CLIENT_INFO = {"name": "private-agent", "version": "0.1.0"}
# T-4(架构修订 P2-4): stdio 单行 JSON-RPC 大小上限(2MB, 防恶意 server 内存耗尽)
_MAX_MCP_LINE_BYTES = 2 * 1024 * 1024


@dataclass
class MCPClientConfig:
    """MCP 服务配置(对应 config.yaml tools.mcp.servers[] 条目)。

    Attributes:
        server_id: 服务唯一标识。
        server_type: 通信类型(stdio/http)。
        command: 启动命令(stdio 模式)。
        args: 启动参数(stdio 模式)。
        url: HTTP 服务地址(http 模式, 为完整 MCP 端点)。
        tags: 服务标签列表(用于 ManualRouter 路由)。
        timeout_sec: 通信超时秒数。
        protocol_version: "auto"(自动协商) | "2026-07-28" | "2025-11-25"。
        health_check_interval_sec: liveness_loop 探活间隔秒数。
        auth_token: Bearer token(http 模式认证, 请求带 Authorization 头)。
    """

    server_id: str
    server_type: str  # "stdio" | "http"
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    tags: list[str] = field(default_factory=list)
    timeout_sec: float = 30.0
    protocol_version: str = PROTOCOL_AUTO
    health_check_interval_sec: float = 30.0
    auth_token: str = ""  # Bearer token(http 模式认证, 请求带 Authorization 头)
    # V1.2-6.2: 额外环境变量(stdio 模式启动子进程时注入, 如 API Key)
    env: dict[str, str] = field(default_factory=dict)


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


def parse_sse(text: str) -> list[dict]:
    """解析 SSE(text/event-stream)响应, 提取所有 data: 行的 JSON。

    Args:
        text: SSE 原始文本。

    Returns:
        JSON dict 列表(每个 data 行一个, 无 data 行为空列表)。
    """
    results: list[dict] = []
    for block in text.split("\n\n"):
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            continue
        payload = "\n".join(data_lines)
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                results.append(obj)
        except json.JSONDecodeError:
            continue
    return results


class MCPClient:
    """MCP 协议客户端(双协议 + 自动协商)。"""

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
        # 自动协商状态
        self._negotiated: str | None = None   # 协商后的协议版本(进程内)
        self._session_id: str | None = None   # 旧协议 Mcp-Session-Id

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

    @property
    def negotiated_version(self) -> str | None:
        """自动协商出的协议版本(auto 模式且已协商时非空)。"""
        return self._negotiated

    # --------------------------------------------------------------------------
    # Lifecycle: connect / disconnect
    # --------------------------------------------------------------------------

    async def connect(self) -> None:
        """建立与 MCP 服务器的连接。

        stdio: 启动子进程 + 后台读循环。
        http: 创建 httpx.AsyncClient + 立即 ping 探活(自动协商内含版本探测)。

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
                # V1.2-6.2: 注入 server 级 env(合并进系统环境变量, 如 API Key)
                env={
                    **os.environ,
                    **{k: str(v) for k, v in self._config.env.items()},
                },
            ),
            timeout=self._config.timeout_sec,
        )
        self._connected = True
        self._reconnect_count = 0
        self._read_task = asyncio.create_task(self._read_loop())
        logger.info("MCP server '%s' connected (pid=%d)", self._config.server_id, self._process.pid)

    async def _connect_http(self) -> None:
        """HTTP 模式:创建 httpx client + 立即 ping 探活(含自动协商)。"""
        logger.info(
            "Connecting to MCP HTTP server '%s': %s (protocol=%s)",
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
        logger.info(
            "MCP HTTP server '%s' connected (negotiated=%s)",
            self._config.server_id,
            self._negotiated or self._config.protocol_version,
        )

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
    # MCP Protocol: discover / call (双协议)
    # --------------------------------------------------------------------------

    async def discover_tools(self) -> list[dict]:
        """调用 MCP tools/list 发现服务器工具列表。"""
        if not self._connected:
            raise RuntimeError(f"MCP server '{self._config.server_id}' not connected")

        if self._config.server_type == "http":
            result = await self._http_post("tools/list")
            return result.get("tools", [])

        response = await self._send_request({"method": "tools/list"})
        return response.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict:
        """调用 MCP tools/call 执行工具(自动协商协议版本)。

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
    # 双探活:ping / health_check / liveness_loop
    # --------------------------------------------------------------------------

    async def ping(self) -> bool:
        """MCP JSON-RPC ping 探活(双协议, auto 模式会触发版本协商)。"""
        try:
            if self._config.server_type == "http":
                assert self._http_client is not None
                start = time.monotonic()
                ok = await self._send_with_negotiation(
                    "ping",
                    params=None,
                    is_ping=True,
                )
                self._latency_ms = (time.monotonic() - start) * 1000
                return ok
            if not self._connected:
                return False
            response = await self._send_request({"method": "ping"})
            return "result" in response
        except Exception:  # noqa: BLE001 - 探活失败即视为不健康
            return False

    async def health_check(self) -> McpHealthStatus:
        """组合探活:ping + discover_tools。"""
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
        """后台定期 ping 探活循环。"""
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
    # Internal: 双协议 HTTP 请求 + 自动协商
    # --------------------------------------------------------------------------

    def _effective_version(self) -> str:
        """返回当前生效的协议版本。"""
        if self._config.protocol_version != PROTOCOL_AUTO:
            return self._config.protocol_version
        return self._negotiated or PROTOCOL_VERSION

    def _is_legacy(self) -> bool:
        return self._effective_version() == LEGACY_PROTOCOL_VERSION

    def _endpoint(self) -> str:
        """HTTP MCP 端点: 直接使用 config.url(不拼接路径)。"""
        return self._config.url.rstrip("/")

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """组合请求头, 有 auth_token 时附加 Authorization: Bearer。"""
        headers = dict(extra or {})
        if self._config.auth_token:
            headers["Authorization"] = f"Bearer {self._config.auth_token}"
        return headers

    def _build_request(self, method: str, params: dict[str, Any] | None = None) -> dict:
        """构造 JSON-RPC 2.0 请求体。"""
        self._request_id += 1
        request: dict[str, Any] = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
        if params is not None:
            request["params"] = params
        return request

    def _inject_meta(self, params: dict[str, Any] | None) -> dict[str, Any] | None:
        """向 params 注入 2026-07-28 无状态 _meta(仅新协议使用)。"""
        if params is None:
            params = {}
        meta = params.get("_meta")
        if meta is None:
            meta = {}
            params["_meta"] = meta
        meta.setdefault("protocolVersion", PROTOCOL_VERSION)
        meta.setdefault("io.modelcontextprotocol/clientInfo", CLIENT_INFO)
        meta.setdefault("capabilities", {})
        return params

    async def _parse_http_response(self, resp: httpx.Response) -> dict:
        """解析 HTTP 响应(支持普通 JSON 与 SSE 流式), 返回完整 JSON-RPC 响应 dict。"""
        if resp.status_code >= 400:
            try:
                data = resp.json()
                return data if isinstance(data, dict) else {"error": {"message": resp.text[:300]}}
            except Exception:  # noqa: BLE001
                return {"error": {"message": resp.text[:300]}}
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            events = parse_sse(resp.text)
            return events[-1] if events else {"error": {"message": "empty SSE response"}}
        try:
            data = resp.json()
            return data if isinstance(data, dict) else {"result": {}}
        except Exception:  # noqa: BLE001
            return {"error": {"message": resp.text[:300]}}

    async def _legacy_initialize(self) -> None:
        """旧协议握手: 发送 initialize(2025-11-25), 保存 Mcp-Session-Id。"""
        assert self._http_client is not None
        payload = self._build_request(
            "initialize",
            {
                "protocolVersion": LEGACY_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        resp = await self._http_client.post(
            self._endpoint(),
            json=payload,
            headers=self._auth_headers({"Accept": "application/json, text/event-stream"}),
            timeout=self._config.timeout_sec,
        )
        data = await self._parse_http_response(resp)
        # 旧协议服务器可能在响应头返回 Mcp-Session-Id(无状态模式下也可为空)
        session_id = resp.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        result = data.get("result", {})
        server_version = result.get("protocolVersion") or LEGACY_PROTOCOL_VERSION
        self._negotiated = server_version if server_version in (
            LEGACY_PROTOCOL_VERSION, "2025-06-18",
        ) else LEGACY_PROTOCOL_VERSION
        logger.info(
            "MCP '%s' negotiated legacy protocol %s (session=%s)",
            self._config.server_id,
            self._negotiated,
            bool(self._session_id),
        )

    async def _send_with_negotiation(
        self,
        method: str,
        params: dict[str, Any] | None,
        name: str | None = None,
        is_ping: bool = False,
    ) -> Any:
        """发送请求, 带自动协商: 新协议被拒(Unsupported protocol version)时降级旧协议重试。

        Returns:
            is_ping 时返回 bool(是否有 result); 否则返回 result dict。
        """
        assert self._http_client is not None
        version = self._effective_version()

        # 旧协议: 确保已完成 initialize 握手
        if version == LEGACY_PROTOCOL_VERSION and self._negotiated is None:
            await self._legacy_initialize()

        # 构造请求(按协议分支)
        if self._is_legacy():
            payload = self._build_request(method, params)
            headers: dict[str, str] = {"Accept": "application/json, text/event-stream"}
            if self._session_id:
                headers["Mcp-Session-Id"] = self._session_id
        else:
            params = self._inject_meta(params)
            payload = self._build_request(method, params)
            headers = {"MCP-Protocol-Version": version, "Mcp-Method": method}
            if name is not None:
                headers["Mcp-Name"] = name

        resp = await self._http_client.post(
            self._endpoint(),
            json=payload,
            headers=self._auth_headers(headers),
            timeout=self._config.timeout_sec,
        )

        # 自动协商: 400 + Unsupported protocol version + auto 模式 + 尚未协商 → 降级重试
        if (
            resp.status_code == 400
            and self._config.protocol_version == PROTOCOL_AUTO
            and self._negotiated is None
            and "Unsupported protocol version" in resp.text
        ):
            logger.info(
                "MCP '%s' rejects %s, downgrading to %s",
                self._config.server_id, version, LEGACY_PROTOCOL_VERSION,
            )
            self._negotiated = LEGACY_PROTOCOL_VERSION
            await self._legacy_initialize()
            return await self._send_with_negotiation(method, params, name, is_ping)

        data = await self._parse_http_response(resp)
        if is_ping:
            return "result" in data and "error" not in data
        return data.get("result", {})

    async def _http_post(
        self, method: str, params: dict[str, Any] | None = None, name: str | None = None
    ) -> dict:
        """POST MCP 端点发送请求(自动协商协议), 返回 result dict。"""
        return await self._send_with_negotiation(method, params, name)

    # --------------------------------------------------------------------------
    # Internal: JSON-RPC communication (stdio, 注入 _meta)
    # --------------------------------------------------------------------------

    async def _send_request(self, msg: dict) -> dict:
        """发送 JSON-RPC 请求并等待响应(stdio, body 注入 _meta)。"""
        self._request_id += 1
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
                    # T-4(架构修订 P2-4): stdio 行大小上限 —— 恶意/失陷
                    # MCP server 发送超长行会耗尽内存, 超限行直接丢弃
                    if len(line) > _MAX_MCP_LINE_BYTES:
                        logger.warning(
                            "MCP line too large (%d bytes) from server '%s', "
                            "dropping line", len(line), self._config.server_id,
                        )
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
        """将 JSON-RPC 响应分发到对应的 pending Future。"""
        # T-4(架构修订 P2-4): 校验 jsonrpc 协议版本 —— 防御失陷 server 的
        # 伪造响应进入 pending 分发
        if response.get("jsonrpc") not in (None, "2.0"):
            logger.warning(
                "invalid jsonrpc version from MCP server '%s': %r",
                self._config.server_id, response.get("jsonrpc"),
            )
            return
        rid = response.get("id")
        if rid is not None and rid in self._pending:
            self._pending[rid].set_result(response)
        else:
            logger.debug("Unmatched response: id=%s", rid)
