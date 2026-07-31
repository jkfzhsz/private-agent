"""http_request 内置工具:httpx GET/POST 封装。

支持 GET 和 POST 请求，返回响应文本。
"""
from __future__ import annotations

import httpx

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["http_request_handler", "HTTP_REQUEST_TOOL"]

_SUPPORTED_METHODS = {"GET", "POST"}


async def http_request_handler(args: dict) -> ToolResult:
    """执行 HTTP 请求。

    Args:
        args: 包含 url、method、body(可选)的 dict。

    Returns:
        响应文本或错误信息。
    """
    url = args.get("url", "")
    method = args.get("method", "GET").upper()
    body = args.get("body", None)

    if not url:
        return ToolResult(output="", error="No URL provided")
    if method not in _SUPPORTED_METHODS:
        return ToolResult(output="", error=f"Unsupported method: {method}. Supported: {sorted(_SUPPORTED_METHODS)}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method == "GET":
                response = await client.get(url)
            else:
                response = await client.post(url, content=body)
            response.raise_for_status()
            return ToolResult(
                output=response.text,
                metadata={"status_code": response.status_code},
            )
    except Exception as e:
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")


HTTP_REQUEST_TOOL = ToolDef(
    name="http_request",
    description="Make an HTTP GET or POST request to a URL. Returns the response body text.",
    parameters_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to send the request to.",
            },
            "method": {
                "type": "string",
                "description": "HTTP method: GET or POST. Defaults to GET.",
                "enum": ["GET", "POST"],
            },
            "body": {
                "type": "string",
                "description": "Request body for POST requests (optional).",
            },
        },
        "required": ["url"],
    },
    handler=http_request_handler,
)