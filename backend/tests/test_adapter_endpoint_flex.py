"""P3 API 端点灵活性回归（2026-09-29）。

覆盖：
- `OpenAICompatibleAdapter` 新增 `chat_path` / `extra_headers` / `extra_body`
  三个可选参数，**默认值与历史行为完全一致（零回归）**；
- `registry._make_factory` 从 provider 配置透传这些字段。

用途：接入非标准路径的兼容网关（私有部署/中转）、需要额外鉴权头的服务、
以及厂商特有请求参数 —— 换厂商不再需要改代码。
"""
import asyncio
import json

import httpx
import pytest

from private_agent.models.adapters import OpenAICompatibleAdapter
from private_agent.models.registry import get_adapter


def _build(**kwargs):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleAdapter(
        base_url=kwargs.pop("base_url", "https://api.example.com/v1"),
        api_key=kwargs.pop("api_key", "sk-test"),
        client=client,
        **kwargs,
    )
    return adapter, captured, client


def _capture(**kwargs):
    adapter, captured, client = _build(**kwargs)

    async def _go():
        try:
            await adapter.chat([{"role": "user", "content": "hi"}])
        finally:
            await client.aclose()

    asyncio.run(_go())
    return captured


def test_default_request_shape_unchanged():
    """零回归: 默认路径 `/chat/completions`, 仅 Authorization 头, body 无多余字段。"""
    captured = _capture(model_name="m-1")

    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    assert captured["headers"].get("x-api-key") is None
    assert captured["body"] == {
        "model": "m-1",
        "messages": [{"role": "user", "content": "hi"}],
    }


def test_custom_chat_path_headers_and_body():
    """自定义路径 / 额外头 / 厂商特有参数全部生效。"""
    captured = _capture(
        model_name="m-1",
        chat_path="/openai/deployments/dep1/chat/completions",
        extra_headers={"X-Api-Key": "custom-key", "X-Tenant": "t1"},
        extra_body={"top_k": 5, "thinking": {"type": "enabled"}},
    )

    assert captured["url"] == (
        "https://api.example.com/v1/openai/deployments/dep1/chat/completions"
    )
    assert captured["headers"]["x-api-key"] == "custom-key"
    assert captured["headers"]["x-tenant"] == "t1"
    assert captured["headers"]["authorization"] == "Bearer sk-test"  # 保留既有头
    assert captured["body"]["top_k"] == 5
    assert captured["body"]["thinking"] == {"type": "enabled"}
    assert captured["body"]["model"] == "m-1"


def test_chat_path_without_leading_slash_is_normalized():
    """`chat_path` 缺前导斜杠时自动补齐(避免拼出 example.com/v1v2/chat)。"""
    captured = _capture(model_name="m-1", chat_path="v2/chat")
    assert captured["url"] == "https://api.example.com/v1/v2/chat"


def test_registry_passes_endpoint_fields_from_cfg(monkeypatch):
    """provider 配置里的新字段应透传到 adapter 实例。"""
    monkeypatch.setenv("PA_GW_API_KEY", "sk-gw")
    cfg = {
        "models": {
            "providers": {
                "gw": {
                    "base_url": "https://gw.example.com/v1",
                    "model_name": "m",
                    "enabled": True,
                    "chat_path": "/custom/chat",
                    "extra_headers": {"X-Tenant": "t1"},
                    "extra_body": {"top_p": 0.9},
                }
            },
            "router": {"type": "manual", "fallback_chain": ["gw"]},
        }
    }
    adapter = get_adapter("gw", cfg)
    assert adapter.chat_path == "/custom/chat"
    assert adapter.extra_headers == {"X-Tenant": "t1"}
    assert adapter.extra_body == {"top_p": 0.9}
    assert adapter.api_key == "sk-gw"


def test_registry_defaults_when_fields_absent(monkeypatch):
    """配置缺省时回落历史默认(零回归)。"""
    monkeypatch.setenv("PA_PLAIN_API_KEY", "sk-plain")
    cfg = {
        "models": {
            "providers": {
                "plain": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "m",
                    "enabled": True,
                }
            },
            "router": {"type": "manual", "fallback_chain": ["plain"]},
        }
    }
    adapter = get_adapter("plain", cfg)
    assert adapter.chat_path == "/chat/completions"
    assert adapter.extra_headers == {}
    assert adapter.extra_body == {}
