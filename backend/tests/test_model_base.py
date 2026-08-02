"""M1 Phase 2 Behavior 1 - models/base.py ModelAdapter Protocol + ModelCapability + FallbackChain.

Source: plan/m1-react-loop step 7 (蓝图 §2.7 模型适配器 + §2.9 fallback_chain)
"""
import asyncio

from private_agent.models.base import (
    AllProvidersFailedError,
    ChatResult,
    FallbackChain,
    ModelAdapter,
    ModelCapability,
    ProviderError,
)


# ──────────────────────────────────────────────────────────────────────────────
# ModelCapability dataclass
# ──────────────────────────────────────────────────────────────────────────────


def test_model_capability_has_fields():
    """ModelCapability 含 streaming/function_calling/vision/json_mode 四个字段。"""
    cap = ModelCapability(
        streaming=True,
        function_calling=True,
        vision=False,
        json_mode=True,
    )
    assert cap.streaming is True
    assert cap.function_calling is True
    assert cap.vision is False
    assert cap.json_mode is True


# ──────────────────────────────────────────────────────────────────────────────
# ModelAdapter Protocol (runtime_checkable)
# ──────────────────────────────────────────────────────────────────────────────


class _DummyAdapter:
    """实现 ModelAdapter Protocol 的最小可调用对象(测试用)。"""

    provider_name = "dummy"
    capability = ModelCapability(
        streaming=True, function_calling=True, vision=False, json_mode=True
    )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        return ChatResult(
            content="ok",
            tool_calls=[],
            used_provider=self.provider_name,
            failed_providers=[],
        )


def test_model_adapter_protocol_accepts_implementor():
    """任意类实现 async chat() + provider_name + capability 可被识别为 ModelAdapter。"""
    adapter = _DummyAdapter()
    assert isinstance(adapter, ModelAdapter), (
        "实现 ModelAdapter Protocol 的实例应通过 runtime_checkable isinstance 检查"
    )


# ──────────────────────────────────────────────────────────────────────────────
# FallbackChain
# ──────────────────────────────────────────────────────────────────────────────


class _StubAdapter:
    """按预设行为构造的 stub adapter。"""

    def __init__(self, name: str, behavior: str = "ok", content: str = ""):
        self.provider_name = name
        self.behavior = behavior
        self.content = content or f"reply-from-{name}"
        self.capability = ModelCapability(
            streaming=True, function_calling=True, vision=False, json_mode=True
        )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        if self.behavior == "fail":
            raise ProviderError(self.provider_name, "mock failure")
        return ChatResult(
            content=self.content,
            tool_calls=[],
            used_provider=self.provider_name,
            failed_providers=[],
        )


def test_fallback_chain_tries_providers_in_order():
    """a 失败 → 尝试 b → b 成功返回 b 的结果。"""
    a = _StubAdapter("a", behavior="fail")
    b = _StubAdapter("b", behavior="ok", content="b-wins")
    c = _StubAdapter("c", behavior="ok")
    chain = FallbackChain([a, b, c])

    result = asyncio.run(chain.chat(messages=[{"role": "user", "content": "hi"}]))

    assert result.used_provider == "b"
    assert result.content == "b-wins"


def test_fallback_chain_all_fail_raises_all_providers_failed():
    """全部抛 ProviderError 时抛 AllProvidersFailedError。"""
    a = _StubAdapter("a", behavior="fail")
    b = _StubAdapter("b", behavior="fail")
    c = _StubAdapter("c", behavior="fail")
    chain = FallbackChain([a, b, c])

    raised = False
    try:
        asyncio.run(chain.chat(messages=[{"role": "user", "content": "hi"}]))
    except AllProvidersFailedError:
        raised = True
    assert raised, "全部 provider 失败时应抛 AllProvidersFailedError"


def test_fallback_chain_records_fallback_event():
    """降级时记录哪个 provider 失败、切换到哪个(返回 ChatResult 含 failed_providers)。"""
    a = _StubAdapter("a", behavior="fail")
    b = _StubAdapter("b", behavior="ok")
    chain = FallbackChain([a, b])

    result = asyncio.run(chain.chat(messages=[{"role": "user", "content": "hi"}]))

    assert result.used_provider == "b"
    assert result.failed_providers == ["a"], (
        f"failed_providers 应记录 ['a'],实际 {result.failed_providers}"
    )
