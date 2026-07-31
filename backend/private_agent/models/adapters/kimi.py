"""蓝图 §2.7 Kimi 适配器(moonshot,OpenAI 兼容)。"""
from __future__ import annotations

from private_agent.models.adapters import OpenAICompatibleAdapter
from private_agent.models.base import ModelCapability


class KimiAdapter(OpenAICompatibleAdapter):
    """moonshot-v1-8k 适配器(provider_name='kimi')。

    capability:streaming=T / function_calling=T / vision=T / json_mode=F
    """

    provider_name = "kimi"
    capability = ModelCapability(
        streaming=True, function_calling=True, vision=True, json_mode=False
    )
    default_model_name = "moonshot-v1-8k"
