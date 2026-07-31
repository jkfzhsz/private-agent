"""蓝图 §2.7/§2.9 - 模型适配层基类:Protocol + ModelCapability + FallbackChain。

Source: plan/m1-react-loop step 7
- ModelCapability: streaming/function_calling/vision/json_mode 四项能力位
- ModelAdapter Protocol: async chat() + provider_name + capability
- FallbackChain: 按 fallback_chain 顺序尝试,失败降级,全 fail 抛 AllProvidersFailedError
"""
from __future__ import annotations

import typing
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelCapability:
    """蓝图 §2.7 模型能力位(决定 router 降级与 tool_call 协商)。"""

    streaming: bool
    function_calling: bool
    vision: bool
    json_mode: bool


@dataclass
class ChatResult:
    """adapter.chat() 统一返回结构。

    failed_providers: 本次降级过程中失败的 provider 名(按顺序),无降级时为空列表。
    """

    content: str
    tool_calls: list[dict] = field(default_factory=list)
    used_provider: str = ""
    failed_providers: list[str] = field(default_factory=list)


class ProviderError(Exception):
    """单个 provider 调用失败(蓝图 §2.9 fallback 触发条件)。"""

    def __init__(self, provider: str, message: str = ""):
        self.provider = provider
        super().__init__(f"[{provider}] {message}" if message else provider)


class AllProvidersFailedError(Exception):
    """fallback_chain 全部 provider 失败(蓝图 §2.9 终态错误)。"""


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
    - 任一 adapter 成功 → 返回 ChatResult(used_provider=成功方,failed_providers=前面失败列表)
    - 全部失败 → 抛 AllProvidersFailedError
    """

    def __init__(self, adapters: list[ModelAdapter]):
        self._adapters = list(adapters)

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResult:
        failed: list[str] = []
        last_error: Exception | None = None
        for adapter in self._adapters:
            try:
                result = await adapter.chat(messages, tools)
            except ProviderError as e:
                failed.append(adapter.provider_name)
                last_error = e
                continue
            # 成功:回填 failed_providers(适配器自身不感知降级上下文)
            result.failed_providers = failed
            return result
        raise AllProvidersFailedError(
            f"all {len(self._adapters)} providers failed: {failed}"
        ) from last_error
