"""蓝图 §2.7/§2.9 - provider 注册表 + ManualRouter。

Source: plan/m1-react-loop step 9
- _REGISTRY: provider 名 → factory(cfg) -> ModelAdapter
- register_adapter / get_adapter / build_fallback_chain
- 启动时自动注册 glm/deepseek/kimi
- API Key 从环境变量 PA_{NAME}_API_KEY 读(蓝图 §2.7),默认 "test-key"(测试用)
"""
from __future__ import annotations

import os
from typing import Callable

from private_agent.models.adapters.deepseek import DeepSeekAdapter
from private_agent.models.adapters.glm import GlmAdapter
from private_agent.models.adapters.kimi import KimiAdapter
from private_agent.models.base import FallbackChain, ModelAdapter

# provider 名 → 构造工厂 factory(cfg) -> ModelAdapter
_REGISTRY: dict[str, Callable[[dict], ModelAdapter]] = {}


def register_adapter(
    name: str, factory: Callable[[dict], ModelAdapter]
) -> None:
    """注册 provider 构造工厂。"""
    _REGISTRY[name] = factory


def get_adapter(name: str, cfg: dict | None = None) -> ModelAdapter:
    """从 registry 取 factory,用 cfg 构造 adapter。

    Args:
        name: provider 名(glm/deepseek/kimi/...)。
        cfg: 配置 dict(用于读取 base_url/model_name 等)。

    Raises:
        KeyError: name 未注册。
    """
    if name not in _REGISTRY:
        raise KeyError(f"provider '{name}' not registered")
    factory = _REGISTRY[name]
    return factory(cfg if cfg is not None else {})


def build_fallback_chain(cfg: dict) -> FallbackChain:
    """按 cfg['models']['router']['fallback_chain'] 顺序构造 FallbackChain。

    跳过 enabled=false 的 provider(蓝图 §2.9)。
    """
    router_cfg = cfg["models"]["router"]
    chain_names = router_cfg.get("fallback_chain", [])
    providers = cfg["models"]["providers"]
    adapters: list[ModelAdapter] = []
    for name in chain_names:
        prov = providers.get(name, {})
        if not prov.get("enabled", True):
            continue
        adapters.append(get_adapter(name, cfg))
    return FallbackChain(adapters)


class ManualRouter:
    """蓝图 §2.9 manual router(MVP 简化版:按名直选,不走 tag 协商)。"""

    def __init__(self, cfg: dict):
        self._cfg = cfg

    def select(self, provider_name: str) -> ModelAdapter:
        """返回指定 provider 的 adapter。

        Raises:
            KeyError: provider_name 未注册或 cfg 中缺失。
        """
        return get_adapter(provider_name, self._cfg)


# ──────────────────────────────────────────────────────────────────────────────
# 启动时自动注册三家 provider(蓝图 §2.7)
# ──────────────────────────────────────────────────────────────────────────────


def _make_factory(
    name: str, adapter_cls: type[ModelAdapter]
) -> Callable[[dict], ModelAdapter]:
    """生成 provider factory:从 cfg 读 base_url/model_name,从 env 读 api_key。"""
    env_var = f"PA_{name.upper()}_API_KEY"

    def factory(cfg: dict) -> ModelAdapter:
        prov = cfg.get("models", {}).get("providers", {}).get(name, {})
        api_key = os.environ.get(env_var, "test-key")
        return adapter_cls(
            base_url=prov.get("base_url", ""),
            api_key=api_key,
            model_name=prov.get("model_name"),
        )

    return factory


register_adapter("glm", _make_factory("glm", GlmAdapter))
register_adapter("deepseek", _make_factory("deepseek", DeepSeekAdapter))
register_adapter("kimi", _make_factory("kimi", KimiAdapter))
