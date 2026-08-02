"""蓝图 §2.7/§2.9 - provider 注册表 + ManualRouter(V2 P3 去预置化)。

Source: plan/m1-react-loop step 9
- _REGISTRY: provider 名 → factory(cfg) -> ModelAdapter
- register_adapter / get_adapter / build_fallback_chain
- 不预置任何 provider: 全量运行时动态注册(设置页添加, OpenAI 兼容)
- API Key 从环境变量 PA_{NAME}_API_KEY 读(蓝图 §2.7),默认 "test-key"(测试用)
"""
from __future__ import annotations

import os
from typing import Callable

from private_agent.models.base import FallbackChain, ModelAdapter

# provider 名 → 构造工厂 factory(cfg) -> ModelAdapter
_REGISTRY: dict[str, Callable[[dict], ModelAdapter]] = {}


def register_adapter(
    name: str, factory: Callable[[dict], ModelAdapter]
) -> None:
    """注册 provider 构造工厂。"""
    _REGISTRY[name] = factory


def ensure_registered(name: str) -> None:
    """确保 provider 已注册;未注册的按 OpenAI 兼容动态注册(设置页可新增任意模型)。

    新增 provider 只配置了 base_url/model_name/api_key, 无专用 adapter 类,
    统一走 OpenAICompatibleAdapter(绝大多数云厂商兼容 OpenAI 协议)。
    """
    if name in _REGISTRY:
        return
    from private_agent.models.adapters import OpenAICompatibleAdapter

    register_adapter(name, _make_factory(name, OpenAICompatibleAdapter))


def get_adapter(name: str, cfg: dict | None = None) -> ModelAdapter:
    """从 registry 取 factory,用 cfg 构造 adapter。

    Args:
        name: provider 名(glm/deepseek/kimi/...)。
        cfg: 配置 dict(用于读取 base_url/model_name 等)。

    Raises:
        KeyError: name 未注册。
    """
    ensure_registered(name)
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


def build_adapter_for_model_name(
    cfg: dict, model_name: str
) -> ModelAdapter | None:
    """按模型名(非 provider 名)匹配 provider 并构造 OpenAI 兼容 adapter。

    V2 P3 去预置化: compress_model / judge_model 等按"模型名"配置,
    遍历 providers 找 model_name 相等的 enabled provider 动态注册构造。
    无匹配 → None(优雅降级, 压缩/评测功能禁用而非崩溃)。

    Args:
        cfg: 配置 dict(含 models.providers)。
        model_name: 目标模型名(如 "deepseek-v4-flash-0731")。

    Returns:
        ModelAdapter;无匹配/未配置时 None。
    """
    if not model_name:
        return None
    providers = cfg.get("models", {}).get("providers", {})
    for name, prov in providers.items():
        if not prov.get("enabled", True):
            continue
        if prov.get("model_name") == model_name:
            return get_adapter(name, cfg)
    return None


def build_compress_adapter(cfg: dict) -> ModelAdapter | None:
    """按 cfg['models']['compress_model'] 构造压缩适配器(蓝图 §4.2,spec AC-7)。

    V2 P3: 不再硬编码 glm/PA_GLM_API_KEY, 按 compress_model(model 名)匹配
    任意 provider。无匹配(未配置/模型不存在) → None, 压缩优雅降级。

    Returns:
        ModelAdapter 实例;无匹配配置时返回 None。
    """
    compress_model = cfg.get("models", {}).get("compress_model")
    if not compress_model:
        return None
    return build_adapter_for_model_name(cfg, compress_model)


def build_default_adapter(cfg: dict) -> ModelAdapter | None:
    """B1 P1-10: 返回 fallback chain 的首个 adapter(供 EvalRunner 等单 adapter 场景使用)。

    Returns:
        FallbackChain 链首 adapter;chain 为空(所有 provider disabled)时返回 None。
    """
    chain = build_fallback_chain(cfg)
    return chain._adapters[0] if chain._adapters else None


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
# provider factory(V2 P3: 不预置任何 provider, 全量动态注册)
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
            provider_name=name,
        )

    return factory