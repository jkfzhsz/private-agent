"""蓝图 §2.7 GLM 适配器(zhipu bigmodel,OpenAI 兼容)。"""
from __future__ import annotations

from private_agent.models.adapters import OpenAICompatibleAdapter
from private_agent.models.base import ModelCapability


class GlmAdapter(OpenAICompatibleAdapter):
    """GLM-4 适配器(provider_name='glm')。

    capability:streaming=T / function_calling=T / vision=F / json_mode=T
    """

    provider_name = "glm"
    capability = ModelCapability(
        streaming=True, function_calling=True, vision=False, json_mode=True
    )
    default_model_name = "glm-4"
