"""蓝图 §2.7 模型适配器子包。

提供 OpenAI 兼容 HTTP 调用的共享基类 + 三家具体适配器。
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from private_agent.models.base import (
    ChatResult,
    ModelAdapter,
    ModelCapability,
    ProviderError,
)

logger = logging.getLogger(__name__)


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
        # V1.5:推理模型(reasoning)复杂请求思考时间可能远超 httpx 默认 5s,
        # 放宽读超时至 120s,避免 ReadTimeout 误杀正常推理(连接 15s)
        self._timeout = httpx.Timeout(120.0, connect=15.0)
        # 注入 client(测试用 MockTransport);默认新建 AsyncClient
        self._client = client if client is not None else httpx.AsyncClient(
            timeout=self._timeout
        )

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
        if max_tokens:
            body["max_tokens"] = max_tokens
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        # V1.5:httpx AsyncClient 长连接可能被上游服务端断开(keep-alive 超时),
        # 复用失效连接会报空消息 http error 且 httpx 不自动重连。
        # 首次 http error 时重建 client 重试一次,保证对话链路稳定。
        resp: httpx.Response | None = None
        for attempt in range(2):
            try:
                resp = await self._client.post(url, json=body, headers=headers)
                break
            except httpx.HTTPError as e:
                logger.warning(
                    "provider %s http error attempt=%d: %r",
                    self.provider_name, attempt, e,
                )
                if attempt == 0:
                    try:
                        await self._client.aclose()
                    except Exception:
                        pass
                    self._client = httpx.AsyncClient(timeout=self._timeout)
                    continue
                raise ProviderError(self.provider_name, f"http error: {e}") from e
        assert resp is not None
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

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        on_delta: Any | None = None,
    ) -> ChatResult:
        """OpenAI 兼容流式 chat: 边收边回调 on_delta(delta 文本), 返回完整 ChatResult。

        - body 带 stream: true + stream_options.include_usage
        - 累积 content(或 reasoning_content)增量与 tool_calls 分片(index 合并)
        - on_delta: async (text: str) -> None, 每段增量回调(用于 WS 推送 delta 事件)

        Raises:
            ProviderError: 非 2xx 响应或连接失败。
        """
        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools
        if max_tokens:
            body["max_tokens"] = max_tokens
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        content_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}

        async with self._client.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code >= 400:
                text = (await resp.aread()).decode("utf-8", "ignore")
                raise ProviderError(
                    self.provider_name,
                    f"upstream {resp.status_code}: {text[:200]}",
                )
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {}) or {}
                text = delta.get("content") or delta.get("reasoning_content") or ""
                if text:
                    content_parts.append(text)
                    if on_delta is not None:
                        await on_delta(text)
                # 流式 tool_calls 分片: 按 index 合并 id/name/arguments
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    acc = tool_calls_acc.setdefault(idx, {
                        "id": "", "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                    if tc.get("id"):
                        acc["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        acc["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        acc["function"]["arguments"] += fn["arguments"]

        tool_calls = [v for _, v in sorted(tool_calls_acc.items())]
        return ChatResult(
            content="".join(content_parts),
            tool_calls=tool_calls,
            used_provider=self.provider_name,
            failed_providers=[],
        )

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
