"""模型链一致性治理(P1, 2026-09-29)。

问题
----
`config_runtime` 存三条独立链: `models.router.{text_chain, vision_chain,
fallback_chain}`。但 provider 的增/删/启/停此前只维护 `fallback_chain`
(见 `api/admin.py`), `text_chain` 与 `vision_chain` 会被遗留**悬空引用**,
且这两条链没有任何管理入口(后端无端点、前端无 UI)。

`registry.build_fallback_chain` 会过滤掉 `enabled=false` 的 provider,
过滤后可能得到**空链对象**(非 None)。消费端曾因此在发图轮抛
`AllProvidersFailedError("all 0 providers failed: []")`
—— 2026-09-17 / 09-23 / 09-29 共 7 次, 图片类上传 100% 失败, 静默 3 周
(详见 `docs/diagnosis-2026-09-29-vision-chain-empty.md`)。

本模块
------
- `CHAIN_KEYS` / `chain_runtime_key`: 三条链的 key 约定
- `load_chain` / `save_chain`: 链读写(**区分"未配置"与"空链"**)
- `sync_chains_on_provider_change`: provider 状态变更后统一维护三条链
- `audit_chains`: 启动期校验 + 自愈(剔除悬空项并写回 runtime)

设计约束
--------
1. **不主动创建未配置的链**。未配置的语义是"回退 fallback_chain"
   (见 `registry.build_fallback_chain`), 擅自创建会改变路由语义 ——
   例如 `vision_chain` 未配置时发图轮本应回退全链, 创建后会被锁成单链。
2. 只依赖 asyncpg 连接, **不 import `api.admin`**(避免循环导入),
   upsert 语句在本模块内自持。
3. 所有函数幂等, 可重复调用。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

#: 三条模型链(与 `config.yaml` / 蓝图 §2.9 的 router 段对应)
CHAIN_KEYS: tuple[str, ...] = ("text_chain", "vision_chain", "fallback_chain")

#: provider 状态变更动作
VALID_ACTIONS: tuple[str, ...] = ("add", "enable", "disable", "delete")

_RUNTIME_PREFIX = "models.router."


def chain_runtime_key(chain_name: str) -> str:
    """链名 → config_runtime 点分 key。"""
    return f"{_RUNTIME_PREFIX}{chain_name}"


def _as_name_list(value: Any) -> list[str]:
    """把 config_runtime 的值规整为 list[str]。

    asyncpg 对 JSONB 列返回 JSON 字符串, 需先解析(项目既有约定);
    解析失败/非列表 → 空列表(不抛, 由调用方按空链处理)。
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [str(v) for v in value]


async def load_chain(conn, chain_name: str) -> list[str] | None:
    """读取一条链。

    Returns:
        链内容; **runtime 无该 key 时返回 None**(表示"未配置", 与"空链"语义
        不同 —— 未配置时 `build_fallback_chain` 会回退 `fallback_chain`)。
    """
    row = await conn.fetchval(
        "SELECT value FROM config_runtime WHERE key = $1",
        chain_runtime_key(chain_name),
    )
    if row is None:
        return None
    return _as_name_list(row)


async def save_chain(conn, chain_name: str, names: Iterable[str]) -> None:
    """写回一条链(upsert 整体列表)。"""
    await conn.execute(
        """
        INSERT INTO config_runtime (key, value)
        VALUES ($1, $2::jsonb)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        chain_runtime_key(chain_name),
        json.dumps(list(names)),
    )


def _chain_should_contain(chain_name: str, multimodal: bool) -> bool:
    """该 provider 按语义是否应属于这条链。

    - `vision_chain`(多模态优先): 仅多模态 provider 入链。
    - `text_chain`(纯文本优先): 仅非多模态 provider 入链。
    - `fallback_chain`(总降级链): 一律可入。
    """
    if chain_name == "vision_chain":
        return multimodal
    if chain_name == "text_chain":
        return not multimodal
    return True


async def sync_chains_on_provider_change(
    conn,
    cfg: dict,
    name: str,
    action: str,
) -> dict[str, list[str]]:
    """provider 增/删/启/停后, 统一维护三条链(只维护**已配置**的链)。

    Args:
        conn: asyncpg 连接。
        cfg: 变更**之后**的配置(含最新的 providers 状态)。
        name: provider 名。
        action: `add` / `enable` / `disable` / `delete`。

    Returns:
        被改写的链 → 新内容(未变更的链不出现)。

    Raises:
        ValueError: action 不在 VALID_ACTIONS 内。
    """
    if action not in VALID_ACTIONS:
        raise ValueError(
            f"unknown action {action!r}, expected one of {VALID_ACTIONS}"
        )

    providers = (cfg.get("models") or {}).get("providers", {}) or {}
    prov = providers.get(name) or {}
    multimodal = bool(prov.get("multimodal"))
    removing = action in ("disable", "delete")

    changed: dict[str, list[str]] = {}
    for chain_name in CHAIN_KEYS:
        current = await load_chain(conn, chain_name)
        if current is None:
            if chain_name != "fallback_chain":
                # 未配置的 text/vision 链: 保持"回退 fallback_chain"语义,
                # 不擅自创建(见模块 docstring)
                continue
            # fallback_chain 是 build_fallback_chain 的**默认链**, 必须有基线
            # (否则首个 provider 无法入链, "添加即入链"的既有行为会回归)。
            # 基线取 cfg 中 router.fallback_chain(已合并 runtime; 无则空列表)。
            current = list(
                ((cfg.get("models") or {}).get("router") or {}).get(
                    "fallback_chain", []
                )
            )

        if removing:
            new = [c for c in current if c != name]
        elif not _chain_should_contain(chain_name, multimodal):
            # 语义不符(如多模态 provider 之于 text_chain) → 主动剔除历史残留
            new = [c for c in current if c != name]
        elif name in current:
            new = current
        else:
            new = [*current, name]

        if new != current:
            await save_chain(conn, chain_name, new)
            changed[chain_name] = new

    if changed:
        logger.info(
            "model chains synced after provider %s action=%s: %s",
            name, action, {k: len(v) for k, v in changed.items()},
        )
    return changed


async def audit_chains(conn, cfg: dict) -> dict[str, Any]:
    """启动期一致性校验与自愈: 剔除链中的悬空引用。

    悬空判据(任一成立): provider 不存在 / `deleted=true` / `enabled=false`。
    剔除后写回 runtime(仅当确有变更), 并回报结果供启动日志告警。

    Args:
        conn: asyncpg 连接。
        cfg: 合并 runtime 覆盖后的配置(建议 `load_config_with_overrides`)。

    Returns:
        {
          "checked": 实际读取到的链名,
          "dropped": {链名: [被剔除的 provider]},
          "emptied": [被清空的链名],        # 需重点告警(如 vision_chain 清空)
          "unconfigured": [未配置的链名],   # 未配置 ≠ 空链, 不创建
        }
    """
    providers = (cfg.get("models") or {}).get("providers", {}) or {}

    checked: list[str] = []
    dropped: dict[str, list[str]] = {}
    emptied: list[str] = []
    unconfigured: list[str] = []

    for chain_name in CHAIN_KEYS:
        current = await load_chain(conn, chain_name)
        if current is None:
            unconfigured.append(chain_name)
            continue
        checked.append(chain_name)

        valid: list[str] = []
        bad: list[str] = []
        for ref in current:
            prov = providers.get(ref)
            invalid = (
                not prov
                or bool(prov.get("deleted"))
                or not prov.get("enabled", True)
            )
            if invalid:
                bad.append(ref)
            elif ref not in valid:
                valid.append(ref)
            else:
                bad.append(ref)  # 重复项一并清理

        if valid != current:
            await save_chain(conn, chain_name, valid)
        if bad:
            dropped[chain_name] = bad
            if not valid:
                emptied.append(chain_name)

    if dropped:
        logger.warning(
            "model chain self-healed: dropped=%s emptied=%s", dropped, emptied
        )
    return {
        "checked": checked,
        "dropped": dropped,
        "emptied": emptied,
        "unconfigured": unconfigured,
    }
