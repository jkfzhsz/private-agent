"""2026-09-29 回归: vision 链空链导致"全部模型调用失败"。

缺陷(见 docs/diagnosis-2026-09-29-vision-chain-empty.md):
    models.router.vision_chain 指向已被禁用(enabled=false)的 provider 时,
    registry.build_fallback_chain 返回 **空 FallbackChain(对象, 非 None)**;
    react_loop 原用 `_vision_adapter is not None` 判定 → 判定通过 →
    模型链被替换为空链 → FallbackChain.chat_stream 循环 0 次 →
    AllProvidersFailedError("all 0 providers failed: []")。
    同时"未配置多模态模型"的人话兜底分支因入口条件恰为 `is None` 而永不可达。
    生产影响: 2026-09-17 / 09-23 / 09-29 共 7 次, 图片类上传 100% 失败
    (静默 3 周), 文档类上传不受影响。

修复(方案 A):
    A-2 react_loop.py —— 判定改"存在且非空"(_adapters 为真), 空链落入
        else 分支(退 full_chain 的 vision 子集, 或给出明确提示)。
    A-3 main.py —— 多模态能力声明补 enabled 条件, 不再对已禁用 provider
        谎报"你具备图片识别能力(已配置多模态模型)"。
"""
import asyncio
import os

import asyncpg

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop
from private_agent.models.base import (
    AllProvidersFailedError,
    ChatResult,
    FallbackChain,
    ModelCapability,
)
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

HINT = "当前模型链不支持图片识别"


def _setup_schema() -> None:
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
        finally:
            await conn.close()

    asyncio.run(_run())


class _MockAdapter:
    """可指定 vision 能力的 mock 适配器。"""

    def __init__(self, content: str = "ok", vision: bool = False, name: str = "mock"):
        self.provider_name = name
        self.capability = ModelCapability(
            streaming=False, function_calling=True, vision=vision, json_mode=False
        )
        self._content = content
        self.chat_calls = 0

    async def chat(self, messages, tools=None, max_tokens=None, require_vision=False):
        self.chat_calls += 1
        return ChatResult(content=self._content, used_provider=self.provider_name)


async def _run_image_turn(conn, tmp_path, *, vision_adapter, text_adapter):
    """在带图消息上跑一轮, 返回 (事件类型列表, 事件列表, text_adapter)。"""
    session_id = await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "vision-guard", "mock",
    )
    cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
    await cm.build_initial(conn)

    # 真实存在的 1x1 PNG(确保 _inject_image_urls 能读到并注入)
    import base64

    png = tmp_path / "shot.png"
    png.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
            "h6FO1AAAAABJRU5ErkJggg=="
        )
    )

    loop = ReactLoop(
        session_id=session_id,
        context_manager=cm,
        adapter=text_adapter,
        tools=[],
        conn=conn,
        # 关闭状态栏注入: 它是一条追加在末尾的 user-role meta 消息, 会覆盖
        # _messages_contain_image 的"最后一条 user 消息"文本引用检测
        # (生产 inject_every_iterations=3 时第 1 次迭代不注入, 故不受影响;
        #  本测试聚焦 vision 链判定, 显式关闭以隔离变量)。
        cfg={"context": {"status_bar": {"enabled": False}}},
        vision_adapter=vision_adapter,
    )
    await loop.run_turn(f"识别 [已上传文件: shot.png 路径: {png}]")

    events = []
    while not loop.event_queue.empty():
        events.append(loop.event_queue.get_nowait())
    return [e["event_type"] for e in events], events, text_adapter


def test_empty_vision_chain_emits_hint_not_all_providers_failed(tmp_path):
    """核心回归: vision 链为空(_adapters==[]) → 给出明确提示, 不抛
    AllProvidersFailedError("all 0 providers failed: []")。

    修复前: `is not None` 判定通过 → 空链替换模型链 → 抛错。
    修复后: 落 else 分支 → 全链也无多模态 → final 提示。
    """
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            text = _MockAdapter(content="文本回复")
            empty_vision = FallbackChain([])  # 模拟 vision_chain 上 provider 全禁用
            types, events, _ = await _run_image_turn(
                conn, tmp_path,
                vision_adapter=empty_vision,
                text_adapter=FallbackChain([text]),
            )
            return types, events
        finally:
            await conn.close()

    types, events = asyncio.run(_run())

    # 不得出现模型全失败的崩溃路径
    finals = [e for e in events if e["event_type"] == "final"]
    assert finals, f"应产出 final 提示事件, 实际事件序列: {types}"
    assert HINT in finals[0]["payload"]["content"], (
        f"应给出人话提示, 实际: {finals[0]['payload']['content']!r}"
    )
    # 带图轮不得把图丢给纯文本链硬猜
    assert "all 0 providers failed" not in str(events)


def test_empty_vision_chain_does_not_call_text_chain_with_image(tmp_path):
    """空 vision 链时, 文本链不应被拿来做图识别(mock 的 chat 调用数为 0)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            text = _MockAdapter(content="不该被调用")
            types, events, _ = await _run_image_turn(
                conn, tmp_path,
                vision_adapter=FallbackChain([]),
                text_adapter=FallbackChain([text]),
            )
            return text.chat_calls
        finally:
            await conn.close()

    assert asyncio.run(_run()) == 0


def test_none_vision_adapter_still_emits_hint(tmp_path):
    """旧构造(_vision_adapter is None)行为不回归: 同样走提示路径。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            text = _MockAdapter(content="文本回复")
            types, events, _ = await _run_image_turn(
                conn, tmp_path,
                vision_adapter=None,
                text_adapter=FallbackChain([text]),
            )
            return types, events
        finally:
            await conn.close()

    types, events = asyncio.run(_run())
    finals = [e for e in events if e["event_type"] == "final"]
    assert finals, f"应产出 final 提示, 实际: {types}"
    assert HINT in finals[0]["payload"]["content"]


def test_non_empty_vision_chain_still_switches(tmp_path):
    """非空 vision 链正常路径不回归: 切到视觉 adapter 并成功调用。

    视觉 adapter 的 chat 被调用 >0 次, 且不出现"不支持图片识别"提示。
    """
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            text = _MockAdapter(content="文本")
            vision = _MockAdapter(content="我看到了图片", vision=True, name="vision-mock")
            types, events, _ = await _run_image_turn(
                conn, tmp_path,
                vision_adapter=FallbackChain([vision]),
                text_adapter=FallbackChain([text]),
            )
            return types, events, vision.chat_calls
        finally:
            await conn.close()

    types, events, vision_calls = asyncio.run(_run())
    assert vision_calls > 0, f"视觉 adapter 应被调用, 实际事件: {types}"
    assert not any(
        e["event_type"] == "final" and HINT in str(e["payload"].get("content", ""))
        for e in events
    ), "非空 vision 链不应给出'不支持图片识别'提示"


def test_empty_vision_chain_direct_call_reproduces_original_error():
    """病根旁证: 空链直接调用 chat_stream 会抛 original 错误串
    (证明 "all 0 providers failed: []" 的确切来源, 即修复所绕开的路径)。"""
    chain = FallbackChain([])

    async def _run():
        try:
            await chain.chat_stream([{"role": "user", "content": "hi"}], None)
        except AllProvidersFailedError as e:
            return str(e)
        return None

    msg = asyncio.run(_run())
    assert msg is not None and "all 0 providers failed: []" in msg


# ──────────────────────────────────────────────────────────────────────────────
# A-3: 多模态能力声明必须与 enabled 一致(不再谎报)
# ──────────────────────────────────────────────────────────────────────────────


async def _system_prompt_with_cfg(conn, cfg: dict) -> str:
    from private_agent.main import _get_system_prompt

    sid = await conn.fetchval(
        "INSERT INTO sessions (title, model_id, kind) "
        "VALUES ('vision-note', 'mock', 'monitor') RETURNING id"
    )
    return await _get_system_prompt(cfg, sid, conn)


def _cfg(providers: dict, **router) -> dict:
    return {"models": {"providers": providers, "router": {"type": "manual", **router}}}


def test_vision_note_present_when_vision_chain_has_multimodal():
    """P5-1(看链不看字典): vision_chain 上有可用多模态 provider → 声明能力。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await _system_prompt_with_cfg(
                conn,
                _cfg(
                    {"glm-vision": {"multimodal": True, "enabled": True}},
                    vision_chain=["glm-vision"],
                ),
            )
        finally:
            await conn.close()

    assert "你具备图片识别能力" in asyncio.run(_run())


def test_vision_note_absent_when_multimodal_disabled():
    """回归: multimodal=true 但 enabled=false → 链上被过滤 → 不得声明能力。

    缺陷期: 判定只看 multimodal → 已禁用的 glm-vision 仍让系统提示谎报
    → AI 自我分析误判视觉能力 → 误导用户上传图片实测 → 撞上空链崩溃。
    """
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await _system_prompt_with_cfg(
                conn,
                _cfg(
                    {"glm-vision": {"multimodal": True, "enabled": False}},
                    vision_chain=["glm-vision"],
                    fallback_chain=[],
                ),
            )
        finally:
            await conn.close()

    assert "你具备图片识别能力" not in asyncio.run(_run()), (
        "已禁用/已删除的多模态 provider 不应触发能力声明(谎报)"
    )


def test_vision_note_present_when_vision_chain_empty_but_fallback_has_multimodal():
    """生产现状回归: vision_chain 为空 → 回退 fallback_chain, 其中有多模态
    provider 时**仍应声明能力**(发图轮实际就会用它, 声明必须与实际行为一致)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await _system_prompt_with_cfg(
                conn,
                _cfg(
                    {
                        "deepseek-flash": {"enabled": True, "multimodal": False},
                        "step-3.7-flash": {"enabled": True, "multimodal": True},
                    },
                    vision_chain=[],
                    fallback_chain=["deepseek-flash", "step-3.7-flash"],
                ),
            )
        finally:
            await conn.close()

    assert "你具备图片识别能力" in asyncio.run(_run())


def test_vision_note_absent_when_no_multimodal_anywhere():
    """全链无多模态 provider → 不得声明能力。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await _system_prompt_with_cfg(
                conn,
                _cfg(
                    {"deepseek-flash": {"enabled": True, "multimodal": False}},
                    vision_chain=[],
                    fallback_chain=["deepseek-flash"],
                ),
            )
        finally:
            await conn.close()

    assert "你具备图片识别能力" not in asyncio.run(_run())
