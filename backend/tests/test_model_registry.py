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

    async def chat(self, messages, tools=None):
        return ChatResult(content="stub", tool_calls=[], used_provider="stub")


def test_register_adapter_stores_by_name():
    """register_adapter(name, factory) 后 get_adapter(name) 返回 factory 产出的 adapter。"""
    stub = _StubAdapter()
    register_adapter("test_stub", lambda cfg: stub)

    result = get_adapter("test_stub")
    assert result is stub


def test_get_adapter_unknown_raises_key_error():
    """get_adapter("unknown") 抛 KeyError。"""
    raised = False
    try:
        get_adapter("__nonexistent_provider__")
    except KeyError:
        raised = True
    assert raised, "未注册的 provider 名应抛 KeyError"


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


def test_manual_router_select_unknown_raises():
    """select('unknown') 抛 KeyError。"""
    cfg = _make_cfg()
    router = ManualRouter(cfg)
    raised = False
    try:
        router.select("__nonexistent_provider__")
    except KeyError:
        raised = True
    assert raised, "select 未注册的 provider 应抛 KeyError"
