"""蓝图 §2.15 models 子包 - 模型适配层公开 API。

M1 Phase 2 导出:
- ModelAdapter / ModelCapability / ChatResult:Protocol 与数据类
- FallbackChain / ProviderError / AllProvidersFailedError:降级执行与异常
- GlmAdapter / DeepSeekAdapter / KimiAdapter:三家具体适配器
- register_adapter / get_adapter / build_fallback_chain:注册表
- ManualRouter:手动路由(MVP)
"""
from private_agent.models.adapters.deepseek import DeepSeekAdapter
from private_agent.models.adapters.glm import GlmAdapter
from private_agent.models.adapters.kimi import KimiAdapter
from private_agent.models.base import (
    AllProvidersFailedError,
    ChatResult,
    FallbackChain,
    ModelAdapter,
    ModelCapability,
    ProviderError,
)
from private_agent.models.registry import (
    ManualRouter,
    build_fallback_chain,
    get_adapter,
    register_adapter,
)

__all__ = [
    "ModelAdapter",
    "ModelCapability",
    "ChatResult",
    "FallbackChain",
    "ProviderError",
    "AllProvidersFailedError",
    "GlmAdapter",
    "DeepSeekAdapter",
    "KimiAdapter",
    "register_adapter",
    "get_adapter",
    "build_fallback_chain",
    "ManualRouter",
]
