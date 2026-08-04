"""蓝图 §2.7/§2.9 - 模型适配层基类:Protocol + ModelCapability + FallbackChain。

Source: plan/m1-react-loop step 7
- ModelCapability: streaming/function_calling/vision/json_mode 四项能力位
- ModelAdapter Protocol: async chat() + provider_name + capability
- FallbackChain: 按 fallback_chain 顺序尝试,失败降级,全 fail 抛 AllProvidersFailedError
"""
from __future__ import annotations

import asyncio
import typing
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelCapability:
    """蓝图 §2.7 模型能力位(决定 router 降级与 tool_call 协商)。"""

    streaming: bool
    function_calling: bool
    vision: bool
    json_mode: bool
    # 对话流畅度优化(方向二): 模型上下文窗口(token 数)。None 时用
    # 全局配置 context_window; 压缩触发线 = min(模型能力, 配置) × 0.8
    context_window: int | None = None


@dataclass
class ChatResult:
    """adapter.chat() 统一返回结构。

    failed_providers: 本次降级过程中失败的 provider 名(按顺序),无降级时为空列表。
    """

    content: str
    tool_calls: list[dict] = field(default_factory=list)
    used_provider: str = ""
    failed_providers: list[str] = field(default_factory=list)
    reasoning_content: str = ""  # 模型推理过程(deepseek 等 reasoning 模型)


class ProviderError(Exception):
    """单个 provider 调用失败(蓝图 §2.9 fallback 触发条件)。"""

    def __init__(self, provider: str, message: str = ""):
        self.provider = provider
        super().__init__(f"[{provider}] {message}" if message else provider)


class AllProvidersFailedError(Exception):
    """fallback_chain 全部 provider 失败(蓝图 §2.9 终态错误)。"""


def _is_retryable_error(e: ProviderError) -> bool:
    """判断 ProviderError 是否值得重试(http error / upstream 5xx / 429)。"""
    msg = str(e)
    return (
        "http error" in msg
        or "upstream 429" in msg
        or "upstream 5" in msg
    )


@typing.runtime_checkable
class ModelAdapter(typing.Protocol):
    """蓝图 §2.7 模型适配器 Protocol。

    实现方需提供:
    - async chat(messages, tools=None) -> ChatResult
    - provider_name: str 属性
    - capability: ModelCapability 属性
    """

    provider_name: str
    capability: ModelCapability

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResult: ...


class FallbackChain:
    """蓝图 §2.9 fallback_chain 降级执行器。

    按构造时传入的 adapters 顺序尝试:
    - 任一 adapter 抛 ProviderError → 记录 failed_providers,继续下一个
    - 可重试错误(http error / upstream 5xx / 429)先指数退避重试一次
      (V1.5:上游服务间歇性抖动时提升成功率)
    - 任一 adapter 成功 → 返回 ChatResult(used_provider=成功方,failed_providers=前面失败列表)
    - 全部失败 → 抛 AllProvidersFailedError(附最后一个 provider 的错误详情)
    """

    def __init__(self, adapters: list[ModelAdapter]):
        self._adapters = list(adapters)

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        failed: list[str] = []
        last_error: Exception | None = None
        for adapter in self._adapters:
            attempts = 0
            while True:
                attempts += 1
                try:
                    result = await adapter.chat(messages, tools, max_tokens=max_tokens)
                except ProviderError as e:
                    last_error = e
                    # 可重试错误:退避后重试一次;认证类(401/400/403)重试无意义
                    if attempts == 1 and _is_retryable_error(e):
                        await asyncio.sleep(0.5)
                        continue
                    failed.append(adapter.provider_name)
                    break
                # 成功:回填 failed_providers(适配器自身不感知降级上下文)
                result.failed_providers = failed
                return result
        detail = f" | last: {last_error}" if last_error else ""
        raise AllProvidersFailedError(
            f"all {len(self._adapters)} providers failed: {failed}{detail}"
        ) from last_error

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        on_delta=None,
        on_reasoning=None,
    ) -> ChatResult:
        """流式 chat 降级执行: 逐 adapter 尝试流式, 无流式能力则用非流式兜底。"""
        failed: list[str] = []
        last_error: Exception | None = None
        for adapter in self._adapters:
            capability = getattr(adapter, "capability", None)
            if capability is not None and getattr(capability, "streaming", False):
                try:
                    result = await adapter.chat_stream(
                        messages, tools, max_tokens=max_tokens,
                        on_delta=on_delta, on_reasoning=on_reasoning,
                    )
                    result.failed_providers = failed
                    return result
                except ProviderError as e:
                    failed.append(adapter.provider_name)
                    last_error = e
                    continue
            # 无流式能力: 用非流式 chat 兜底(前端无逐句效果但可用)
            try:
                result = await adapter.chat(messages, tools, max_tokens=max_tokens)
                result.failed_providers = failed
                return result
            except ProviderError as e:
                failed.append(adapter.provider_name)
                last_error = e
        detail = f" | last: {last_error}" if last_error else ""
        raise AllProvidersFailedError(
            f"all {len(self._adapters)} providers failed: {failed}{detail}"
        ) from last_error
