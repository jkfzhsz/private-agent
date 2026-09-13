"""0.6.0 F1-2: mission 工具集单测(mission_create/status/control)。

mock conn(asyncpg 风格 FakeConn, 记录 SQL 与参数、内存行)验证 handler 逻辑,
不连真实 DB —— 模式同 test_mcp_browse.py(无 DB 依赖纯单测)。

覆盖(设计文档 F1-2 验收):
- create: charter/plan 校验(缺 goal/空 plan/非法 executor_type/重复 id)、
  并发上限拒绝、建行返回 id、runner_factory None 时不 spawn(TODO D-1 接线点)
- status: 存在/不存在、JSONB str 解析(2026-08-15 教训)、预算消耗统计
- control: approve_fallback(仅 escalated)、abort(终态拒绝)、pause(仅运行态)、
  未知 action、_rowcount 状态串解析
"""
import asyncio
import json

import pytest

from private_agent.tools.builtins.mission_tools import (
    build_mission_tools,
    mission_cfg,
)


class FakeConn:
    """asyncpg 最小 mock: fetchval/fetchrow/execute, 记录调用。"""

    def __init__(self, running_missions: int = 0):
        self.running_missions = running_missions
        self.inserted: list[dict] = []
        self.rows: dict[int, dict] = {}
        self._next_id = 100
        self.executed: list[str] = []

    async def fetchval(self, sql: str, *args):
        if "SELECT count(*) FROM missions" in sql:
            return self.running_missions
        if "RETURNING id" in sql:
            self._next_id += 1
            mid = self._next_id
            self.rows[mid] = {
                "id": mid,
                "charter": json.loads(args[1]),
                "plan": json.loads(args[2]),
                "budget": json.loads(args[3]),
                "journal": json.loads(args[4]),
                "state": "planning",
                "error": None,
                "created_at": None,
                "completed_at": None,
            }
            self.inserted.append(self.rows[mid])
            return mid
        return None

    async def fetchrow(self, sql: str, *args):
        if "FROM missions WHERE id=$1" in sql and "budget" in sql and "state" in sql:
            return self.rows.get(int(args[0]))
        if "FROM missions WHERE id=$1" in sql:
            row = self.rows.get(int(args[0]))
            if row is None:
                return None
            return {
                "id": row["id"], "charter": json.dumps(row["charter"]),
                "plan": json.dumps(row["plan"]),
                "budget": json.dumps(row["budget"]),
                "journal": json.dumps(row["journal"]),
                "state": row["state"], "error": row["error"],
                "created_at": row["created_at"],
                "completed_at": row["completed_at"],
            }
        return None

    async def execute(self, sql: str, *args):
        self.executed.append(sql)
        mid = int(args[0])
        row = self.rows.get(mid)
        if row is None:
            return "UPDATE 0"
        # 模拟条件更新(WHERE state IN ...)
        if "state='escalated'" in sql.replace("\n", " "):
            if row["state"] != "escalated":
                return "UPDATE 0"
            row["budget"] = json.loads(args[1])
            row["journal"] = row["journal"] + json.loads(args[2])
            row["state"] = "executing"
            return "UPDATE 1"
        if "state='cancelled'" in sql.replace("\n", " "):
            if row["state"] in ("done", "failed", "cancelled"):
                return "UPDATE 0"
            row["journal"] = row["journal"] + json.loads(args[1])
            row["state"] = "cancelled"
            return "UPDATE 1"
        if "state='paused'" in sql.replace("\n", " "):
            if row["state"] not in ("executing", "supervising"):
                return "UPDATE 0"
            row["journal"] = row["journal"] + json.loads(args[1])
            row["state"] = "paused"
            return "UPDATE 1"
        return "UPDATE 0"


def _tools(running: int = 0, runner_factory=None, room_scope: bool = False):
    conn = FakeConn(running_missions=running)
    tools = build_mission_tools(
        conn=conn, cfg={}, session_id=1, runner_factory=runner_factory,
        room_scope=room_scope,
    )
    by_name = {t.name: t for t in tools}
    return conn, by_name


VALID_ARGS = {
    "charter": {"goal": "整理 56 页幻灯片视觉缺陷清单", "dod": ["逐页验证通过"]},
    "plan": [
        {"id": "m1", "milestone": "扫描全部页面", "executor_type": "subagent"},
        {"id": "m2", "milestone": "汇总缺陷 CSV", "executor_type": "script"},
    ],
}


# ── mission_create ──────────────────────────────────────────────────────

def test_create_happy_path_no_runner():
    """合法创建 → 返回 mission_id, 提示待 MissionRunner 装配(不 spawn)。"""
    conn, by_name = _tools()
    res = asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    assert not res.error
    assert res.metadata["mission_id"] == 101
    assert len(conn.inserted) == 1
    assert conn.inserted[0]["state"] == "planning"
    assert "待 MissionRunner 装配" in res.output


def test_create_with_runner_factory_spawns():
    """runner_factory 注入 → create 时被调用(D-1 接线点语义)。"""
    spawned: list[int] = []

    async def factory(mid: int) -> None:
        spawned.append(mid)

    conn, by_name = _tools(runner_factory=factory)
    res = asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    assert not res.error
    assert spawned == [res.metadata["mission_id"]]
    assert "后台编排已启动" in res.output


def test_create_missing_goal():
    conn, by_name = _tools()
    res = asyncio.run(by_name["mission_create"].handler(
        {"charter": {"dod": ["x"]}, "plan": VALID_ARGS["plan"]}
    ))
    assert res.error and "charter.goal 必填" in res.error


def test_create_empty_plan():
    conn, by_name = _tools()
    res = asyncio.run(by_name["mission_create"].handler(
        {"charter": {"goal": "g"}, "plan": []}
    ))
    assert res.error and "非空数组" in res.error


def test_create_bad_executor_type():
    conn, by_name = _tools()
    bad = {"charter": {"goal": "g"}, "plan": [
        {"id": "m1", "milestone": "x", "executor_type": "delegate"}
    ]}
    res = asyncio.run(by_name["mission_create"].handler(bad))
    assert res.error and "executor_type" in res.error


def test_create_duplicate_plan_id():
    conn, by_name = _tools()
    bad = {"charter": {"goal": "g"}, "plan": [
        {"id": "m1", "milestone": "x", "executor_type": "script"},
        {"id": "m1", "milestone": "y", "executor_type": "script"},
    ]}
    res = asyncio.run(by_name["mission_create"].handler(bad))
    assert res.error and "重复" in res.error


# ── 0.6.0 P3(W4): plan[].role 会议室准入 ─────────────────────────────────

def test_create_role_accepted_in_room_scope():
    """会议室会话: 合法角色放行, 建行成功。"""
    conn, by_name = _tools(room_scope=True)
    args = {
        "charter": {"goal": "做一份 Q3 经营分析汇报 PPT"},
        "plan": [
            {"id": "m1", "milestone": "转制商务 PPT",
             "executor_type": "subagent", "role": "frontend_design"},
        ],
    }
    res = asyncio.run(by_name["mission_create"].handler(args))
    assert not res.error
    assert len(conn.inserted) == 1


@pytest.mark.parametrize("bad_role", ["monitor", "无涯", "ghost", 123, ["office"]])
def test_create_role_rejected_illegal_value(bad_role):
    """非法角色(含 monitor/非字符串)一律拒绝, 不留 mission 行。

    注意 ``""`` 不在此列: role="" 与 None 同义(未指定), 与 runner 侧
    ``if role:`` 的真值判定一致 —— schema enum 已约束模型不会传空串。
    """
    conn, by_name = _tools(room_scope=True)
    args = {
        "charter": {"goal": "g"},
        "plan": [
            {"id": "m1", "milestone": "x", "executor_type": "subagent",
             "role": bad_role},
        ],
    }
    res = asyncio.run(by_name["mission_create"].handler(args))
    assert res.error and "非法" in res.error
    assert conn.inserted == []


def test_create_role_rejected_outside_room():
    """非会议室会话: 携带 role 的 plan 创建时即拒绝(而非运行期静默降级)。"""
    conn, by_name = _tools(room_scope=False)
    args = {
        "charter": {"goal": "g"},
        "plan": [
            {"id": "m1", "milestone": "x", "executor_type": "subagent",
             "role": "frontend_design"},
        ],
    }
    res = asyncio.run(by_name["mission_create"].handler(args))
    assert res.error and "仅会议室会话可用" in res.error
    assert conn.inserted == []


def test_create_role_absent_or_null_ok_outside_room():
    """非会议室会话不带 role(或显式 None/空串) → 完全兼容既有行为(零回归)。"""
    for extra in (None, {}, {"role": None}, {"role": ""}):
        conn, by_name = _tools(room_scope=False)
        item = {"id": "m1", "milestone": "x", "executor_type": "wait",
                "wait_sec": 0.1}
        item.update(extra or {})
        res = asyncio.run(by_name["mission_create"].handler(
            {"charter": {"goal": "g"}, "plan": [item]}
        ))
        assert not res.error, extra


def test_role_enum_exposed_in_schema():
    """role 出现在 mission_create 的 plan items schema(模型可见性)。"""
    _, by_name = _tools()
    schema = by_name["mission_create"].parameters_schema
    props = schema["properties"]["plan"]["items"]["properties"]
    assert props["role"]["enum"] == ["office", "data_analysis", "frontend_design"]
    assert "role" not in schema["properties"]["plan"]["items"]["required"]


def test_create_rejects_when_running_at_cap():
    """运行中 mission 达上限(默认 2) → 拒绝创建。"""
    conn, by_name = _tools(running=2)
    res = asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    assert res.error and "上限" in res.error


def test_create_running_below_cap_ok():
    conn, by_name = _tools(running=1)
    res = asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    assert not res.error


def test_create_journal_created_entry():
    """建行时 journal 首条 kind=created(台账审计起点)。"""
    conn, by_name = _tools()
    asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    journal = conn.inserted[0]["journal"]
    assert journal[0]["kind"] == "created"


# ── mission_status ──────────────────────────────────────────────────────

def test_status_renders_plan_and_budget():
    conn, by_name = _tools()
    asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    res = asyncio.run(by_name["mission_status"].handler({"mission_id": 101}))
    assert not res.error
    assert "state=planning" in res.output
    assert "整理 56 页幻灯片视觉缺陷清单" in res.output
    assert "改道已用 0/2" in res.output
    assert "[m1] 扫描全部页面" in res.output


def test_status_jsonb_str_parsed():
    """JSONB 返回 str(FakeConn 模拟) → 正常解析(2026-08-15 教训防御)。"""
    conn, by_name = _tools()
    asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    # fetchrow 的 status 路径返回 str JSONB —— 已由 FakeConn 模拟
    res = asyncio.run(by_name["mission_status"].handler({"mission_id": 101}))
    assert not res.error  # 未抛 json 解析错误即通过


def test_status_missing_mission():
    conn, by_name = _tools()
    res = asyncio.run(by_name["mission_status"].handler({"mission_id": 999}))
    assert res.error and "不存在" in res.error


# ── mission_control ─────────────────────────────────────────────────────

def _make_escalated(conn, mid: int = 101) -> None:
    conn.rows[mid]["state"] = "escalated"


def test_control_approve_fallback_on_escalated():
    """escalated → approve_fallback: 预算 +1 + 状态回 executing + journal 留痕。"""
    conn, by_name = _tools()
    asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    _make_escalated(conn)
    res = asyncio.run(by_name["mission_control"].handler(
        {"mission_id": 101, "action": "approve_fallback"}
    ))
    assert not res.error
    row = conn.rows[101]
    assert row["budget"]["max_fallbacks"] == 3  # 默认 2 + 1
    assert row["state"] == "executing"
    assert any(e["kind"] == "fallback_approved" for e in row["journal"])


def test_control_approve_fallback_rejects_non_escalated():
    """非 escalated 状态 → 拒绝(_rowcount=0 路径, 2026-08-13 教训防御)。"""
    conn, by_name = _tools()
    asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    res = asyncio.run(by_name["mission_control"].handler(
        {"mission_id": 101, "action": "approve_fallback"}
    ))
    assert res.error and "非 escalated" in res.error


def test_control_abort_running():
    conn, by_name = _tools()
    asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    conn.rows[101]["state"] = "executing"
    res = asyncio.run(by_name["mission_control"].handler(
        {"mission_id": 101, "action": "abort"}
    ))
    assert not res.error
    assert conn.rows[101]["state"] == "cancelled"


def test_control_abort_rejects_terminal():
    conn, by_name = _tools()
    asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    conn.rows[101]["state"] = "done"
    res = asyncio.run(by_name["mission_control"].handler(
        {"mission_id": 101, "action": "abort"}
    ))
    assert res.error and "终态" in res.error


def test_control_pause_running_only():
    """pause 仅 executing/supervising 可用; planning 被拒。"""
    conn, by_name = _tools()
    asyncio.run(by_name["mission_create"].handler(VALID_ARGS))
    res = asyncio.run(by_name["mission_control"].handler(
        {"mission_id": 101, "action": "pause"}
    ))
    assert res.error and "非运行状态" in res.error
    conn.rows[101]["state"] = "supervising"
    res = asyncio.run(by_name["mission_control"].handler(
        {"mission_id": 101, "action": "pause"}
    ))
    assert not res.error
    assert conn.rows[101]["state"] == "paused"


def test_control_unknown_action():
    conn, by_name = _tools()
    res = asyncio.run(by_name["mission_control"].handler(
        {"mission_id": 101, "action": "nuke"}
    ))
    assert res.error and "未知 action" in res.error


def test_control_missing_mission():
    conn, by_name = _tools()
    res = asyncio.run(by_name["mission_control"].handler(
        {"mission_id": 999, "action": "abort"}
    ))
    assert res.error


# ── mission_cfg ─────────────────────────────────────────────────────────

def test_mission_cfg_defaults():
    """默认值(§八裁决 3): max_running=2 / max_fallbacks=2 / max_total_sec=3600。"""
    c = mission_cfg(None)
    assert c == {"max_running": 2, "max_fallbacks": 2, "max_total_sec": 3600}


def test_mission_cfg_overrides():
    c = mission_cfg({"tools": {"mission": {"max_running": 5}}})
    assert c["max_running"] == 5
    assert c["max_fallbacks"] == 2  # 未覆盖项保默认


def test_tools_are_kernel_annotated():
    """三工具均 is_kernel=True(始终注入, 同 delegate 锚点语义)。"""
    _, by_name = _tools()
    for name in ("mission_create", "mission_status", "mission_control"):
        assert by_name[name].is_kernel is True, name
