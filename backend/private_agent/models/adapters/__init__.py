"""蓝图 §2.7 模型适配器子包。

提供 OpenAI 兼容 HTTP 调用的共享基类 + 三家具体适配器。
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from private_agent.models.base import (
    ChatResult,
    ModelAdapter,
    ModelCapability,
    ProviderError,
)


class OpenAICompatibleAdapter(ModelAdapter):
    """OpenAI 兼容 /chat/completions 端点的通用基类。

    子类需设置:
    - provider_name: str
    - capability: ModelCapability
    - default_model_name: str
    """

    provider_name: str = ""
    capability: ModelCapability = ModelCapability(
        streaming=False, function_calling=False, vision=False, json_mode=False
    )
    default_model_name: str = ""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name or self.default_model_name
        # 注入 client(测试用 MockTransport);默认新建 AsyncClient
        self._client = client if client is not None else httpx.AsyncClient()

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            resp = await self._client.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderError(self.provider_name, f"http error: {e}") from e
        if resp.status_code >= 500:
            raise ProviderError(
                self.provider_name,
                f"upstream {resp.status_code}: {resp.text[:200]}",
            )
        if resp.status_code >= 400:
            raise ProviderError(
                self.provider_name,
                f"upstream {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        return self._parse_openai_response(data)

    def _parse_openai_response(self, data: dict) -> ChatResult:
        choices = data.get("choices") or []
        message = choices[0]["message"] if choices else {}
        content = message.get("content") or ""
        # 纯推理模型(如 deepseek-v4-pro)content 恒为空,输出在 reasoning_content
        if not content:
            content = message.get("reasoning_content") or ""
        tool_calls = message.get("tool_calls") or []
        # tool_calls 透传 OpenAI 结构:{id, type, function:{name, arguments}}
        return ChatResult(
            content=content,
            tool_calls=list(tool_calls),
            used_provider=self.provider_name,
            failed_providers=[],
        )
