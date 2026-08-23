"""B4 P0-1 AC-1..2 - TokenEstimator 纯函数测试。

Source: plan/b4-compress-billing step 12 (AC-1, AC-2)
"""
from private_agent.core.token_estimator import TokenEstimator


def test_estimate_default_ratio():
    """AC-1: estimate 返回 len(text)/3.0 取整。"""
    estimator = TokenEstimator()
    assert estimator.estimate("abc") == 1
    assert estimator.estimate("abcdef") == 2
    assert estimator.estimate("abcdefghi") == 3
    assert estimator.estimate("") == 1  # 空文本至少 1 token


def test_estimate_messages_skips_compressed():
    """AC-2: estimate_messages 跳过 compressed 消息。"""
    estimator = TokenEstimator()
    messages = [
        {"role": "system", "content": "Hello" * 30},  # 150 chars → 50 tokens
        {"role": "user", "content": "Hi" * 15, "compressed": True},  # 跳过
        {"role": "assistant", "content": "OK" * 10},  # 20 chars → 6 tokens
    ]
    total = estimator.estimate_messages(messages)
    assert total == 50 + 6


def test_estimate_messages_includes_tool_calls():
    """estimate_messages 计入 tool_calls 中的 content。"""
    estimator = TokenEstimator()
    messages = [
        {
            "role": "assistant",
            "content": "Let me help",
            "tool_calls": [
                {"function": {"name": "echo", "arguments": '{"x":"hello"}'}}
            ],
        }
    ]
    total = estimator.estimate_messages(messages)
    assert total > 0


def test_estimate_empty_text_returns_1():
    """空文本返回 1 token(避免除零)。"""
    estimator = TokenEstimator()
    assert estimator.estimate("") == 1