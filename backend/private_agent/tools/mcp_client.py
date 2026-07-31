"""蓝图 §5.x / spec m2-tools-lifecycle - MCPClient MCP 协议客户端。

MCP 2025-11-25 协议实现:
- stdio 模式:全量实现(子进程管理、JSON-RPC 通信)
- HTTP 模式:仅类型定义 + 配置解析 stub,调用时抛 McpHttpStubNotImplementedError
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from private_agent.errors import McpHttpStubNotImplementedError

logger = logging.getLogger(__name__)

__all__ = ["MCPClient", "MCPClientConfig"]


@dataclass
class MCPClientConfig:
    """MCP 服务配置(对应 config.yaml tools.mcp.servers[] 条目)。

    Attributes:
        server_id: 服务唯一标识。
        server_type: 通信类型(stdio/http)。
        command: 启动命令(stdio 模式)。
        args: 启动参数(stdio 模式)。
        tags: 服务标签列表(用于 ManualRouter 路由)。
        timeout_sec: 通信超时秒数。
    """

    server_id: str
    server_type: str  # "stdio" | "http"
    command: str = ""
    args: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    timeout_sec: float = 30.0


class MCPClient:
    """MCP 协议客户端。

    管理 MCP 服务器子进程的生命周期和 JSON-RPC 通信。
    """

    def __init__(self, config: MCPClientConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._connected: bool = False
        self._request_id: int = 0
        self._reconnect_count: int = 0
        self._read_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict]] = {}

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

        Raises:
            McpHttpStubNotImplementedError: HTTP 模式下调用。
            asyncio.TimeoutError: 超时未启动。
        """
        if self._config.server_type == "http":
            raise McpHttpStubNotImplementedError(
                f"MCP HTTP mode not implemented for server '{self._config.server_id}'"
            )

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

    async def disconnect(self) -> None:
        """断开与 MCP 服务器的连接。幂等操作。

        Raises:
            McpHttpStubNotImplementedError: HTTP 模式下调用。
        """
        if self._config.server_type == "http":
            raise McpHttpStubNotImplementedError(
                f"MCP HTTP mode not implemented for server '{self._config.server_id}'"
            )
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
    # MCP Protocol: discover / call
    # --------------------------------------------------------------------------

    async def discover_tools(self) -> list[dict]:
        """调用 MCP tools/list 发现服务器工具列表。

        Returns:
            工具描述 dict 列表(原始 MCP schema 格式)。

        Raises:
            RuntimeError: 未连接时调用。
            McpHttpStubNotImplementedError: HTTP 模式下调用。
        """
        if self._config.server_type == "http":
            raise McpHttpStubNotImplementedError(
                f"MCP HTTP mode not implemented for server '{self._config.server_id}'"
            )
        if not self._connected:
            raise RuntimeError(f"MCP server '{self._config.server_id}' not connected")

        response = await self._send_request({"method": "tools/list"})
        return response.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict:
        """调用 MCP tools/call 执行工具。

        Args:
            name: 工具名称。
            arguments: 工具参数字典。

        Returns:
            MCP 工具调用结果 dict。

        Raises:
            RuntimeError: 未连接时调用。
            McpHttpStubNotImplementedError: HTTP 模式下调用。
        """
        if self._config.server_type == "http":
            raise McpHttpStubNotImplementedError(
                f"MCP HTTP mode not implemented for server '{self._config.server_id}'"
            )
        if not self._connected:
            raise RuntimeError(f"MCP server '{self._config.server_id}' not connected")

        response = await self._send_request({
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        return response.get("result", {})

    # --------------------------------------------------------------------------
    # Internal: JSON-RPC communication
    # --------------------------------------------------------------------------

    async def _send_request(self, msg: dict) -> dict:
        """发送 JSON-RPC 请求并等待响应。

        Args:
            msg: 请求体(method/params)。

        Returns:
            完整 JSON-RPC 响应 dict。

        Raises:
            asyncio.TimeoutError: 超时未收到响应。
        """
        self._request_id += 1
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