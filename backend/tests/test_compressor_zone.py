"""压缩 Zone 隔离测试(架构修订 C-1 / P1-7)。

原则: 压缩只作用于 active zone —— Frozen(system prompt)/Stable(记忆/KB)
永不标记 compressed, 防止被过滤出 API 上下文。
"""

from __future__ import annotations

import pytest

from private_agent.core.compressor import Compressor


def _msg(role: str, content: str, turn: int = 0, zone: str | None = None) -> dict:
    m: dict = {"role": role, "content": content, "turn": turn}
    if zone:
        m["zone"] = zone
    return m


def test_sliding_window_skips_frozen_and_stable():
    """frozen system + stable 记忆/KB 即使 turn 旧也不被标记 compressed。"""
    c = Compressor()
    messages = [
        _msg("system", "你是助手", turn=0, zone="frozen"),   # system prompt
        _msg("user", "[User Memories] 用户偏好", turn=0, zone="stable"),
        _msg("user", "[KB Context] 知识片段", turn=2, zone="stable"),
        # active 旧轮次(应被压缩)
        _msg("user", "第 1 轮问题", turn=1, zone="active"),
        _msg("assistant", "第 1 轮回答", turn=1, zone="active"),
        _msg("user", "第 10 轮问题", turn=10, zone="active"),
        _msg("assistant", "第 10 轮回答", turn=10, zone="active"),
    ]
    result = c._sliding_window(messages, keep_turns=2)
    by_zone: dict[str, bool] = {}
    for m in result:
        by_zone[m["zone"]] = m.get("compressed", False)
    # frozen/stable 永不压缩
    assert by_zone.get("frozen") is False
    assert by_zone.get("stable") is False
    # active 旧轮次(1 < 10-2+1=9)被压缩
    active_msgs = [m for m in result if m["zone"] == "active"]
    assert any(m.get("compressed") for m in active_msgs)
    # 最新轮次(10 >= 9)不压缩
    latest = [m for m in active_msgs if m["turn"] == 10]
    assert all(not m.get("compressed") for m in latest)


def test_sliding_window_zone_absent_treated_as_active():
    """无 zone 键的消息(active 历史)按原逻辑参与压缩(向后兼容)。"""
    c = Compressor()
    messages = [
        _msg("user", "旧消息", turn=1),
        _msg("assistant", "旧回答", turn=1),
        _msg("user", "新消息", turn=8),
    ]
    result = c._sliding_window(messages, keep_turns=2)
    assert result[0].get("compressed") is True  # turn=1 < 8-2+1=7
    assert not result[-1].get("compressed")  # 最新轮次不压缩(None/False 均可)


def test_sliding_window_tool_pairing_preserved_within_active():
    """active 内 tool_call/tool_result 配对不因压缩拆分(回归保护)。"""
    c = Compressor()
    messages = [
        {
            "role": "assistant", "turn": 1, "zone": "active",
            "content": "", "tool_calls": [{"id": "call_x", "function": {}}],
        },
        {
            "role": "tool", "turn": 1, "zone": "active",
            "tool_call_id": "call_x", "content": "结果",
        },
        _msg("user", "新消息", turn=8, zone="active"),
    ]
    result = c._sliding_window(messages, keep_turns=2)
    # turn=1 < 7 → 应压缩, 但配对保护: tool 消息与 call 同 turn 且在 keep_from 内?
    # keep_from = max(1, 8-2+1)=7 → turn=1 < 7 → 标记 compressed
    # 配对保护: tool 的 call_turn=1 < 7 → 不豁免 → 两条都压缩(配对一致)
    comp = [m for m in result if m.get("compressed")]
    assert all(m.get("role") in ("assistant", "tool") for m in comp)
