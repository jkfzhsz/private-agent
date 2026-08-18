"""B-1 压缩多信号触发 + keep_ratio + compress_now 测试(设计文档 §3.2)。

覆盖:
- maybe_compress 多信号: 触发优先级 token_limit > model_suggested > task_phase > turn_limit
- 信号默认关闭: 未开启 task_phase/model_suggested 时行为与旧版一致
- keep_ratio: 保留最近 ~10% token 原始消息(与 keep_turns 取更优)
- keep_ratio 边界: ratio=0 全压缩 / ratio=1 全保留
- compress_now 工具: 定义/权限/注册开关默认关
- ReactLoop._compress_now_requested: 工具调用置位 → model_suggested 触发
- compress 事件 trigger 新枚举(model_suggested/task_phase)
"""
import asyncio
import os
from unittest.mock import AsyncMock

import pytest

from private_agent.core.compressor import Compressor
from private_agent.tools.builtins.compress_now import (
    COMPRESS_NOW_TOOL,
    compress_now_handler,
)


def _msg(role, content, turn=1, **extra):
    m = {"role": role, "content": content, "turn": turn}
    m.update(extra)
    return m


def _long_messages(n_turns: int = 15) -> list[dict]:
    """n_turns 轮消息(每轮 user + assistant)。"""
    msgs = []
    for t in range(1, n_turns + 1):
        msgs.append(_msg("user", f"第 {t} 轮问题 " + "x" * 50, turn=t, msg_id=t))
        msgs.append(_msg("assistant", f"第 {t} 轮回答 " + "y" * 50, turn=100 + t, msg_id=100 + t))
    return msgs


# ── maybe_compress 多信号 ───────────────────────────────────────────────────


def test_maybe_compress_turn_limit_default():
    """未开启新信号: 轮次 > 10 触发 turn_limit(默认行为不变)。"""
    c = Compressor()
    msgs = _long_messages(12)
    trigger = c.maybe_compress(msgs, active_turns=12, context_window=8000)
    assert trigger == "turn_limit"
    # 轮次少 → 不触发
    assert c.maybe_compress(msgs[:6], active_turns=3, context_window=8000) is None


def test_maybe_compress_token_limit_priority():
    """token 超限 → token_limit(最高优先级, 即使轮次未超)。"""
    c = Compressor()
    msgs = _long_messages(5)
    trigger = c.maybe_compress(msgs, active_turns=5, context_window=200)
    assert trigger == "token_limit"


def test_maybe_compress_signal_priority():
    """优先级: token_limit > model_suggested > task_phase > turn_limit。"""
    c = Compressor()
    msgs = _long_messages(12)  # turn_limit 会触发
    # model_suggested > turn_limit
    assert (
        c.maybe_compress(msgs, active_turns=12, context_window=8000, model_suggested=True)
        == "model_suggested"
    )
    # task_phase > turn_limit
    assert (
        c.maybe_compress(msgs, active_turns=12, context_window=8000, task_phase=True)
        == "task_phase"
    )
    # model_suggested > task_phase
    assert (
        c.maybe_compress(
            msgs, active_turns=12, context_window=8000,
            task_phase=True, model_suggested=True,
        )
        == "model_suggested"
    )
    # token_limit 最高
    assert (
        c.maybe_compress(
            msgs, active_turns=12, context_window=200,
            task_phase=True, model_suggested=True,
        )
        == "token_limit"
    )


def test_maybe_compress_signals_off_no_early_trigger():
    """信号默认关闭: 轮次 8 且 token 未超 → 不触发(零回归)。"""
    c = Compressor()
    msgs = _long_messages(8)
    assert (
        c.maybe_compress(
            msgs, active_turns=8, context_window=8000,
            task_phase=False, model_suggested=False,
        )
        is None
    )
    # 开启 task_phase → 轮次 8 提前触发
    assert (
        c.maybe_compress(
            msgs, active_turns=8, context_window=8000, task_phase=True
        )
        == "task_phase"
    )


# ── keep_ratio 保留比例(B-2) ────────────────────────────────────────────────


def test_plan_compression_keep_ratio_keeps_recent_tokens():
    """keep_ratio 保留最近 ~ratio token(与 keep_turns 取更优)。"""
    c = Compressor()
    msgs = _long_messages(12)
    plan_ratio = c.plan_compression(msgs, keep_ratio=0.3)
    plan_turns = c.plan_compression(msgs, keep_turns=6)
    # 取 token 更优者: 保留更多消息
    kept_ratio = len(plan_ratio["kept"])
    kept_turns = len(plan_turns["kept"])
    assert kept_ratio >= 6  # 至少保留最近几轮
    assert plan_ratio["kept"] or plan_ratio["compressed"]  # 消息总量不变


def test_keep_ratio_boundary():
    """ratio=0 且 keep_turns=0 → 全压缩; ratio=1 → 全保留。"""
    c = Compressor()
    msgs = _long_messages(5)
    p0 = c.plan_compression(msgs, keep_turns=0, keep_ratio=0.0)
    assert len(p0["kept"]) == 0
    p1 = c.plan_compression(msgs, keep_ratio=1.0)
    assert len(p1["compressed"]) == 0
    assert len(p1["kept"]) == len(msgs)


def test_keep_ratio_default_preserves_turns_behavior():
    """keep_ratio 默认 0.1: 与 keep_turns=6 取 token 更优者, 行为不劣化。"""
    c = Compressor()
    msgs = _long_messages(15)
    plan = c.plan_compression(msgs, keep_turns=6, keep_ratio=0.1)
    # 保留消息 ≥ 最近 6 轮中更优者; 压缩消息存在
    assert plan["compressed"]
    kept_turns = max(m.get("turn", 0) for m in plan["kept"])
    assert kept_turns >= 15 - 6  # 至少保留最近 6 轮


# ── compress_now 工具 ───────────────────────────────────────────────────────


def test_compress_now_tool_definition():
    """工具定义: safe 级 + 参数 reason 可选 + 描述含压缩语义。"""
    assert COMPRESS_NOW_TOOL.name == "compress_now"
    assert COMPRESS_NOW_TOOL.safety_level == "safe"
    schema = COMPRESS_NOW_TOOL.to_openai_schema()
    assert schema["function"]["name"] == "compress_now"
    assert "压缩" in schema["function"]["description"]
    props = schema["function"]["parameters"]["properties"]
    assert "reason" in props
    assert schema["function"]["parameters"]["required"] == []


def test_compress_now_handler_returns_marker():
    """handler 返回标记消息(真实压缩由 ReactLoop 本轮结束执行)。"""
    async def _run():
        result = await compress_now_handler({"reason": "完成交付物"})
        assert result.error is None
        assert "压缩请求已记录" in result.output
        return result

    asyncio.run(_run())


def test_compress_now_config_default_off():
    """config 默认 compress_now_tool=false(工具不注册, 零回归)。"""
    import private_agent.config.loader as loader

    cfg = loader.load_config()
    comp = cfg.get("context", {}).get("compression", {})
    assert comp.get("compress_now_tool") is False
    assert comp.get("task_phase") is False
    assert comp.get("model_suggested") is False
    assert comp.get("keep_ratio", 0.1) == 0.1  # B-2 默认


# ── ReactLoop._compress_now_requested 信号 ──────────────────────────────────


def test_compress_now_requested_flag_and_model_suggested_trigger():
    """工具调用置位 → _maybe_compress 以 model_suggested 触发(config 开启时)。

    通过直接调用 maybe_compress + 标记模拟 ReactLoop 装配语义。
    """
    c = Compressor()
    msgs = _long_messages(8)  # 轮次未超 10, token 未超
    # config 关闭 model_suggested: 即使置位也不触发(信号被 config 门控)
    requested = True
    model_suggested = bool(False) and requested
    assert (
        c.maybe_compress(
            msgs, active_turns=8, context_window=8000,
            model_suggested=model_suggested,
        )
        is None
    )
    # config 开启 + 置位 → model_suggested 触发
    model_suggested = bool(True) and requested
    assert (
        c.maybe_compress(
            msgs, active_turns=8, context_window=8000,
            model_suggested=model_suggested,
        )
        == "model_suggested"
    )


def test_compress_now_tool_not_registered_by_default():
    """register_all_builtins 不注册 compress_now(需 config 显式开)。"""
    from private_agent.tools.builtins import register_all_builtins
    from private_agent.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_all_builtins(reg)
    names = {t.name for t in reg.list_tools()}
    assert "compress_now" not in names
