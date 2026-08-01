"""M3 Skills 框架 - SkillManager.activate_skill(蓝图 §7.4,spec AC-1/3/4/5)。

Source: plan/m3-skills-office step 13
- activate_skill: load → 校验 → 锁定检查 → 模板替换 → 少样本 → 白名单 → Frozen Zone → hash → 锁定
- AC-1: 成功 activate 返回 locked_version + frozen_hash
- AC-3: 返回过滤后 tools(enabled=false 不含)
- AC-4: 已锁定 session 切换不同 skill → SkillSwitchNotAllowedError
- AC-5: manifest 引用不存在工具 → SkillValidationError
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from private_agent.core.context_manager import ContextManager
from private_agent.skills.errors import (
    SkillSwitchNotAllowedError,
    SkillValidationError,
)
from private_agent.skills.models import ToolDependency

if TYPE_CHECKING:
    from private_agent.skills.example_loader import ExampleLoader
    from private_agent.skills.loader import SkillLoader
    from private_agent.tools.registry import ToolRegistry

__all__ = ["SkillManager"]


class SkillManager:
    """蓝图 §7.4 Skill 管理器(激活 + 会话锁定)。"""

    def __init__(
        self,
        loader: "SkillLoader",
        example_loader: "ExampleLoader",
        tool_registry: "ToolRegistry",
    ) -> None:
        self.loader = loader
        self.example_loader = example_loader
        self.tool_registry = tool_registry

    async def activate_skill(
        self,
        skill_name: str,
        session_id: int,
        conn,
    ) -> dict:
        """激活 Skill 并锁定到会话(蓝图 §7.4,spec AC-1/3/4/5)。

        Args:
            skill_name: Skill 名(如 'office')。
            session_id: 会话 ID。
            conn: asyncpg.Connection。

        Returns:
            {locked_version, frozen_hash, filtered_tools, system_prompt}

        Raises:
            SkillNotFoundError: Skill 不存在(loader 抛,透传)。
            SkillValidationError: 工具白名单引用不存在的工具。
            SkillSwitchNotAllowedError: 会话已锁定不同 Skill。
        """
        # 1. 加载 Skill(loader 找不到时抛 SkillNotFoundError)
        skill = await self.loader.load(skill_name, conn)

        # 2. 校验工具白名单引用都在 ToolRegistry 中(AC-5)
        self._validate_tools(skill.manifest.dependencies.tools)

        # 3. 查 sessions 锁定状态(AC-4)
        row = await conn.fetchrow(
            "SELECT locked_skill_name, created_at FROM sessions WHERE id = $1",
            session_id,
        )
        locked = row["locked_skill_name"] if row else None
        if locked is not None and locked != skill_name:
            raise SkillSwitchNotAllowedError(
                f"Session {session_id} 已锁定 Skill '{locked}',"
                f"不允许切换到 '{skill_name}'"
            )

        # 4. 模板变量替换(蓝图 §3.7)
        created_at = row["created_at"] if (row and "created_at" in row) else None
        system_prompt = self._replace_template_vars(
            skill.system_prompt, skill_name, session_id, created_at
        )

        # 少样本注入(蓝图 §7.7)
        if skill.manifest.examples.enabled:
            examples = await self.example_loader.load(
                skill_name,
                max_examples=skill.manifest.examples.max_examples,
                max_token=skill.manifest.max_frozen_token,
            )
            if examples:
                system_prompt += "\n\n## 示例\n\n" + "\n\n".join(examples)

        # 5. 工具白名单过滤(仅 enabled=true,AC-3)
        whitelist = [
            t.name for t in skill.manifest.dependencies.tools if t.enabled
        ]
        filtered_tools = self.tool_registry.list_tools_for_session(whitelist)

        # 6. 构建 Frozen Zone + 计算 frozen_hash(AC-1)
        cm = ContextManager(
            session_id=session_id,
            system_prompt=system_prompt,
            tools=filtered_tools,
        )
        await cm.ensure_initial(conn)
        frozen_hash = cm.compute_frozen_hash()

        # 7. UPDATE sessions 锁定字段
        await conn.execute(
            "UPDATE sessions SET locked_skill_name=$1, locked_skill_version=$2, "
            "frozen_hash=$3 WHERE id=$4",
            skill_name,
            skill.manifest.version,
            frozen_hash,
            session_id,
        )

        return {
            "locked_version": skill.manifest.version,
            "frozen_hash": frozen_hash,
            "filtered_tools": filtered_tools,
            "system_prompt": system_prompt,
        }

    def _validate_tools(self, tools: list[ToolDependency]) -> None:
        """校验 manifest 引用的工具都在 ToolRegistry 中存在(AC-5)。"""
        registered = {t.name for t in self.tool_registry.list_tools()}
        for td in tools:
            if td.name not in registered:
                raise SkillValidationError(
                    f"工具 '{td.name}' 不在 ToolRegistry 中"
                    f"(manifest 引用了未注册的工具)"
                )

    def _replace_template_vars(
        self,
        prompt: str,
        skill_name: str,
        session_id: int,
        created_at: datetime | None,
    ) -> str:
        """模板变量替换(蓝图 §3.7)。

        支持: {{user.name}} / {{now}} / {{session.id}} /
              {{session.created_at}} / {{skills.active}} / {{skills.tools}}
        """
        replacements = {
            "{{user.name}}": "user",
            "{{now}}": datetime.now().isoformat(),
            "{{session.id}}": str(session_id),
            "{{session.created_at}}": str(created_at) if created_at else "",
            "{{skills.active}}": skill_name,
            "{{skills.tools}}": "",
        }
        for key, value in replacements.items():
            prompt = prompt.replace(key, value)
        return prompt
