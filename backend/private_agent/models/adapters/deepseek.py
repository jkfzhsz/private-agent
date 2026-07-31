"""蓝图 §2.7 DeepSeek 适配器(OpenAI 兼容)。"""
from __future__ import annotations

from private_agent.models.adapters import OpenAICompatibleAdapter
from private_agent.models.base import ModelCapability


class DeepSeekAdapter(OpenAICompatibleAdapter):
    """deepseek-chat 适配器(provider_name='deepseek')。

    capability:streaming=T / function_calling=T / vision=F / json_mode=T
    """

    provider_name = "deepseek"
    capability = ModelCapability(
        streaming=True, function_calling=True, vision=False, json_mode=True
    )
    default_model_name = "deepseek-chat"
