"""M3 权限缓存 cache_key 构造(蓝图 §7.5,spec AC-4/5)。

Source: plan/m3-remaining-done-criteria step 4
- get_permission_cache_key(skill_name, tool_name, args) 返回 sha256 hex
- cache_key = sha256(f"{skill_name}::{tool_name}::{args_json_sort_keys}")
- skill_name 作为前缀,避免不同 Skill 同工具权限缓存互相覆盖
- MVP 仅提供纯函数 + 单测,不集成到运行时权限校验路径(蓝图 §5.12 注明 MVP 不经 PermissionManager ABC)

阶段三批次 1(B-2, 调研 round2 §4.2.1): PermissionRule 规则 DSL(纯函数层)。
- 规则格式: "action:Tool(specifier)", 如 "deny:file_write" / "allow:code_execution(//sandbox/**)"
- action ∈ {allow, ask, deny}; specifier 对 args 字符串值做 fnmatch(省略 = 仅工具名匹配)
- evaluate_rules: deny 优先于一切 allow(Claude Code 语义); 非 deny 按 source 优先级
  (session > skill > config) 取首个; 无匹配返回 None → 调用方回退 safety_level。
- 本文件仅提供纯函数与数据类, 运行时接入在 permission_manager(阶段三批次 1)。
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass

__all__ = [
    "get_permission_cache_key",
    "PermissionRule",
    "parse_rule",
    "match_rule",
    "evaluate_rules",
    "PERMISSION_ACTIONS",
    "PERMISSION_SOURCES",
]


def get_permission_cache_key(
    skill_name: str,
    tool_name: str,
    args: dict,
) -> str:
    """构造权限确认缓存 key(蓝图 §7.5)。

    skill_name 作为缓存隔离前缀,完全规避不同 Skill 同一工具权限缓存
    互相覆盖问题。与 §5.12 权限校验逻辑兼容。

    Args:
        skill_name: Skill 名称(如 "office" / "data_analysis")。
        tool_name: 工具名称(如 "file_read" / "code_execution")。
        args: 工具调用参数字典。

    Returns:
        64 字符 sha256 hex 字符串。
    """
    args_str = json.dumps(args, sort_keys=True, ensure_ascii=False)
    raw = f"{skill_name}::{tool_name}::{args_str}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ── 阶段三批次 1: 权限规则 DSL ──────────────────────────────────────────────

PERMISSION_ACTIONS = ("allow", "ask", "deny")
# source 优先级: 数值越大优先级越高(会话级 > Skill 声明 > 系统配置)
PERMISSION_SOURCES = ("config", "skill", "session")
_SOURCE_WEIGHT = {s: i for i, s in enumerate(PERMISSION_SOURCES)}

# "action:Tool(specifier)" — specifier 可选, 形如 "(//sandbox/**)" / "(api.example.com)"
_RULE_RE = re.compile(
    r"^(?P<action>allow|ask|deny)\s*:\s*"
    r"(?P<tool>[A-Za-z0-9_][A-Za-z0-9_.-]*"
    r"(?:\*\*?)?)"  # 工具名, 允许尾部通配(如 mcp__iFind__*)
    r"(?:\((?P<spec>[^()]*)\))?\s*$"
)


@dataclass(frozen=True)
class PermissionRule:
    """权限规则: 匹配工具 + 参数模式的决策声明。

    Args:
        pattern: 原始规则字符串(如 "deny:file_write(//**/.env)"), 用于审计回显。
        action: allow | ask | deny。
        tool: 工具名或通配模式(如 "code_execution" / "mcp__iFind__*")。
        specifier: 参数模式(fnmatch, 对 args 字符串值匹配); None = 仅工具名匹配。
        source: config | skill | session(决策优先级来源)。
    """

    pattern: str
    action: str
    tool: str
    specifier: str | None = None
    source: str = "config"

    def matches(self, tool_name: str, args: dict | None = None) -> bool:
        """判断规则是否命中指定工具调用。"""
        if not fnmatch.fnmatch(tool_name, self.tool):
            return False
        if self.specifier is None:
            return True
        args = args or {}
        # specifier 对 args 中任意字符串值做 fnmatch(任一命中即匹配)
        for value in args.values():
            if isinstance(value, str) and fnmatch.fnmatch(value, self.specifier):
                return True
        return False

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"PermissionRule({self.pattern!r}, source={self.source!r})"


def parse_rule(rule_str: str, source: str = "config") -> PermissionRule:
    """解析规则字符串 "action:Tool(specifier)"。

    Args:
        rule_str: 规则字符串(如 "deny:file_write(//**/.env)")。
        source: 规则来源(config/skill/session)。

    Returns:
        PermissionRule 实例。

    Raises:
        ValueError: 格式非法 / action 或 source 非法。
    """
    if source not in _SOURCE_WEIGHT:
        raise ValueError(
            f"invalid permission source: {source!r} "
            f"(expected {list(_SOURCE_WEIGHT)})"
        )
    m = _RULE_RE.match(rule_str.strip())
    if not m:
        raise ValueError(
            f"invalid permission rule: {rule_str!r} "
            f"(expected 'action:Tool(specifier)')"
        )
    action = m.group("action")
    tool = m.group("tool")
    spec = m.group("spec")
    return PermissionRule(
        pattern=rule_str.strip(),
        action=action,
        tool=tool,
        specifier=spec if spec else None,
        source=source,
    )


def match_rule(rule: PermissionRule, tool_name: str, args: dict | None = None) -> bool:
    """纯函数版规则匹配(便于单测与审计)。"""
    return rule.matches(tool_name, args)


def evaluate_rules(
    rules: list[PermissionRule],
    tool_name: str,
    args: dict | None = None,
) -> str | None:
    """对规则集求值, 返回最终决策。

    求值语义(对齐 Claude Code allow/ask/deny):
    1. 任意匹配的 deny → "deny"(deny 优先于一切 allow, 全局短路);
    2. 无 deny 时, 按 source 优先级(session > skill > config)取首个匹配的
       allow/ask;
    3. 无任何匹配 → None(调用方回退 safety_level 默认路径)。

    Args:
        rules: 规则列表(可含不同 source)。
        tool_name: 工具名。
        args: 工具调用参数。

    Returns:
        "allow" | "ask" | "deny" | None。
    """
    if not rules:
        return None
    args = args or {}
    # 第一步: deny 全局短路
    for rule in rules:
        if rule.action == "deny" and rule.matches(tool_name, args):
            return "deny"
    # 第二步: 按 source 权重取首个匹配的非 deny 决策
    best: tuple[int, str] | None = None
    best_action: str | None = None
    for rule in rules:
        if rule.action == "deny":
            continue
        if not rule.matches(tool_name, args):
            continue
        weight = _SOURCE_WEIGHT.get(rule.source, 0)
        if best is None or weight > best[0]:
            best = (weight, rule.action)
            best_action = rule.action
    return best_action
