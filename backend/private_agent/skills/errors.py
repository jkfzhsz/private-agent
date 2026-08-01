"""M3 Skills 框架 - 异常定义(spec AC-4/AC-5,蓝图 §7.4)。

Source: plan/m3-skills-office step 8
- SkillNotFoundError: Skill 不存在(PG + 文件系统均未找到)
- SkillSwitchNotAllowedError: 会话已锁定 Skill,运行中拒绝切换(409)
- SkillValidationError: manifest 校验失败(工具不存在/safety_level 枚举错)(400)
"""
from __future__ import annotations


class SkillNotFoundError(Exception):
    """Skill 不存在(PG + 文件系统均未找到)。"""


class SkillSwitchNotAllowedError(Exception):
    """会话已锁定 Skill,运行中拒绝切换(蓝图 §7.4 MVP 不支持中途切换)。"""


class SkillValidationError(Exception):
    """manifest 校验失败(工具不存在 / safety_level_override 枚举错 / schema 错)。"""
