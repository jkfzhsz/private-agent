"""M1 Phase 2 Behavior 2 - models/adapters/{glm,deepseek,kimi}.py 三家 mock 适配器。

Source: plan/m1-react-loop step 8 (蓝图 §2.7)
- 用 httpx.MockTransport mock HTTP,不依赖真实 API
- OpenAI 兼容响应:choices[0].message.content + tool_calls
"""
import asyncio
import json

import httpx

from private_agent.models.adapters.deepseek import DeepSeekAdapter
from private_agent.models.adapters.glm import GlmAdapter
from private_agent.models.adapters.kimi import KimiAdapter
from private_agent.models.base import (
    ChatResult,
    ModelAdapter,
    ModelCapability,
    ProviderError,
)


# ──────────────────────────────────────────────────────────────────────────────
# capability 正确性
# ──────────────────────────────────────────────────────────────────────────────


def test_glm_adapter_has_correct_capability():
    """GlmAdapter.capability == (streaming=T, function_calling=T, vision=F, json_mode=T)。"""
    adapter = GlmAdapter(base_url="http://glm.test", api_key="k")
    assert adapter.capability == ModelCapability(
        streaming=True, function_calling=True, vision=False, json_mode=True
    )


def test_deepseek_adapter_has_correct_capability():
    """DeepSeekAdapter.capability 同 glm。"""
    adapter = DeepSeekAdapter(base_url="http://ds.test", api_key="k")
    assert adapter.capability == ModelCapability(
        streaming=True, function_calling=True, vision=False, json_mode=True
    )


def test_kimi_adapter_has_correct_capability():
    """KimiAdapter.capability == (streaming=T, function_calling=T, vision=T, json_mode=F)。"""
    adapter = KimiAdapter(base_url="http://kimi.test", api_key="k")
    assert adapter.capability == ModelCapability(
        streaming=True, function_calling=True, vision=True, json_mode=False
    )


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


def test_glm_adapter_chat_returns_chat_result_with_provider_name():
    """glm mock 200 → ChatResult.used_provider='glm'。"""
    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": "hello from glm"}}
        ]
    }
    client = _make_mock_client(_openai_ok_handler(payload), "http://glm.test")
    adapter = GlmAdapter(base_url="http://glm.test", api_key="k", client=client)

    result = asyncio.run(adapter.chat(messages=[{"role": "user", "content": "hi"}]))

    assert isinstance(result, ChatResult)
    assert result.used_provider == "glm"
    assert result.content == "hello from glm"


def test_glm_adapter_chat_503_raises_provider_error():
    """glm mock 503 → 抛 ProviderError(provider='glm')。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "service unavailable"})

    client = _make_mock_client(handler, "http://glm.test")
    adapter = GlmAdapter(base_url="http://glm.test", api_key="k", client=client)

    raised = False
    try:
        asyncio.run(adapter.chat(messages=[{"role": "user", "content": "hi"}]))
    except ProviderError as e:
        raised = True
        assert e.provider == "glm"
    assert raised, "503 应抛 ProviderError"


def test_deepseek_adapter_chat_parses_tool_calls():
    """deepseek mock 返回含 tool_calls → ChatResult.tool_calls 非空。"""
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
    client = _make_mock_client(_openai_ok_handler(payload), "http://ds.test")
    adapter = DeepSeekAdapter(base_url="http://ds.test", api_key="k", client=client)

    result = asyncio.run(adapter.chat(messages=[{"role": "user", "content": "hi"}]))

    assert result.used_provider == "deepseek"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["function"]["name"] == "echo"


def test_kimi_adapter_chat_returns_content():
    """kimi mock 200 → ChatResult.content 非空。"""
    payload = {
        "choices": [
            {"message": {"role": "assistant", "content": "kimi says hi"}}
        ]
    }
    client = _make_mock_client(_openai_ok_handler(payload), "http://kimi.test")
    adapter = KimiAdapter(base_url="http://kimi.test", api_key="k", client=client)

    result = asyncio.run(adapter.chat(messages=[{"role": "user", "content": "hi"}]))

    assert result.used_provider == "kimi"
    assert result.content == "kimi says hi"


def test_adapter_uses_injected_client():
    """适配器构造函数接受 client=None,默认用 httpx.AsyncClient;测试可注入 mock client。"""
    # 默认构造不报错(用真实 AsyncClient,不发起请求)
    default_adapter = GlmAdapter(base_url="http://glm.test", api_key="k")
    assert default_adapter.capability is not None
    assert isinstance(default_adapter, ModelAdapter)

    # 注入 mock client
    payload = {"choices": [{"message": {"role": "assistant", "content": "x"}}]}
    mock_client = _make_mock_client(_openai_ok_handler(payload), "http://glm.test")
    injected_adapter = GlmAdapter(
        base_url="http://glm.test", api_key="k", client=mock_client
    )
    result = asyncio.run(injected_adapter.chat(messages=[{"role": "user", "content": "hi"}]))
    assert result.content == "x"
