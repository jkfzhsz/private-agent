"""2026-08-04: FallbackChain 429/5xx 指数退避重试测试。

覆盖:
- 429 前 2 次失败第 3 次成功 → 返回成功(退避重试生效)
- 持续 429 超过重试上限 → AllProvidersFailedError
- 401 认证错误不重试(立即失败)
- 流式 chat_stream 同样退避重试
"""
import asyncio

import pytest

from private_agent.models.base import (
    AllProvidersFailedError,
    ChatResult,
    FallbackChain,
    ModelAdapter,
    ProviderError,
)


class _FlakyAdapter:
    """前 n 次抛 ProviderError(429), 之后成功。"""

    provider_name = "flaky"
    capability = None

    def __init__(self, fails: int, err_msg: str = "upstream 429: rate limited"):
        self.fails = fails
        self.err_msg = err_msg
        self.calls = 0

    async def chat(self, messages, tools=None, max_tokens=None):
        self.calls += 1
        if self.calls <= self.fails:
            raise ProviderError(self.provider_name, self.err_msg)
        return ChatResult(content="ok", used_provider=self.provider_name)


class _AuthFailAdapter:
    """认证错误(401)不重试。"""

    provider_name = "auth"
    capability = None

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, max_tokens=None):
        self.calls += 1
        raise ProviderError(self.provider_name, "401 Unauthorized")


class _StreamFlakyAdapter:
    provider_name = "stream"
    capability = None

    def __init__(self, fails: int):
        self.fails = fails
        self.calls = 0
        self.streaming = True
        # 用简单属性模拟 capability
        class _Cap:
            streaming = True
        self.capability = _Cap()

    async def chat_stream(self, messages, tools=None, max_tokens=None, on_delta=None, on_reasoning=None):
        self.calls += 1
        if self.calls <= self.fails:
            raise ProviderError(self.provider_name, "upstream 503: busy")
        return ChatResult(content="stream-ok", used_provider=self.provider_name)

    async def chat(self, messages, tools=None, max_tokens=None):
        return ChatResult(content="fallback", used_provider=self.provider_name)


class TestFallbackRetry:
    def test_429_retries_then_succeeds(self):
        """前 2 次 429, 第 3 次成功 → 成功返回。"""
        adapter = _FlakyAdapter(fails=2)

        async def run():
            chain = FallbackChain([adapter])
            result = await chain.chat([])
            assert result.content == "ok"
            assert adapter.calls == 3  # 2 次失败 + 1 次成功

        asyncio.run(run())

    def test_persistent_429_raises(self):
        """持续 429 超过重试上限 → AllProvidersFailedError。"""
        adapter = _FlakyAdapter(fails=99)

        async def run():
            chain = FallbackChain([adapter])
            with pytest.raises(AllProvidersFailedError):
                await chain.chat([])
            # 3 次尝试(上限)而非无限
            assert adapter.calls == FallbackChain._RETRY_LIMIT

        asyncio.run(run())

    def test_auth_error_no_retry(self):
        """401 认证错误不重试, 立即失败。"""
        adapter = _AuthFailAdapter()

        async def run():
            chain = FallbackChain([adapter])
            with pytest.raises(AllProvidersFailedError):
                await chain.chat([])
            assert adapter.calls == 1  # 仅 1 次

        asyncio.run(run())

    def test_stream_retries_then_succeeds(self):
        """流式 503 前 2 次失败第 3 次成功。"""
        adapter = _StreamFlakyAdapter(fails=2)

        async def run():
            chain = FallbackChain([adapter])
            result = await chain.chat_stream([])
            assert result.content == "stream-ok"
            assert adapter.calls == 3

        asyncio.run(run())

    def test_retry_backoff_increases(self):
        """退避时间递增(用 fake 计时验证调用间隔足够)。"""
        adapter = _FlakyAdapter(fails=2)
        delays = []

        orig_sleep = asyncio.sleep

        async def fake_sleep(seconds):
            delays.append(seconds)
            await orig_sleep(0)  # 不真睡

        asyncio.sleep = fake_sleep
        try:
            async def run():
                chain = FallbackChain([adapter])
                await chain.chat([])
            asyncio.run(run())
        finally:
            asyncio.sleep = orig_sleep
        # 退避 0.5 → 1.0
        assert delays == [0.5, 1.0], f"delays={delays}"
