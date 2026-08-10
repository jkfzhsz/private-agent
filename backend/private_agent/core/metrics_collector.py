"""0.5.0 P1: 系统指标采集器(主智能体监控数据源)。

职责:
1. 周期性采集系统级指标(CPU/内存/进程) —— psutil
2. 采集服务级指标(WS 连接数/活跃 turn 数) —— 由 main.py 注入计数器
3. 聚合 react_events(会话 token 用量/工具失败率) —— 落库 system_metrics

设计(见 docs/next-phase-plan-2026-08-08-four-windows.md §4.1):
- 默认 60s 间隔, apscheduler 后台任务驱动(轻量, 不阻塞请求)
- 指标以 (kind, name, ts) 维度存储, 消费方按 name 过滤 + ts 范围聚合
- 采集失败不抛出(静默降级, 记录 meta.error), 保证监控链路不影响主流程
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# 采集间隔(秒), config 可覆盖: config.system.metrics.interval_sec
DEFAULT_INTERVAL_SEC = 60.0
# 指标保留窗口: 仅保留最近 N 小时(防无限膨胀)
METRICS_RETENTION_HOURS = 72
# 每轮采集批量写入上限(防慢连接拖垮采集)
BATCH_INSERT_LIMIT = 200


class MetricsCollector:
    """周期采集系统/服务/会话指标并落库 system_metrics。

    Args:
        db: asyncpg 连接池工厂(可调用返回连接)。
        interval_sec: 采集间隔(秒)。
    """

    def __init__(
        self,
        db: Any,
        interval_sec: float = DEFAULT_INTERVAL_SEC,
    ) -> None:
        self._db = db
        self._interval_sec = interval_sec
        # 外部注入的运行时计数器(由 main.py 每轮更新):
        # ws_conns / active_turns / provider_ok / provider_fail
        self.runtime_stats: dict[str, float] = {}
        self._last_run: datetime | None = None

    async def collect_once(self, conn: asyncpg.Connection) -> dict[str, float]:
        """采集一次并落库, 返回本次指标 dict(供测试断言/即时 system_status)。"""
        now = datetime.now(timezone.utc)
        rows: list[tuple[str, str, float, dict]] = []

        # ── 系统级(psutil, 不可用时跳过) ─────────────────────────────────
        try:
            import psutil

            vm = psutil.virtual_memory()
            rows.append(("system", "cpu_percent", psutil.cpu_percent(interval=0.2), {}))
            rows.append(("system", "ram_used_mb", round(vm.used / 1024 / 1024, 1), {}))
            rows.append(("system", "ram_percent", vm.percent, {}))
            rows.append(("system", "process_count", float(len(psutil.pids())), {}))
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("metrics: psutil 采集失败: %s", e)

        # ── 服务级(运行时计数器) ─────────────────────────────────────────
        for name in ("ws_conns", "active_turns", "provider_ok", "provider_fail"):
            rows.append(("system", name, float(self.runtime_stats.get(name, 0.0)), {}))

        # ── 会话级(react_events 聚合: 最近 30 分钟) ─────────────────────
        try:
            agg = await self._aggregate_session_metrics(conn, now)
            for session_id, metrics in agg.items():
                for name, value in metrics.items():
                    rows.append(("session", name, value, {"session_id": session_id}))
        except Exception as e:  # noqa: BLE001
            logger.warning("metrics: react_events 聚合失败: %s", e)

        # ── 落库(批量, 限流) ────────────────────────────────────────────
        if rows:
            await conn.executemany(
                """
                INSERT INTO system_metrics (ts, kind, session_id, name, value, meta)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                [
                    (now, kind, meta.get("session_id"), name, value,
                     json.dumps(meta, ensure_ascii=False))
                    for kind, name, value, meta in rows
                ],
            )
        # 清理过期指标(每次采集顺带, 低频)
        try:
            await conn.execute(
                "DELETE FROM system_metrics WHERE ts < $1",
                now - timedelta(hours=METRICS_RETENTION_HOURS),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("metrics: 清理过期指标失败: %s", e)
        self._last_run = now
        return {name: value for _, name, value, _ in rows}

    async def _aggregate_session_metrics(
        self,
        conn: asyncpg.Connection,
        now: datetime,
    ) -> dict[int, dict[str, float]]:
        """聚合 react_events 最近 30 分钟, 按会话统计 token 用量/工具失败率。

        Returns:
            {session_id: {turn_count, tool_calls, tool_failures, total_tokens}}
        """
        cutoff = now - timedelta(minutes=30)
        rows = await conn.fetch(
            """
            SELECT session_id,
                   event_type,
                   COUNT(*) AS cnt
            FROM react_events
            WHERE created_at >= $1
            GROUP BY session_id, event_type
            """,
            cutoff,
        )
        agg: dict[int, dict[str, float]] = {}
        for r in rows:
            sid = r["session_id"]
            if sid is None:
                continue
            d = agg.setdefault(sid, {
                "turn_count": 0.0, "tool_calls": 0.0,
                "tool_failures": 0.0, "total_tokens": 0.0,
            })
            et = r["event_type"]
            if et == "final":
                d["turn_count"] += r["cnt"]
            elif et == "tool_call":
                d["tool_calls"] += r["cnt"]
            elif et == "tool_error":
                d["tool_failures"] += r["cnt"]
            elif et in ("delta", "thinking"):
                # delta/thinking 按条数近似 token 消耗(粗估, 精确用量走 billing)
                d["total_tokens"] += r["cnt"] * 20.0
        return agg

    async def query(
        self,
        conn: asyncpg.Connection,
        *,
        names: list[str] | None = None,
        since_hours: float = 24.0,
        session_id: int | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """查询历史指标(供 system_metrics_query 工具)。

        Args:
            names: 指标名过滤(空=全部)。
            since_hours: 最近 N 小时。
            session_id: 会话过滤(可选)。
            limit: 返回条数上限。

        Returns:
            [{ts, kind, session_id, name, value, meta}]
        """
        where = ["ts >= $1"]
        params: list[Any] = [
            datetime.now(timezone.utc) - timedelta(hours=since_hours),
        ]
        if names:
            where.append(f"name = ANY(${len(params) + 1})")
            params.append(list(names))
        if session_id is not None:
            where.append(f"session_id = ${len(params) + 1}")
            params.append(session_id)
        sql = (
            "SELECT ts, kind, session_id, name, value, meta "
            "FROM system_metrics WHERE "
            + " AND ".join(where)
            + f" ORDER BY ts DESC LIMIT {int(limit)}"
        )
        rows = await conn.fetch(sql, *params)
        return [dict(r) for r in rows]

    async def latest_summary(
        self,
        conn: asyncpg.Connection,
        *,
        since_hours: float = 1.0,
    ) -> str:
        """生成最近指标摘要文本(主智能体注入用, ≤400 token)。

        Returns:
            形如 "[System Metrics] 最近1小时: cpu 38% · ram 62% · ws 4/4 · ..."
            无数据时返回空字符串。
        """
        rows = await self.query(
            conn, since_hours=since_hours, limit=300
        )
        if not rows:
            return ""
        # 按 name 聚合最近值(取最后一条)
        latest: dict[str, float] = {}
        for r in rows:
            latest[r["name"]] = r["value"]
        parts: list[str] = []
        for name, value in latest.items():
            if name == "cpu_percent":
                parts.append(f"cpu {value:.0f}%")
            elif name == "ram_percent":
                parts.append(f"ram {value:.0f}%")
            elif name == "ws_conns":
                parts.append(f"ws {value:.0f} 连接")
            elif name == "active_turns":
                parts.append(f"活跃轮次 {value:.0f}")
            elif name == "tool_failures":
                parts.append(f"工具失败 {value:.0f} 次")
            elif name == "total_tokens":
                parts.append(f"近30min tokens≈{value:.0f}")
        if not parts:
            return ""
        return "[System Metrics] 最近" + (f"{since_hours:g}小时" if since_hours >= 1 else f"{since_hours*60:.0f}分钟") + ": " + " · ".join(parts)
