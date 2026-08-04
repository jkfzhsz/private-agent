"""http_request 内置工具:httpx GET/POST 封装(阶段二批次 2 起带 SSRF 防护)。

支持 GET 和 POST 请求,返回响应文本。
SSRF 防护(审查 A.2.3/B.1.3): 内网/回环/云元数据/重定向逃逸/scheme 非法全部拒绝。
"""
from __future__ import annotations

from private_agent.security.ssrf import SSRFBlockedError, safe_httpx_client
from private_agent.tools.defs import ToolDef, ToolResult

__all__ = ["http_request_handler", "HTTP_REQUEST_TOOL", "set_security_config"]

_SUPPORTED_METHODS = {"GET", "POST"}

# 模块级安全配置(应用启动时由 main.py 注入, 同 code_execution._sandbox_config 模式)
_security_config: dict | None = None


def set_security_config(config: dict) -> None:
    """设置安全配置(应用启动时调用, 供 SSRF 校验读取)。"""
    global _security_config
    _security_config = config


def _read_ssrf_cfg() -> dict:
    """读取 SSRF 配置(模块级 > args 注入 > 默认)。"""
    cfg = _security_config or {}
    ssrf = cfg.get("security", {}).get("ssrf", {}) if isinstance(cfg, dict) else {}
    return ssrf


async def http_request_handler(args: dict) -> ToolResult:
    """执行 HTTP 请求(带 SSRF 防护)。

    Args:
        args: 包含 url、method、body(可选)的 dict。
        _ssrf_config: 测试用配置注入(可选)。

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

    # 测试注入优先级高于模块级
    ssrf_cfg = args.get("_ssrf_config") or _read_ssrf_cfg()
    enabled = ssrf_cfg.get("enabled", True)
    allow_private = bool(ssrf_cfg.get("allow_private", False))
    max_bytes = int(ssrf_cfg.get("max_response_bytes", 2 * 1024 * 1024))

    try:
        if enabled:
            async with safe_httpx_client(
                max_response_bytes=max_bytes,
                allow_private=allow_private,
            ) as client:
                resp = await client.request(method, url, content=body)
                resp.raise_for_status()
                raw = await client.limited_content(resp)
                text = raw.decode("utf-8", errors="replace")
            return ToolResult(
                output=text,
                metadata={"status_code": resp.status_code},
            )
        # 显式关闭防护(不推荐): 保持原行为
        import httpx

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            if method == "GET":
                response = await client.get(url)
            else:
                response = await client.post(url, content=body)
            response.raise_for_status()
            return ToolResult(
                output=response.text,
                metadata={"status_code": response.status_code},
            )
    except SSRFBlockedError as e:
        return ToolResult(
            output="",
            error=f"SSRF blocked: {e}",
            metadata={"blocked": True},
        )
    except Exception as e:
        return ToolResult(output="", error=f"{type(e).__name__}: {e}")


HTTP_REQUEST_TOOL = ToolDef(
    name="http_request",
    description="Make an HTTP GET or POST request to a public URL. Returns the response body text. "
    "Blocked: private/internal network addresses, cloud metadata endpoints, non-http(s) schemes.",
    parameters_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to send the request to (http/https, public only).",
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
