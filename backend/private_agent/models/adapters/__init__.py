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
    TokenUsage,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleAdapter(ModelAdapter):
    @staticmethod
    def _extract_usage(data: dict | None) -> TokenUsage | None:
        """从 OpenAI 兼容响应 usage 提取 TokenUsage(兼容缓存字段差异)。

        - OpenAI 标准: prompt_tokens / completion_tokens / total_tokens,
          cached_tokens 在 prompt_tokens_details.cached_tokens
        - DeepSeek: cached 在 prompt_cache_hit_tokens(prompt_tokens 含 hit)
        - 无 usage / 全零: 返回 None(上层守卫跳过, 不阻断主流程)
        """
        if not data:
            return None
        input_tokens = int(data.get("prompt_tokens") or 0)
        output_tokens = int(data.get("completion_tokens") or 0)
        total_tokens = int(data.get("total_tokens") or (input_tokens + output_tokens))
        details = data.get("prompt_tokens_details") or {}
        cached = int(
            details.get("cached_tokens")
            or data.get("prompt_cache_hit_tokens")
            or 0
        )
        if input_tokens == 0 and output_tokens == 0 and cached == 0:
            return None
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached,
        )

    """OpenAI 兼容 /chat/completions 端点的通用基类。

    子类需设置:
    - provider_name: str
    - capability: ModelCapability
    - default_model_name: str
    """

    provider_name: str = ""
    # 通用 OpenAI 兼容 adapter 支持流式 + 函数调用(chat_stream/tool_calls 均已实现)
    capability: ModelCapability = ModelCapability(
        streaming=True, function_calling=True, vision=False, json_mode=False
    )
    default_model_name: str = ""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str | None = None,
        client: httpx.AsyncClient | None = None,
        provider_name: str | None = None,
        multimodal: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model_name = model_name or self.default_model_name
        # 动态注册的 provider 传入真实名(错误信息/降级记录用, 默认类属性)
        if provider_name:
            self.provider_name = provider_name
        # 多模态(provider 配置 multimodal=true): 覆盖 capability.vision=True,
        # 使 FallbackChain.require_vision 跳转能命中此 adapter
        if multimodal:
            self.capability = ModelCapability(
                streaming=True, function_calling=True,
                vision=True, json_mode=False,
            )
        # V1.5:推理模型(reasoning)复杂请求思考时间可能远超 httpx 默认 5s,
        # 放宽读超时避免 ReadTimeout 误杀正常推理(连接 15s)。
        # 2026-08-16(历史任务继续提问无响应诊断): 120s → 60s —— 第三方中转
        # (tokenrhythm)延迟高/抖动时, 过长读超时让 fallback 链逐 provider
        # 各等 120s(最坏 10 分钟无反馈, 用户感知"思考中无响应")。60s 内无
        # 响应即 fallback 下一 provider(官方 API 快), 总等待有界。
        self._timeout = httpx.Timeout(60.0, connect=15.0)
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
        on_reasoning: Any | None = None,
    ) -> ChatResult:
        """OpenAI 兼容流式 chat: 边收边回调 on_delta(delta 文本), 返回完整 ChatResult。

        - body 带 stream: true + stream_options.include_usage
        - 累积 content 增量与 tool_calls 分片(index 合并)
        - on_delta: async (text: str) -> None, 正文增量回调(WS 推送 delta 事件)
        - on_reasoning: async (text: str) -> None, 推理增量回调(reasoning_content,
          用于前端"查看推理过程"逐段展示; 无则 None)

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
        reasoning_parts: list[str] = []
        tool_calls_acc: dict[int, dict[str, Any]] = {}
        usage: TokenUsage | None = None

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
                    # 流式 usage chunk: 无 choices 但带 usage(OpenAI 兼容,
                    # stream_options.include_usage 时末尾返回) —— 修复此前
                    # 被 continue 跳过导致 usage 永远丢失
                    if chunk.get("usage"):
                        usage = self._extract_usage(chunk["usage"])
                    continue
                delta = choices[0].get("delta", {}) or {}
                # 推理与正文分离: reasoning_content → on_reasoning, content → on_delta
                reasoning = delta.get("reasoning_content") or ""
                text = delta.get("content") or ""
                if reasoning:
                    reasoning_parts.append(reasoning)
                    if on_reasoning is not None:
                        await on_reasoning(reasoning)
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
            reasoning_content="".join(reasoning_parts),
            tool_calls=tool_calls,
            used_provider=self.provider_name,
            failed_providers=[],
            usage=usage,
        )

    def _parse_openai_response(self, data: dict) -> ChatResult:
        choices = data.get("choices") or []
        message = choices[0]["message"] if choices else {}
        content = message.get("content") or ""
        # reasoning_content 单独保留(前端'查看推理过程'); content 为空时
        # 兼容纯推理模型, 用 reasoning_content 兜底 content
        reasoning = message.get("reasoning_content") or ""
        if not content:
            content = reasoning
        tool_calls = message.get("tool_calls") or []
        # tool_calls 透传 OpenAI 结构:{id, type, function:{name, arguments}}
        return ChatResult(
            content=content,
            tool_calls=list(tool_calls),
            used_provider=self.provider_name,
            failed_providers=[],
            reasoning_content=reasoning,
            usage=self._extract_usage(data.get("usage")),
        )
