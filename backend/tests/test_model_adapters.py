"""M1 Phase 2 Behavior 2 / V2 P3 - OpenAICompatibleAdapter 通用适配器测试。

V2 P3 去预置化: 删除 glm/deepseek/kimi 专用 adapter 类, 统一 OpenAICompatibleAdapter
(动态注册时传 provider_name)。本文件用通用类 + provider_name 覆盖原三家行为测试。
"""
import asyncio
import json

import httpx

from private_agent.models.adapters import OpenAICompatibleAdapter
from private_agent.models.base import (
    ChatResult,
    ModelAdapter,
    ModelCapability,
    ProviderError,
)


# ──────────────────────────────────────────────────────────────────────────────
# capability 正确性
# ──────────────────────────────────────────────────────────────────────────────


def test_openai_adapter_has_correct_capability():
    """OpenAICompatibleAdapter.capability == (streaming=T, function_calling=T)。"""
    adapter = OpenAICompatibleAdapter(base_url="http://glm.test", api_key="k")
    assert adapter.capability == ModelCapability(
        streaming=True, function_calling=True, vision=False, json_mode=False
    )


def test_provider_name_override():
    """动态注册传入 provider_name → 覆盖类默认值。"""
    adapter = OpenAICompatibleAdapter(
        base_url="http://x.test", api_key="k", provider_name="my-llm"
    )
    assert adapter.provider_name == "my-llm"


# ──────────────────────────────────────────────────────────────────────────────
# chat() 行为:用 httpx.MockTransport 注入 mock client
# ──────────────────────────────────────────────────────────────────────────────


def _make_mock_client(handler, base_url: str) -> httpx.AsyncClient:
    """构造带 MockTransport 的 AsyncClient,base_url 锁定前缀。"""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(base_url=base_url, transport=transport)


def _openai_ok_handler(payload: dict):
    """返回 200 + payload 的 handler 工厂。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)
    return handler


def test_adapter_chat_returns_chat_result_with_provider_name():
    """mock 200 → ChatResult.used_provider=provider_name。"""
    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": "hello from model"}}
        ]
    }
    client = _make_mock_client(_openai_ok_handler(payload), "http://m.test")
    adapter = OpenAICompatibleAdapter(
        base_url="http://m.test", api_key="k", client=client, provider_name="m"
    )

    result = asyncio.run(adapter.chat(messages=[{"role": "user", "content": "hi"}]))

    assert isinstance(result, ChatResult)
    assert result.used_provider == "m"
    assert result.content == "hello from model"


def test_adapter_chat_503_raises_provider_error():
    """mock 503 → 抛 ProviderError(provider=provider_name)。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "service unavailable"})

    client = _make_mock_client(handler, "http://m.test")
    adapter = OpenAICompatibleAdapter(
        base_url="http://m.test", api_key="k", client=client, provider_name="m"
    )

    raised = False
    try:
        asyncio.run(adapter.chat(messages=[{"role": "user", "content": "hi"}]))
    except ProviderError as e:
        raised = True
        assert e.provider == "m"
    assert raised, "503 应抛 ProviderError"


def test_adapter_chat_parses_tool_calls():
    """mock 返回含 tool_calls → ChatResult.tool_calls 非空。"""
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "arguments": json.dumps({"text": "hi"}),
                            },
                        }
                    ],
                }
            }
        ]
    }
    client = _make_mock_client(_openai_ok_handler(payload), "http://m.test")
    adapter = OpenAICompatibleAdapter(
        base_url="http://m.test", api_key="k", client=client, provider_name="m"
    )

    result = asyncio.run(adapter.chat(messages=[{"role": "user", "content": "hi"}]))

    assert result.used_provider == "m"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["function"]["name"] == "echo"


def test_adapter_chat_falls_back_to_reasoning_content():
    """推理模型 content 为空时回落 reasoning_content。"""
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "思维链...最终回复:收到",
                }
            }
        ]
    }
    client = _make_mock_client(_openai_ok_handler(payload), "http://m.test")
    adapter = OpenAICompatibleAdapter(
        base_url="http://m.test", api_key="k", client=client, provider_name="m"
    )

    result = asyncio.run(adapter.chat(messages=[{"role": "user", "content": "hi"}]))

    assert result.used_provider == "m"
    assert result.content == "思维链...最终回复:收到"


def test_adapter_chat_ignores_reasoning_when_content_present():
    """content 非空时 reasoning_content 不参与结果(正常模型不受影响)。"""
    payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "正常回复",
                    "reasoning_content": "不应被使用",
                }
            }
        ]
    }
    client = _make_mock_client(_openai_ok_handler(payload), "http://m.test")
    adapter = OpenAICompatibleAdapter(
        base_url="http://m.test", api_key="k", client=client, provider_name="m"
    )

    result = asyncio.run(adapter.chat(messages=[{"role": "user", "content": "hi"}]))

    assert result.content == "正常回复"


def test_adapter_uses_injected_client():
    """构造函数接受 client=None,默认用 httpx.AsyncClient;测试可注入 mock client。"""
    default_adapter = OpenAICompatibleAdapter(base_url="http://m.test", api_key="k")
    assert default_adapter.capability is not None
    assert isinstance(default_adapter, ModelAdapter)

    payload = {"choices": [{"message": {"role": "assistant", "content": "x"}}]}
    mock_client = _make_mock_client(_openai_ok_handler(payload), "http://m.test")
    injected_adapter = OpenAICompatibleAdapter(
        base_url="http://m.test", api_key="k", client=mock_client
    )
    result = asyncio.run(injected_adapter.chat(messages=[{"role": "user", "content": "hi"}]))
    assert result.content == "x"
