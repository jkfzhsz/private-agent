"""阶段4(agent-upgrader 设计文档 §2.2 能力域⑥): skill_manager —— PA 技能管理。

无涯(monitor)自我扩展核心工具: 创建/修改 PA 的 skill, 使无涯能
"自我安装一个技能并验证"(阶段4 验收标准)。

功能:
- skill_list: 列出所有技能(名称/描述/scenario/enabled) —— safe 只读
- skill_create: 创建新技能(skill.yaml + system_prompt.md) —— elevated
- skill_update: 修改技能 system_prompt / 元信息 —— elevated
- skill_delete: 删除技能目录 —— elevated(谨慎, 需确认)

安全边界:
- 技能目录 = ${PA_USER_DATA}/skills(与 SkillLoader.dev_dir 同源)
- 写操作 elevated(WS 确认); 创建/更新后用 SkillLoader 校验加载
- 删除技能前必须确认(不可恢复)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from private_agent.tools.defs import ToolDef, ToolResult

logger = logging.getLogger(__name__)

__all__ = [
    "SKILL_MANAGER_LIST_TOOL",
    "SKILL_MANAGER_CREATE_TOOL",
    "SKILL_MANAGER_UPDATE_TOOL",
    "SKILL_MANAGER_DELETE_TOOL",
    "SKILL_MANAGER_TOOLS",
    "_resolve_skills_dir",
    "_list_skills",
]


def _resolve_skills_dir(workspace: str = "") -> str:
    """解析技能目录(与 SkillLoader.dev_dir 同源)。

    优先级: 环境 PA_USER_DATA/skills > 会话工作区/skills > backend/skills。
    """
    import os as _os

    ud = _os.environ.get("PA_USER_DATA", "").strip()
    if ud:
        return str(Path(ud) / "skills")
    if workspace:
        ws = _os.path.expandvars(str(workspace))
        cand = Path(ws) / "skills"
        if cand.is_dir():
            return str(cand)
    # 兜底: backend/skills(开发模式)
    return str(Path(__file__).resolve().parents[2] / "skills")


def _list_skills(skills_dir: str) -> list[dict]:
    """列出技能目录下的技能(读 skill.yaml name/description/scenario)。"""
    import yaml

    out: list[dict] = []
    base = Path(skills_dir)
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        yaml_path = d / "skill.yaml"
        if not yaml_path.is_file():
            continue
        try:
            meta = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            meta = {}
        out.append({
            "name": d.name,
            "description": (meta.get("description") or "")[:120],
            "scenario": meta.get("scenario", ""),
            "enabled": meta.get("enabled", True),
            "version": meta.get("version", ""),
        })
    return out


async def _skill_list_handler(args: dict) -> ToolResult:
    """列出 PA 全部技能(只读)。"""
    ws = args.get("workspace") or ""
    skills_dir = _resolve_skills_dir(ws)
    skills = _list_skills(skills_dir)
    lines = [f"{s['name']} (scenario={s['scenario']}, enabled={s['enabled']}): {s['description']}" for s in skills]
    return ToolResult(output=f"技能目录: {skills_dir}\n共 {len(lines)} 个技能:\n" + "\n".join(lines))


async def _skill_create_handler(args: dict) -> ToolResult:
    """创建新技能(skill.yaml + system_prompt.md)。elevated 需确认。

    Args:
        name: 技能名(标识符, ^[a-z0-9_-]+$)。
        description: 技能描述。
        scenario: 技能类目(可选, 默认 'general')。
        system_prompt: 系统提示词(可选, 默认由 description 生成)。
    """
    name = str(args.get("name") or "").strip()
    if not name or not _VALID_NAME_RE.match(name):
        return ToolResult(
            output="", error="name required(^[a-z0-9_-]+$, 小写字母/数字/下划线/连字符)"
        )
    description = str(args.get("description") or "").strip()
    if not description:
        return ToolResult(output="", error="description required")
    scenario = str(args.get("scenario") or "general").strip()
    system_prompt = str(args.get("system_prompt") or "").strip()
    ws = args.get("workspace") or ""
    skills_dir = _resolve_skills_dir(ws)
    skill_dir = Path(skills_dir) / name

    if skill_dir.exists():
        return ToolResult(output="", error=f"技能 {name} 已存在(先 update 或 delete)")

    # 校验技能名合法性 + scenario 合法(与 provider name 同规则)
    if not _VALID_SCENARIO_RE.match(scenario):
        return ToolResult(output="", error=f"scenario 非法: {scenario}")

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        skill_yaml = (
            f"name: {name}\n"
            f"version: '1.0.0'\n"
            f"description: '{description}'\n"
            f"scenario: {scenario}\n"
            f"author: 'private-agent-monitor'\n"
            f"created_at: '{now}'\n"
            f"enabled: true\n"
            f"permissions:\n"
            f"  allow_file_write: true\n"
            f"  allow_network: false\n"
            f"  sandbox_enabled: true\n"
        )
        (skill_dir / "skill.yaml").write_text(skill_yaml, encoding="utf-8")
        prompt = system_prompt or (
            f"你是 PA 的 {name} 技能。{description}\n"
            f"请按技能职责完成用户请求, 涉及代码改动遵循 PA 工程纪律"
            f"(低风险直接做 + 核心改动先出方案)。"
        )
        (skill_dir / "system_prompt.md").write_text(prompt, encoding="utf-8")
    except OSError as e:
        return ToolResult(output="", error=f"技能创建失败: {type(e).__name__}: {e}")

    # 校验: 用 SkillLoader 加载确认技能可用
    try:
        from private_agent.skills.loader import SkillLoader
        from private_agent.config import loader as cfg_loader

        cfg = cfg_loader.load_config()
        await SkillLoader.from_cfg(cfg).load(name)
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            output="", error=f"技能已写入但加载校验失败: {type(e).__name__}: {e}"
        )
    return ToolResult(output=f"技能 {name} 创建成功(目录: {skill_dir}), 加载校验通过 ✅")


async def _skill_update_handler(args: dict) -> ToolResult:
    """修改技能(system_prompt 或元信息)。elevated 需确认。

    Args:
        name: 技能名。
        system_prompt: 新的系统提示词(可选)。
        description: 新的描述(可选, 写入 skill.yaml)。
    """
    import yaml

    name = str(args.get("name") or "").strip()
    if not name or not _VALID_NAME_RE.match(name):
        return ToolResult(output="", error="name required(^[a-z0-9_-]+$)")
    ws = args.get("workspace") or ""
    skills_dir = _resolve_skills_dir(ws)
    skill_dir = Path(skills_dir) / name
    if not skill_dir.is_dir():
        return ToolResult(output="", error=f"技能 {name} 不存在")

    system_prompt = str(args.get("system_prompt") or "").strip()
    description = str(args.get("description") or "").strip()
    changed: list[str] = []

    if system_prompt:
        (skill_dir / "system_prompt.md").write_text(system_prompt, encoding="utf-8")
        changed.append("system_prompt.md")
    if description:
        yaml_path = skill_dir / "skill.yaml"
        try:
            meta = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            meta["description"] = description
            yaml_path.write_text(
                yaml.safe_dump(meta, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            changed.append("skill.yaml(description)")
        except Exception as e:  # noqa: BLE001
            return ToolResult(output="", error=f"skill.yaml 更新失败: {e}")

    if not changed:
        return ToolResult(output="", error="无更新内容(提供 system_prompt 或 description)")
    return ToolResult(output=f"技能 {name} 已更新: {', '.join(changed)}")


async def _skill_delete_handler(args: dict) -> ToolResult:
    """删除技能目录。elevated 需确认(不可恢复)。

    Args:
        name: 技能名。
        confirm: 必须显式传 'yes'(防止误删)。
    """
    name = str(args.get("name") or "").strip()
    if not name or not _VALID_NAME_RE.match(name):
        return ToolResult(output="", error="name required(^[a-z0-9_-]+$)")
    confirm = str(args.get("confirm") or "").strip()
    if confirm != "yes":
        return ToolResult(
            output="", error="删除技能需显式确认: confirm='yes'(不可恢复, 请三思)"
        )
    ws = args.get("workspace") or ""
    skills_dir = _resolve_skills_dir(ws)
    skill_dir = Path(skills_dir) / name
    if not skill_dir.is_dir():
        return ToolResult(output="", error=f"技能 {name} 不存在")

    import shutil

    try:
        shutil.rmtree(skill_dir)
    except OSError as e:
        return ToolResult(output="", error=f"技能删除失败: {type(e).__name__}: {e}")
    return ToolResult(output=f"技能 {name} 已删除")


# 技能名/类目合法性(小写字母/数字/下划线/连字符)
_VALID_NAME_RE = __import__("re").compile(r"^[a-z0-9_-]+$")
_VALID_SCENARIO_RE = __import__("re").compile(r"^[a-zA-Z0-9_.-]+$")


SKILL_MANAGER_LIST_TOOL = ToolDef(
    name="skill_list",
    description=(
        "列出 PA 全部技能(名称/类目/描述/启用状态)。只读, 自动执行。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "会话工作区(可选)"},
        },
    },
    handler=_skill_list_handler,
    is_kernel=False,
    safety_level="safe",
    risk_level="low",
)

SKILL_MANAGER_CREATE_TOOL = ToolDef(
    name="skill_create",
    description=(
        "创建 PA 新技能(skill.yaml + system_prompt.md), 自动加载校验。"
        "会触发权限确认。name 为小写标识符, description 必填, "
        "scenario 为技能类目(默认 general), system_prompt 可选。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能名(^[a-z0-9_-]+$)"},
            "description": {"type": "string", "description": "技能描述(必填)"},
            "scenario": {"type": "string", "description": "技能类目(默认 general)"},
            "system_prompt": {"type": "string", "description": "系统提示词(可选)"},
            "workspace": {"type": "string", "description": "会话工作区(可选)"},
        },
        "required": ["name", "description"],
    },
    handler=_skill_create_handler,
    is_kernel=False,
    safety_level="elevated",
    risk_level="medium",
)

SKILL_MANAGER_UPDATE_TOOL = ToolDef(
    name="skill_update",
    description=(
        "修改 PA 技能(更新 system_prompt.md 或 skill.yaml description)。"
        "会触发权限确认。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能名"},
            "system_prompt": {"type": "string", "description": "新的系统提示词(可选)"},
            "description": {"type": "string", "description": "新的描述(可选)"},
            "workspace": {"type": "string", "description": "会话工作区(可选)"},
        },
        "required": ["name"],
    },
    handler=_skill_update_handler,
    is_kernel=False,
    safety_level="elevated",
    risk_level="medium",
)

SKILL_MANAGER_DELETE_TOOL = ToolDef(
    name="skill_delete",
    description=(
        "删除 PA 技能目录(不可恢复)。需 confirm='yes' 显式确认, "
        "会触发权限确认。仅用于废弃技能清理。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能名"},
            "confirm": {"type": "string", "description": "显式确认: 'yes'"},
            "workspace": {"type": "string", "description": "会话工作区(可选)"},
        },
        "required": ["name", "confirm"],
    },
    handler=_skill_delete_handler,
    is_kernel=False,
    safety_level="elevated",
    risk_level="high",
)

SKILL_MANAGER_TOOLS: list[ToolDef] = [
    SKILL_MANAGER_LIST_TOOL,
    SKILL_MANAGER_CREATE_TOOL,
    SKILL_MANAGER_UPDATE_TOOL,
    SKILL_MANAGER_DELETE_TOOL,
]
