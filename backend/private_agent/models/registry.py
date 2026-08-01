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


def build_compress_adapter(cfg: dict) -> ModelAdapter | None:
    """按 cfg['models']['compress_model'] 构造单 GLM 压缩适配器(蓝图 §4.2,spec AC-7)。

    compress_model 当前仅支持 glm 系列(如 'glm-4-flash')。
    复用 glm provider 的 base_url + env api_key,但 model_name 用 compress_model 覆盖。

    Returns:
        GlmAdapter 实例;provider disabled 或无 compress_model 配置时返回 None。
    """
    models_cfg = cfg.get("models", {})
    compress_model = models_cfg.get("compress_model")
    if not compress_model:
        return None
    prov = models_cfg.get("providers", {}).get("glm", {})
    if not prov.get("enabled", True):
        return None
    api_key = os.environ.get("PA_GLM_API_KEY", "test-key")
    return GlmAdapter(
        base_url=prov.get("base_url", ""),
        api_key=api_key,
        model_name=compress_model,
    )


class ManualRouter:
    """蓝图 §2.9 manual router(MVP 简化版:按名直选,不走 tag 协商)。

    M2 扩展:select_by_tag 支持基于 MCP Server 标签的筛选。
    """

    def __init__(self, cfg: dict):
        self._cfg = cfg

    def select(self, provider_name: str) -> ModelAdapter:
        """返回指定 provider 的 adapter。

        Raises:
            KeyError: provider_name 未注册或 cfg 中缺失。
        """
        return get_adapter(provider_name, self._cfg)

    def select_by_tag(self, tag_name: str, mcp_clients: list) -> list:
        """AC-5: 基于 MCP Server 标签筛选候选客户端。

        返回 tags 列表包含指定 tag_name 的 MCPClient 实例列表。
        仅支持单标签精确匹配(方案 B:用户标签路由)。

        Args:
            tag_name: 要匹配的标签名称。
            mcp_clients: MCPClient 实例列表。

        Returns:
            匹配标签的 MCPClient 实例列表。
        """
        if not tag_name:
            return []
        return [c for c in mcp_clients if tag_name in c.config.tags]


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