"""M4 §8.8 LLM-as-Judge 模块测试。

Source: plan/m4-metrics-judge step 10 (AC-6..AC-8)
覆盖:
- AC-6: build_judge_adapter 读 cfg["eval"]["judge_model"] 构造 GlmAdapter
- AC-7: load_judge_prompt 加载 general.md 模板含三模板变量
- AC-8: LLMJudge.judge() 解析 JSON,降级返回 0 分
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from private_agent.eval.judge import LLMJudge, build_judge_adapter, load_judge_prompt
from private_agent.models.base import ChatResult, ProviderError


# ── AC-6: build_judge_adapter ──────────────────────────────────────────


def test_build_judge_adapter():
    """AC-6(V2 P3): judge_model 按模型名匹配 provider 构造 adapter。"""
    cfg = {
        "eval": {"judge_model": "judge-m"},
        "models": {
            "providers": {
                "my-llm": {
                    "base_url": "https://api.example.com/v1",
                    "model_name": "judge-m",
                    "enabled": True,
                }
            }
        },
    }
    adapter = build_judge_adapter(cfg)
    assert adapter is not None
    assert adapter.model_name == "judge-m"
    assert adapter.provider_name == "my-llm"


def test_build_judge_adapter_disabled_returns_none():
    """model_name 匹配但 provider disabled 时返回 None。"""
    cfg = {
        "eval": {"judge_model": "judge-m"},
        "models": {"providers": {"my-llm": {"model_name": "judge-m", "enabled": False}}},
    }
    assert build_judge_adapter(cfg) is None


def test_build_judge_adapter_no_match_returns_none():
    """V2 P3: judge_model 无匹配 provider → None(去预置化, 不隐式绑定)。"""
    cfg = {
        "eval": {"judge_model": "ghost-model"},
        "models": {
            "providers": {
                "my-llm": {"base_url": "http://x", "model_name": "other-m", "enabled": True}
            }
        },
    }
    assert build_judge_adapter(cfg) is None


# ── AC-7: load_judge_prompt ────────────────────────────────────────────


def test_load_judge_prompt():
    """AC-7: 加载 general.md,含三模板变量。"""
    cfg = {"eval": {"judge_prompt_dir": str(Path(__file__).resolve().parent.parent / "config" / "judge_prompts")}}
    template = load_judge_prompt(cfg)
    assert "{user_input}" in template
    assert "{agent_response}" in template
    assert "{expected_output}" in template


# ── AC-8: LLMJudge.judge() ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_judge_normal():
    """AC-8: mock adapter 返回合法 JSON,解析成功。"""
    mock_adapter = MagicMock()
    mock_adapter.chat = AsyncMock(
        return_value=ChatResult(
            content='{"response_quality": 4, "task_completion": 5, "quality_reason": "good", "completion_reason": "all covered"}'
        )
    )
    judge = LLMJudge(adapter=mock_adapter, prompt_template="input:{user_input}\nresp:{agent_response}\nexp:{expected_output}")
    result = await judge.judge(user_input="test input", agent_response="test response", expected_output="expected")
    assert result["response_quality"] == 4
    assert result["task_completion"] == 5
    assert result["quality_reason"] == "good"
    assert result["completion_reason"] == "all covered"


@pytest.mark.asyncio
async def test_judge_markdown_wrapped():
    """AC-8 Critic reservation 1: mock adapter 返回 ```json``` 包裹 JSON,解析成功。"""
    mock_adapter = MagicMock()
    mock_adapter.chat = AsyncMock(
        return_value=ChatResult(
            content='```json\n{"response_quality": 3, "task_completion": 4, "quality_reason": "ok", "completion_reason": "partial"}\n```'
        )
    )
    judge = LLMJudge(adapter=mock_adapter, prompt_template="{user_input}{agent_response}{expected_output}")
    result = await judge.judge(user_input="u", agent_response="r", expected_output="e")
    assert result["response_quality"] == 3
    assert result["task_completion"] == 4


@pytest.mark.asyncio
async def test_judge_json_with_prefix():
    """AC-8 Critic reservation 1: 非标准 JSON(带前后缀),正则提取 {...} 子串解析。"""
    mock_adapter = MagicMock()
    mock_adapter.chat = AsyncMock(
        return_value=ChatResult(
            content='评分结果如下:\n{"response_quality": 5, "task_completion": 5, "quality_reason": "perfect", "completion_reason": "all"}\n以上是评分。'
        )
    )
    judge = LLMJudge(adapter=mock_adapter, prompt_template="{user_input}{agent_response}{expected_output}")
    result = await judge.judge(user_input="u", agent_response="r", expected_output="e")
    assert result["response_quality"] == 5
    assert result["task_completion"] == 5


@pytest.mark.asyncio
async def test_judge_parse_error():
    """AC-8: mock adapter 返回非 JSON,降级返回 0 分 + reason="judge_parse_error"。"""
    mock_adapter = MagicMock()
    mock_adapter.chat = AsyncMock(return_value=ChatResult(content="这不是 JSON"))
    judge = LLMJudge(adapter=mock_adapter, prompt_template="{user_input}{agent_response}{expected_output}")
    result = await judge.judge(user_input="u", agent_response="r", expected_output="e")
    assert result["response_quality"] == 0
    assert result["task_completion"] == 0
    assert "judge_parse_error" in result["quality_reason"]


@pytest.mark.asyncio
async def test_judge_call_failed():
    """AC-8: mock adapter 抛 ProviderError,降级返回 0 分 + reason="judge_call_failed"。"""
    mock_adapter = MagicMock()
    mock_adapter.chat = AsyncMock(side_effect=ProviderError("glm", "network timeout"))
    judge = LLMJudge(adapter=mock_adapter, prompt_template="{user_input}{agent_response}{expected_output}")
    result = await judge.judge(user_input="u", agent_response="r", expected_output="e")
    assert result["response_quality"] == 0
    assert result["task_completion"] == 0
    assert "judge_call_failed" in result["quality_reason"]


@pytest.mark.asyncio
async def test_judge_template_filling():
    """AC-8: 模板变量被正确填充到 messages 中。"""
    mock_adapter = MagicMock()
    mock_adapter.chat = AsyncMock(
        return_value=ChatResult(
            content='{"response_quality": 4, "task_completion": 4, "quality_reason": "x", "completion_reason": "y"}'
        )
    )
    template = "USER: {user_input}\nRESP: {agent_response}\nEXP: {expected_output}"
    judge = LLMJudge(adapter=mock_adapter, prompt_template=template)
    await judge.judge(user_input="hello", agent_response="world", expected_output="expected_out")
    # 验证 chat 被调用,messages 含填充后的内容
    mock_adapter.chat.assert_called_once()
    messages = mock_adapter.chat.call_args.kwargs.get("messages") or mock_adapter.chat.call_args.args[0]
    assert any("hello" in str(m) for m in messages)
    assert any("world" in str(m) for m in messages)
    assert any("expected_out" in str(m) for m in messages)
