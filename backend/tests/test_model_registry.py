"""M1 Phase 2 Behavior 3 - models/registry.py provider 注册 + ManualRouter。

Source: plan/m1-react-loop step 9 (蓝图 §2.7 + §2.9)
- register_adapter / get_adapter / build_fallback_chain
- ManualRouter.select(name) 返回指定 provider 的 adapter
- 启动时自动注册 glm/deepseek/kimi
"""
import asyncio

from private_agent.models.base import (
    ChatResult,
    FallbackChain,
    ModelAdapter,
    ModelCapability,
)
from private_agent.models.registry import (
    ManualRouter,
    build_fallback_chain,
    get_adapter,
    register_adapter,
)


def _make_cfg(fallback_chain=None, overrides=None):
    """构造测试用 cfg(模拟 config.yaml 的 models 段)。"""
    providers = {
        "glm": {
            "base_url": "http://glm.test",
            "model_name": "glm-4",
            "enabled": True,
        },
        "deepseek": {
            "base_url": "http://ds.test",
            "model_name": "deepseek-chat",
            "enabled": True,
        },
        "agnes": {
            "base_url": "http://agnes.test",
            "model_name": "agnes-1",
            "enabled": False,
        },
        "kimi": {
            "base_url": "http://kimi.test",
            "model_name": "moonshot-v1-8k",
            "enabled": True,
        },
    }
    if overrides:
        for name, patch in overrides.items():
            providers[name].update(patch)
    if fallback_chain is None:
        fallback_chain = ["glm", "deepseek", "agnes", "kimi"]
    return {
        "models": {
            "providers": providers,
            "router": {
                "type": "manual",
                "fallback_chain": fallback_chain,
            },
        }
    }


# ──────────────────────────────────────────────────────────────────────────────
# register_adapter / get_adapter
# ──────────────────────────────────────────────────────────────────────────────


class _StubAdapter:
    """register/get roundtrip 测试用的 stub。"""

    provider_name = "stub"
    capability = ModelCapability(
        streaming=True, function_calling=True, vision=False, json_mode=True
    )

    async def chat(self, messages, tools=None, **kwargs):
        return ChatResult(content="stub", tool_calls=[], used_provider="stub")


def test_register_adapter_stores_by_name():
    """register_adapter(name, factory) 后 get_adapter(name) 返回 factory 产出的 adapter。"""
    stub = _StubAdapter()
    register_adapter("test_stub", lambda cfg: stub)

    result = get_adapter("test_stub")
    assert result is stub


def test_get_adapter_unknown_dynamically_registers():
    """未注册的 provider 名 → ensure_registered 动态注册为 OpenAICompatibleAdapter。

    (39f11f9/1e0af49 动态注册后不再抛 KeyError —— 开放式接入的基础行为)
    """
    from private_agent.models.adapters import OpenAICompatibleAdapter

    adapter = get_adapter("__nonexistent_provider__")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.provider_name == "__nonexistent_provider__"


# ──────────────────────────────────────────────────────────────────────────────
# build_fallback_chain
# ──────────────────────────────────────────────────────────────────────────────


def test_build_fallback_chain_from_config():
    """build_fallback_chain(cfg) 按 fallback_chain 顺序构造,返回 FallbackChain。"""
    cfg = _make_cfg(fallback_chain=["glm", "deepseek", "kimi"])
    chain = build_fallback_chain(cfg)
    assert isinstance(chain, FallbackChain)
    # 按 fallback_chain 顺序,3 个 enabled provider
    names = [a.provider_name for a in chain._adapters]
    assert names == ["glm", "deepseek", "kimi"]


def test_build_fallback_chain_skips_disabled():
    """agnes enabled=false → build_fallback_chain 不含 agnes。"""
    cfg = _make_cfg(fallback_chain=["glm", "deepseek", "agnes", "kimi"])
    chain = build_fallback_chain(cfg)
    names = [a.provider_name for a in chain._adapters]
    assert "agnes" not in names
    assert names == ["glm", "deepseek", "kimi"]


# ──────────────────────────────────────────────────────────────────────────────
# ManualRouter
# ──────────────────────────────────────────────────────────────────────────────


def test_manual_router_select_returns_adapter():
    """ManualRouter(cfg).select('glm') 返回 glm adapter(provider_name='glm')。"""
    cfg = _make_cfg()
    router = ManualRouter(cfg)
    adapter = router.select("glm")
    assert isinstance(adapter, ModelAdapter)
    assert adapter.provider_name == "glm"


def test_manual_router_select_unknown_dynamically_registers():
    """select('unknown') → 动态注册并返回 adapter(不再抛 KeyError)。"""
    from private_agent.models.adapters import OpenAICompatibleAdapter

    cfg = _make_cfg()
    router = ManualRouter(cfg)
    adapter = router.select("__nonexistent_provider__")
    assert isinstance(adapter, OpenAICompatibleAdapter)
    assert adapter.provider_name == "__nonexistent_provider__"
