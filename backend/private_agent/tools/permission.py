"""M3 权限缓存 cache_key 构造(蓝图 §7.5,spec AC-4/5)。

Source: plan/m3-remaining-done-criteria step 4
- get_permission_cache_key(skill_name, tool_name, args) 返回 sha256 hex
- cache_key = sha256(f"{skill_name}::{tool_name}::{args_json_sort_keys}")
- skill_name 作为前缀,避免不同 Skill 同工具权限缓存互相覆盖
- MVP 仅提供纯函数 + 单测,不集成到运行时权限校验路径(蓝图 §5.12 注明 MVP 不经 PermissionManager ABC)
"""
from __future__ import annotations

import hashlib
import json

__all__ = ["get_permission_cache_key"]


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
