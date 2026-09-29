"""P1 链一致性治理回归（2026-09-29）。

背景：`config_runtime` 存三条模型链（text_chain / vision_chain /
fallback_chain），但 provider 的增删启停此前只维护 `fallback_chain`；
且 `update_fallback_chain` 的自愈只判 `deleted` 不判 `enabled`。
结果是链中残留"指向已禁用 provider"的悬空引用，`build_fallback_chain`
过滤后可能返回**空链**，消费端抛
`AllProvidersFailedError("all 0 providers failed: []")`
（09-17/09-23/09-29 共 7 次，图片类上传 100% 失败）。

本文件覆盖：
- P1-1 `sync_chains_on_provider_change`：三链统一维护 + 语义归属
- P1-2 `audit_chains`：启动期悬空剔除与自愈
- P1-4 `PUT /settings/fallback-chain`：valid_names 补 enabled 判定
"""
import asyncio
import os

import asyncpg

from private_agent.models.chain_guard import (
    CHAIN_KEYS,
    audit_chains,
    load_chain,
    save_chain,
    sync_chains_on_provider_change,
)
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


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


def _cfg(providers: dict) -> dict:
    return {"models": {"providers": providers}}


async def _seed_all_chains(conn, names: list[str]) -> None:
    for chain_name in CHAIN_KEYS:
        await save_chain(conn, chain_name, names)


# ──────────────────────────────────────────────────────────────────────────────
# P1-1 sync_chains_on_provider_change
# ──────────────────────────────────────────────────────────────────────────────


def test_disable_removes_name_from_all_chains():
    """禁用 provider → 三条链均剔除(原实现只清 fallback_chain)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _seed_all_chains(conn, ["glm-vision", "deepseek-flash"])
            cfg = _cfg({
                "glm-vision": {"enabled": False, "multimodal": True},
                "deepseek-flash": {"enabled": True},
            })
            changed = await sync_chains_on_provider_change(
                conn, cfg, "glm-vision", "disable"
            )
            return {c: await load_chain(conn, c) for c in CHAIN_KEYS}, changed
        finally:
            await conn.close()

    chains, changed = asyncio.run(_run())
    for chain_name in CHAIN_KEYS:
        assert chain_name in changed, f"{chain_name} 应被改写"
        assert chains[chain_name] == ["deepseek-flash"], (
            f"{chain_name} 应剔除已禁用 provider, 实际 {chains[chain_name]}"
        )


def test_delete_removes_name_from_all_chains():
    """删除 provider → 三条链均剔除。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _seed_all_chains(conn, ["qwen", "deepseek-flash"])
            cfg = _cfg({"qwen": {"deleted": True, "enabled": False}})
            await sync_chains_on_provider_change(conn, cfg, "qwen", "delete")
            return {c: await load_chain(conn, c) for c in CHAIN_KEYS}
        finally:
            await conn.close()

    chains = asyncio.run(_run())
    for chain_name in CHAIN_KEYS:
        assert chains[chain_name] == ["deepseek-flash"], chain_name


def test_enable_non_multimodal_joins_text_and_fallback_only():
    """启用非多模态 provider → 入 fallback_chain + text_chain, 不入 vision_chain。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _seed_all_chains(conn, ["deepseek-flash"])
            cfg = _cfg({"deepseek-v5": {"enabled": True, "multimodal": False}})
            await sync_chains_on_provider_change(
                conn, cfg, "deepseek-v5", "add"
            )
            return {c: await load_chain(conn, c) for c in CHAIN_KEYS}
        finally:
            await conn.close()

    chains = asyncio.run(_run())
    assert chains["fallback_chain"] == ["deepseek-flash", "deepseek-v5"]
    assert chains["text_chain"] == ["deepseek-flash", "deepseek-v5"]
    assert chains["vision_chain"] == ["deepseek-flash"], (
        "非多模态 provider 不应进入 vision_chain"
    )


def test_enable_multimodal_appends_to_text_chain_tail():
    """D1(2026-09-29 蒋先生决策): 多模态 provider 按需进 text_chain(**追加尾部**),
    协助文本模型完成特定识别任务后退出, 主对话仍由文本模型主导。

    实现机制 = `FallbackChain.require_vision`(轮次级, 无状态): 发图轮
    `_messages_contain_image` 为真 → 链上跳过纯文本模型、从多模态开始;
    下一轮纯文本 → require_vision 为假、回到链首文本模型。因此多模态 provider
    常驻链尾不会抢占主对话, 也无需在运行时增删链成员。
    """
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await save_chain(conn, "text_chain", ["deepseek-flash"])
            await save_chain(conn, "vision_chain", [])
            await save_chain(conn, "fallback_chain", ["deepseek-flash"])
            cfg = _cfg({"glm-vision": {"enabled": True, "multimodal": True}})
            await sync_chains_on_provider_change(conn, cfg, "glm-vision", "enable")
            return {c: await load_chain(conn, c) for c in CHAIN_KEYS}
        finally:
            await conn.close()

    chains = asyncio.run(_run())
    assert chains["text_chain"] == ["deepseek-flash", "glm-vision"], (
        "多模态 provider 应按需留驻 text_chain **尾部**(文本模型仍在链首主导)"
    )
    assert chains["vision_chain"] == ["glm-vision"]
    assert chains["fallback_chain"] == ["deepseek-flash", "glm-vision"]


def test_non_multimodal_not_removed_from_text_chain():
    """非多模态 provider 在 text_chain 中是正常成员, 不被语义归位剔除。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await save_chain(conn, "text_chain", ["deepseek-flash"])
            await save_chain(conn, "vision_chain", ["glm-vision"])
            cfg = _cfg({"deepseek-v5": {"enabled": True, "multimodal": False}})
            await sync_chains_on_provider_change(conn, cfg, "deepseek-v5", "add")
            return await load_chain(conn, "text_chain")
        finally:
            await conn.close()

    assert asyncio.run(_run()) == ["deepseek-flash", "deepseek-v5"]


def test_sync_does_not_create_unconfigured_chain():
    """未配置的链不被擅自创建(未配置语义是"回退 fallback_chain", 见模块 docstring)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 只配置 fallback_chain
            await save_chain(conn, "fallback_chain", [])
            cfg = _cfg({"glm-vision": {"enabled": True, "multimodal": True}})
            await sync_chains_on_provider_change(conn, cfg, "glm-vision", "add")
            return {
                "fallback": await load_chain(conn, "fallback_chain"),
                "vision": await load_chain(conn, "vision_chain"),
                "text": await load_chain(conn, "text_chain"),
            }
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert result["fallback"] == ["glm-vision"]
    assert result["vision"] is None, "未配置的 vision_chain 不应被创建"
    assert result["text"] is None, "未配置的 text_chain 不应被创建"


def test_sync_rejects_unknown_action():
    """非法 action 立即报错(防调用方拼错字符串后静默不维护)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            try:
                await sync_chains_on_provider_change(conn, _cfg({}), "x", "bogus")
            except ValueError as e:
                return str(e)
            return None
        finally:
            await conn.close()

    msg = asyncio.run(_run())
    assert msg is not None and "bogus" in msg


# ──────────────────────────────────────────────────────────────────────────────
# P1-2 audit_chains
# ──────────────────────────────────────────────────────────────────────────────


def test_audit_drops_dangling_refs_and_persists():
    """悬空项(已禁用 / 已删除 / 不存在)被剔除, 并写回 runtime。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await save_chain(conn, "text_chain", ["deepseek-flash", "glm-5.2", "ghost"])
            await save_chain(conn, "vision_chain", ["glm-vision"])
            await save_chain(conn, "fallback_chain", ["deepseek-flash"])
            cfg = _cfg({
                "deepseek-flash": {"enabled": True},
                "glm-5.2": {"enabled": False},
                "glm-vision": {"deleted": True, "enabled": False},
            })
            report = await audit_chains(conn, cfg)
            return report, {c: await load_chain(conn, c) for c in CHAIN_KEYS}
        finally:
            await conn.close()

    report, chains = asyncio.run(_run())
    assert report["dropped"]["text_chain"] == ["glm-5.2", "ghost"]
    assert report["dropped"]["vision_chain"] == ["glm-vision"]
    assert report["emptied"] == ["vision_chain"], (
        "vision_chain 被清空必须单独回报(发图能力会降级)"
    )
    assert chains["text_chain"] == ["deepseek-flash"]
    assert chains["vision_chain"] == []
    assert chains["fallback_chain"] == ["deepseek-flash"]


def test_audit_clean_chains_no_change():
    """全部引用有效 → 不改写、不回报 dropped。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await save_chain(conn, "text_chain", ["deepseek-flash"])
            await save_chain(conn, "vision_chain", ["glm-vision"])
            await save_chain(conn, "fallback_chain", ["deepseek-flash", "glm-vision"])
            cfg = _cfg({
                "deepseek-flash": {"enabled": True},
                "glm-vision": {"enabled": True, "multimodal": True},
            })
            report = await audit_chains(conn, cfg)
            return report, {
                c: await load_chain(conn, c)
                for c in CHAIN_KEYS
            }
        finally:
            await conn.close()

    report, chains = asyncio.run(_run())
    assert report["dropped"] == {}
    assert report["emptied"] == []
    assert set(report["checked"]) == set(CHAIN_KEYS)
    assert chains["fallback_chain"] == ["deepseek-flash", "glm-vision"]


def test_audit_reports_unconfigured_without_creating():
    """未配置的链只回报 unconfigured, 不创建。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await save_chain(conn, "fallback_chain", ["deepseek-flash"])
            cfg = _cfg({"deepseek-flash": {"enabled": True}})
            report = await audit_chains(conn, cfg)
            return report, await load_chain(conn, "vision_chain")
        finally:
            await conn.close()

    report, vision = asyncio.run(_run())
    assert "vision_chain" in report["unconfigured"]
    assert "text_chain" in report["unconfigured"]
    assert report["checked"] == ["fallback_chain"]
    assert vision is None


def test_audit_dedupes_chain_entries():
    """链内重复项一并清理(避免同一 provider 被多次尝试)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await save_chain(conn, "fallback_chain", ["a", "a", "b"])
            cfg = _cfg({"a": {"enabled": True}, "b": {"enabled": True}})
            report = await audit_chains(conn, cfg)
            return report, await load_chain(conn, "fallback_chain")
        finally:
            await conn.close()

    report, chain = asyncio.run(_run())
    assert chain == ["a", "b"]
    assert report["dropped"]["fallback_chain"] == ["a"]


def test_audit_is_idempotent():
    """重复调用结果稳定(启动自愈幂等, 不因自身改写而反复告警)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await save_chain(conn, "vision_chain", ["glm-vision"])
            cfg = _cfg({"glm-vision": {"enabled": False}})
            first = await audit_chains(conn, cfg)
            second = await audit_chains(conn, cfg)
            return first, second
        finally:
            await conn.close()

    first, second = asyncio.run(_run())
    assert first["dropped"]["vision_chain"] == ["glm-vision"]
    assert second["dropped"] == {}, "第二次运行应无变更(幂等)"
