"""C-2 大产物文件化引导 + 压缩转存 archive 联动测试(设计文档 §3.3-C2/C3)。

覆盖:
- execute(workspace=...): 事实型消息转存 {workspace}/archive/ctx-{hash}.md,
  摘要含 "[事实快照见 ws:archive/...]" 路径引用
- execute 无 workspace: 维持内联 factual_snapshot(零回归)
- archive 文件幂等(同内容同 hash)
- large_output_threshold_chars=0 默认关闭引导(零回归)
- 超阈值 + 有 workspace → 注入系统消息提示
- 超阈值但无 workspace → 不注入
"""
import asyncio
import os

import pytest

from private_agent.core.compressor import Compressor


def _msg(role, content, turn=1, **extra):
    m = {"role": role, "content": content, "turn": turn}
    m.update(extra)
    return m


def _factual_turns(n: int = 8) -> list[dict]:
    """含事实型内容(数字/路径)的轮次消息。"""
    msgs = []
    for t in range(1, n + 1):
        msgs.append(
            _msg("user", f"持仓 {t} 号: 市值 {1000 + t} 元, 路径 D:/data/{t}.csv",
                 turn=t, msg_id=t)
        )
        msgs.append(_msg("assistant", f"已记录第 {t} 轮数据", turn=t, msg_id=100 + t))
    return msgs


def _chitchat_turns(n: int = 8) -> list[dict]:
    """非事实型(闲聊)消息 —— 走 LLM 摘要不转存。"""
    return [
        _msg("user", f"你好{t}", turn=t, msg_id=t)
        for t in range(1, n + 1)
    ] + [
        _msg("assistant", f"我在{t}", turn=t, msg_id=100 + t)
        for t in range(1, n + 1)
    ]


# ── 压缩转存联动(C-3) ───────────────────────────────────────────────────────


def test_execute_with_workspace_archives_factual():
    """workspace 非空: 事实型消息转存 archive/ 文件, 摘要含路径引用。"""
    from pathlib import Path

    compressor = Compressor()
    msgs = _factual_turns(8)

    async def _run(tmp_ws):
        result = await compressor.execute(
            msgs, keep_turns=2, keep_ratio=0.0, compress_adapter=None,
            workspace=str(tmp_ws),
        )
        assert result["compressed_msgs"]
        snapshot = result["factual_snapshot"]
        assert snapshot is not None
        assert "ws:archive/" in snapshot["content"]
        # archive 文件存在
        assert result["archive_path"]
        archive_file = Path(tmp_ws) / result["archive_path"]
        assert archive_file.exists()
        content = archive_file.read_text(encoding="utf-8")
        assert "持仓 1 号" in content  # 事实原文保留
        return result

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        r = asyncio.run(_run(tmp))
        assert r["archive_path"].startswith("archive/ctx-")


def test_execute_without_workspace_falls_back_inline():
    """workspace 为空: 维持内联 factual_snapshot(零回归)。"""
    compressor = Compressor()
    msgs = _factual_turns(8)

    async def _run():
        result = await compressor.execute(
            msgs, keep_turns=2, keep_ratio=0.0, compress_adapter=None,
            workspace="",
        )
        assert result["archive_path"] is None
        snapshot = result["factual_snapshot"]
        assert snapshot is not None
        assert snapshot["content"].startswith("[事实快照(原文保留)]")
        assert "持仓 1 号" in snapshot["content"]
        return result

    asyncio.run(_run())


def test_archive_idempotent_same_content_same_file():
    """同内容转存 → 同 hash 文件名(幂等, 不重复写)。"""
    from pathlib import Path

    compressor = Compressor()
    msgs = _factual_turns(8)

    async def _run(tmp_ws):
        r1 = await compressor.execute(
            msgs, keep_turns=2, keep_ratio=0.0, compress_adapter=None,
            workspace=str(tmp_ws),
        )
        r2 = await compressor.execute(
            msgs, keep_turns=2, keep_ratio=0.0, compress_adapter=None,
            workspace=str(tmp_ws),
        )
        assert r1["archive_path"] == r2["archive_path"]
        # 目录内仅 1 个文件
        files = [f for f in (Path(tmp_ws) / "archive").iterdir() if f.is_file()]
        assert len(files) == 1
        return r1

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(_run(tmp))


def test_archive_skips_chitchat():
    """非事实型消息不转存(走 LLM 摘要路径)。"""
    from pathlib import Path

    compressor = Compressor()
    msgs = _chitchat_turns(8)

    async def _run(tmp_ws):
        result = await compressor.execute(
            msgs, keep_turns=2, keep_ratio=0.0, compress_adapter=None,
            workspace=str(tmp_ws),
        )
        # 无事实型 → 无 archive
        assert result["archive_path"] is None
        assert result["factual_snapshot"] is None
        archive_dir = Path(tmp_ws) / "archive"
        assert not archive_dir.exists() or not any(archive_dir.iterdir())
        return result

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(_run(tmp))


# ── 大产物文件化引导(C-2) ───────────────────────────────────────────────────


def test_large_output_threshold_default_off():
    """config 默认 large_output_threshold_chars=0(引导关闭, 零回归)。"""
    import private_agent.config.loader as loader

    cfg = loader.load_config()
    comp = cfg.get("context", {}).get("compression", {})
    assert comp.get("large_output_threshold_chars", 0) == 0


def test_maybe_guide_large_output_injects_with_workspace():
    """超阈值 + 有 workspace → 注入系统消息提示(下一轮模型可见)。"""
    from unittest.mock import AsyncMock

    from private_agent.core.context_manager import ContextManager
    from private_agent.core.react_loop import ReactLoop

    cm = AsyncMock(spec=ContextManager)
    loop = ReactLoop(
        session_id=1,
        context_manager=cm,
        adapter=None,
        tools=[],
        conn=None,
        cfg={
            "system": {"workspace_root": "D:\\PA\\zizhan"},
            "context": {"compression": {"large_output_threshold_chars": 100}},
        },
    )
    loop._workspace_label = "D:\\PA\\zizhan"
    cm.append_system_message = AsyncMock()

    async def _run():
        await loop._maybe_guide_large_output("x" * 500)  # 超 100
        assert cm.append_system_message.called
        call_args = cm.append_system_message.call_args[1]
        assert "ws_write" in call_args["content"]
        assert "路径引用" in call_args["content"]

    asyncio.run(_run())


def test_maybe_guide_large_output_skips_without_workspace():
    """超阈值但无 workspace → 不注入。"""
    from unittest.mock import AsyncMock

    from private_agent.core.context_manager import ContextManager
    from private_agent.core.react_loop import ReactLoop

    cm = AsyncMock(spec=ContextManager)
    loop = ReactLoop(
        session_id=1,
        context_manager=cm,
        adapter=None,
        tools=[],
        conn=None,
        cfg={
            "context": {"compression": {"large_output_threshold_chars": 100}},
        },
    )
    loop._workspace_label = ""  # 无 workspace
    cm.append_system_message = AsyncMock()

    async def _run():
        await loop._maybe_guide_large_output("x" * 500)
        assert not cm.append_system_message.called

    asyncio.run(_run())


def test_maybe_guide_large_output_skips_below_threshold():
    """未超阈值 → 不注入。"""
    from unittest.mock import AsyncMock

    from private_agent.core.context_manager import ContextManager
    from private_agent.core.react_loop import ReactLoop

    cm = AsyncMock(spec=ContextManager)
    loop = ReactLoop(
        session_id=1,
        context_manager=cm,
        adapter=None,
        tools=[],
        conn=None,
        cfg={
            "system": {"workspace_root": "D:\\PA\\zizhan"},
            "context": {"compression": {"large_output_threshold_chars": 10000}},
        },
    )
    loop._workspace_label = "D:\\PA\\zizhan"
    cm.append_system_message = AsyncMock()

    async def _run():
        await loop._maybe_guide_large_output("短内容")
        assert not cm.append_system_message.called

    asyncio.run(_run())
