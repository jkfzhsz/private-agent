"""会议室(Meeting Room)核心: 角色枚举 / 房间共享目录 / 角色工具白名单解析。

设计文档: docs/next-phase-plan-2026-09-11-meeting-room.md

模型(§4.1/§4.2):
- **房间** = 一条 `sessions` 行(`kind='room'`)。`locked_skill_name` = 主持人角色,
  `workspace` = 房间共享目录, `room_meta` 记主持人/成员/目标。
  主持人即该会话的主循环(ReactLoop), 由此天然规避"子代理嵌套深度恒 1"限制。
- **成员** = 由主持人委派出的**角色绑定子代理**(`SubagentRunner(role_skill=...)`),
  其写权限经既有继承链自动落在房间目录 —— 零改动:
  `sessions.workspace` → main.py 覆盖 `cfg.system.workspace_root`
  → subagent.py 取父会话 workspace 覆写 `self._cfg`
  → `ReactLoop(cfg=self._cfg)` → 对 `file_write` 强制注入 `data_dir`。
  (逐行核实见设计文档 §4.3; `file_write`/`react_loop`/`file_read` 均不需改动)

产物交接(§4.3)目录约定:
    <rooms_root>/<room_key>/
        README.md      任务契约(目标 / 交付定义 / 成员名单)
        artifacts/     产物落地区(所有成员写成这里)
        notes/         交接说明(上游→下游)

角色枚举是**显式白名单**而非开放字符串: 委派协议接受模型给的 role 时,
只允许下表三个场景技能, 防止模型传入任意 skill 名越权。
"""
from __future__ import annotations

import os
import re
import secrets
from datetime import datetime
from pathlib import Path

__all__ = [
    "ROOM_ROLES",
    "ROOM_KIND",
    "ROLE_LABELS",
    "RoomRoleError",
    "is_room_role",
    "role_label",
    "role_display",
    "normalize_roles",
    "room_key",
    "rooms_root",
    "room_dir",
    "build_room_readme",
    "build_room_contract",
    "ensure_room_layout",
    "parse_room_meta",
    "resolve_role_whitelist",
]

#: 房间会话的 sessions.kind 取值(需 CHECK 约束含 'room', 见 migrations)。
ROOM_KIND = "room"

#: 可作为房间主持人/成员的场景技能(与 backend/skills/<name>/skill.yaml 同名)。
#: 无涯(monitor)按设计不参与具体工作, 故不在列。
ROOM_ROLES: tuple[str, ...] = ("office", "data_analysis", "frontend_design")

#: 角色 → 中文显示名(供 README 与系统提示词注入使用; 单一来源避免漂移)。
ROLE_LABELS: dict[str, str] = {
    "office": "子瞻",
    "data_analysis": "白圭",
    "frontend_design": "清和",
}

#: 房间目录内约定子目录(§4.3)。
ARTIFACTS_DIR = "artifacts"
NOTES_DIR = "notes"
README_NAME = "README.md"

_KEY_TIME_FMT = "%Y%m%d-%H%M%S"
_KEY_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


class RoomRoleError(ValueError):
    """角色非法(不在 ROOM_ROLES 内)。"""


def is_room_role(role: object) -> bool:
    """role 是否为合法房间角色(严格类型 + 白名单成员判定)。"""
    return isinstance(role, str) and role in ROOM_ROLES


def role_label(role: object) -> str:
    """角色 → 中文显示名; 未知角色原样返回(不抛异常, 供展示路径使用)。"""
    if isinstance(role, str) and role in ROLE_LABELS:
        return ROLE_LABELS[role]
    return str(role or "")


def role_display(role: object) -> str:
    """角色 → ``子瞻(office)`` 形式(未知角色退化为纯字符串)。"""
    name = role_label(role)
    if isinstance(role, str) and role in ROLE_LABELS:
        return f"{name}({role})"
    return name


def normalize_roles(
    members: object, host_role: object
) -> tuple[list[str], str]:
    """校验并规范化 (成员列表, 主持人)。

    规则(§7.2 UI 语义: 主持人只能从已选成员中产生):
    - members 必须是非空序列, 每项属 ROOM_ROLES, 去重且保序;
    - host_role 必须是 members 之一。

    Returns:
        (去重后的成员列表, 主持人角色)

    Raises:
        RoomRoleError: 成员为空 / 含非法角色 / 主持人不在成员内。
    """
    if not isinstance(members, (list, tuple)) or not members:
        raise RoomRoleError("members 不能为空")
    seen: list[str] = []
    for m in members:
        if not is_room_role(m):
            raise RoomRoleError(
                f"非法角色 {m!r}; 允许值: {', '.join(ROOM_ROLES)}"
            )
        if m not in seen:
            seen.append(m)
    if not is_room_role(host_role):
        raise RoomRoleError(
            f"非法主持人 {host_role!r}; 允许值: {', '.join(ROOM_ROLES)}"
        )
    if host_role not in seen:
        raise RoomRoleError(f"主持人 {host_role!r} 必须是参会成员之一")
    return seen, str(host_role)


def room_key(now: datetime | None = None) -> str:
    """生成房间目录键: YYYYMMDD-HHMMSS-<4位hex>。

    时间前缀保证可读与可排序, 短随机后缀避免同秒建房的碰撞。
    """
    ts = (now or datetime.now()).strftime(_KEY_TIME_FMT)
    return f"{ts}-{secrets.token_hex(2)}"


def is_valid_room_key(key: object) -> bool:
    """房间键格式校验(用于读接口防路径穿越)。"""
    return isinstance(key, str) and bool(_KEY_RE.match(key))


def rooms_root(cfg: dict, host_workspace: str | None = None) -> Path:
    """解析房间根目录。

    优先级(设计文档 §10-Q5 裁决为 D:\\PA\\rooms, 与各成员工作区同级):
    1. `system.rooms_root` 显式配置(展开环境变量);
    2. 派生: 主持人工作区的**同级** `rooms/` 目录
       (如 host_workspace=D:\\PA\\zizhan → D:\\PA\\rooms);
    3. 兜底: `system.workspace_root` 下 `rooms/`。

    派生而非硬编码盘符, 避免与"依赖与数据落 D 盘"约定之外的部署冲突。
    """
    explicit = (
        str(cfg.get("system", {}).get("rooms_root") or "").strip()
    )
    if explicit:
        return Path(os.path.expandvars(explicit))
    if host_workspace and str(host_workspace).strip():
        return Path(str(host_workspace).strip()).parent / "rooms"
    ws = str(cfg.get("system", {}).get("workspace_root") or "").strip()
    return Path(os.path.expandvars(ws)) / "rooms"


def room_dir(
    cfg: dict, key: str, *, host_workspace: str | None = None
) -> Path:
    """房间共享目录绝对路径(键格式非法时抛 ValueError, 防路径穿越)。"""
    if not is_valid_room_key(key):
        raise ValueError(f"非法房间键: {key!r}")
    return rooms_root(cfg, host_workspace) / key


def build_room_readme(
    *, goal: str, host_role: str, members: list[str], room_key_: str = ""
) -> str:
    """生成房间 README.md(任务契约): 目标 / 交付定义 / 成员分工。"""
    who = role_display

    member_lines = "\n".join(
        "- "
        + who(m)
        + ("  ← 本次主持人" if m == host_role else "")
        for m in members
    )
    return (
        f"# 会议室任务契约\n\n"
        f"> 房间号: {room_key_ or '(未记录)'}\n"
        f"> 主持角色: {who(host_role)}\n\n"
        f"## 任务目标\n\n{goal.strip() or '(未填写)'}\n\n"
        f"## 参会成员\n\n{member_lines}\n\n"
        f"## 目录约定\n\n"
        f"- `artifacts/` —— **产物落地区**。任何成员的最终产物写这里,\n"
        f"  下游成员据此目录取上游产物。\n"
        f"- `notes/` —— **交接说明**。写明「谁交给谁、交付了什么、\n"
        f"  下游需要什么前置、已知问题」, 便于其他成员直接接手。\n\n"
        f"## 协作约定\n\n"
        f"1. 产物一律落在本房间目录内(全体成员共享写权限), 不要写到自己\n"
        f"   的私有工作区 —— 否则其他成员看不到。\n"
        f"2. 引用上游产物时写明**相对路径**(如 `artifacts/report.md`),\n"
        f"   不要粘贴全文(会被截断)。\n"
        f"3. 主持人负责统筹任务分配、执行编排与过程记录。\n"
    )


def build_room_contract(
    *,
    goal: str = "",
    host_role: str = "",
    members: list[str] | None = None,
    room_dir: str = "",
    self_role: str = "",
    is_host: bool = False,
) -> str:
    """生成注入 system prompt 的「会议室约定」段(0.6.0 P1 W3)。

    为什么必须注入: 房间会话的 workspace 被指向房间共享目录后, 运行时约定段
    只会告诉模型"工作区根目录 = 房间目录", 但**不知道** ``artifacts/`` 与
    ``notes/`` 的分工 —— 成员会把产物写到房间根, 主持人与下游成员难以辨认,
    "产物交接"这一核心痛点就落空了。本段把目录分工与各自职责显式写进提示词。

    两种视角(设计文档 §4.4):
    - ``is_host=True``: 房间会话本体(kind='room'), 强调统筹/委派/收口;
    - ``is_host=False``: 角色成员子代理(kind='sub'), 只强调"写哪里/读哪里",
      避免与成员自身场景人格产生指令冲突。

    Args:
        goal: 任务目标(写入目标行)。
        host_role: 本次主持人角色。
        members: 参会成员角色列表。
        room_dir: 房间共享目录绝对路径。
        self_role: 当前会话自身的角色(宿主=主持人; 成员=被指定角色)。
        is_host: 当前会话是否为主持人。

    Returns:
        约定文本(始终非空; 缺字段以"(未填写)"降级, 调用方无需判空)。
    """
    ms = [m for m in (members or []) if isinstance(m, str) and m]
    member_line = "、".join(role_display(m) for m in ms) or "(未记录)"
    goal_line = (goal or "").strip() or "(未填写)"
    host_line = role_display(host_role) or "(未指定)"
    tail = room_dir or "(未配置)"
    art = f"{tail}/{ARTIFACTS_DIR}"
    note = f"{tail}/{NOTES_DIR}"
    readme = f"{tail}/{README_NAME}"
    art_rel = f"{ARTIFACTS_DIR}/"
    note_rel = f"{NOTES_DIR}/"
    me = role_display(self_role) or "(未指定)"

    head = (
        "[会议室约定]\n"
        f"本次任务在多智能体「会议室」中协作完成，房间共享目录：{tail}\n"
        f"- 任务目标：{goal_line}\n"
        f"- 参会成员：{member_line}\n"
    )

    if is_host:
        body = (
            f"- 你是本次**主持人**（{me}），负责统筹任务分配、执行编排与过程记录。\n"
            "\n"
            "目录约定（全体成员共享写权限，你必须遵守）：\n"
            f"- 最终交付物一律写入 `{art}/`（PPT / HTML / 报告 / 图表等）；\n"
            f"- 过程与交接说明写入 `{note}/`"
            "（谁交给谁、交付了什么、下游前置、已知问题）；\n"
            f"- 任务契约见 `{readme}`。\n"
            "\n"
            "主持人职责：\n"
            "1. 拆解任务并决定由哪位成员承担，按依赖顺序编排；\n"
            f"2. 用 `delegate_subtask` 委派，在 `subtasks[].role` 指定成员角色"
            f"（`{ROOM_ROLES[0]}`=子瞻 / `{ROOM_ROLES[1]}`=白圭 / "
            f"`{ROOM_ROLES[2]}`=清和）；\n"
            f"3. 成员产物落到 `{art_rel}` 后，**你直接用 `file_read` 读取并修改、"
            "再交付** —— 产物交接正是本次协作的目的，不要重复生成已有产物；\n"
            f"4. 收口：最终产物留 `{art_rel}`，交接与结论写 `{note_rel}`。\n"
        )
    else:
        body = (
            f"- 你是本次会议的**成员**（{me}）。\n"
            f"- 主持人（{host_line}）会向你派发子任务，"
            "按派发的要求与交付定义完成即可。\n"
            "\n"
            "目录约定（全体成员共享写权限，你必须遵守）：\n"
            f"- 你的交付物一律写入 `{art}/`；\n"
            f"- 需要说明交接内容 / 前置条件 / 已知问题时，写入 `{note}/`；\n"
            f"- 任务契约（目标 / 交付定义 / 成员分工）见 `{readme}`；\n"
            f"- 引用上游成员的产物时，直接 `file_read` 读取 `{art}/<文件名>`，"
            f"并在说明中写**相对路径**（如 `{art_rel}report.md`），"
            "不要粘贴全文（会被截断）；\n"
            "- 不要覆盖其他成员已产出的同名文件，也不要写到房间目录之外 —— "
            "否则主持人与下游成员读不到。\n"
        )
    return head + body


def ensure_room_layout(
    base: Path,
    *,
    goal: str = "",
    host_role: str = "",
    members: list[str] | None = None,
    room_key_: str = "",
) -> Path:
    """创建房间目录骨架(幂等): 目录 + artifacts/ + notes/ + README.md。

    README.md 仅在**不存在**时写入, 避免重启/重复建房覆盖已有记录。

    Returns:
        房间目录 Path。
    """
    base.mkdir(parents=True, exist_ok=True)
    (base / ARTIFACTS_DIR).mkdir(exist_ok=True)
    (base / NOTES_DIR).mkdir(exist_ok=True)
    readme = base / README_NAME
    if not readme.exists():
        readme.write_text(
            build_room_readme(
                goal=goal,
                host_role=host_role,
                members=list(members or []),
                room_key_=room_key_,
            ),
            encoding="utf-8",
        )
    return base


def parse_room_meta(raw: object) -> dict:
    """把 sessions.room_meta(JSONB → dict/str/None) 规整为 dict。

    非法/缺失一律返回 {}, 调用方据此降级(不抛异常 —— 读路径不应因脏数据崩)。
    """
    import json

    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            v = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return v if isinstance(v, dict) else {}
    return {}


async def resolve_role_whitelist(cfg: dict, conn, role: str) -> set[str]:
    """解析某房间角色的**内置工具白名单**(工具名集合)。

    与场景会话同源: 走 `SkillLoader` → `manifest.dependencies.tools`(仅 enabled),
    与 `main._get_frozen_tools` 对"锁定该 skill 的会话"的算法一致 —— 因此
    角色子代理的工具集可与该角色的独立场景会话逐项对齐(设计文档 §9 验收 3)。

    注意: 本函数**不**并入 `_ALWAYS_AVAILABLE_TOOLS`(基础记忆工具), 该并集
    由调用方(main.py)完成 —— 与 `_get_frozen_tools` 的分层保持一致。

    Args:
        cfg: 合并后的配置 dict。
        conn: asyncpg 连接(db_first 时查 PG)。
        role: ROOM_ROLES 之一。

    Raises:
        RoomRoleError: role 不在白名单内。
        SkillNotFoundError: skill 在 PG 与文件系统均缺失。
    """
    if not is_room_role(role):
        raise RoomRoleError(
            f"非法角色 {role!r}; 允许值: {', '.join(ROOM_ROLES)}"
        )
    from private_agent.skills.loader import SkillLoader

    loader = SkillLoader.from_cfg(cfg)
    skill = await loader.load(role, conn)
    return {
        t.name for t in skill.manifest.dependencies.tools if t.enabled
    }
