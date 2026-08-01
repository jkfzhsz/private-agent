"""M3 Skills 框架 - SkillLoader(PG db_first + 文件回退,蓝图 §7.4,spec AC-9)。

Source: plan/m3-skills-office step 9
- load(skill_name, conn): PG skills 表优先(db_first)→ 文件系统 ./skills/{name}/ 回退
- PG row → Skill(manifest from JSONB + system_prompt)
- 文件系统 → skill.yaml + system_prompt.md + tools.yaml(可选)
- 两处都无 → SkillNotFoundError
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from private_agent.skills.errors import SkillNotFoundError
from private_agent.skills.models import Skill, SkillManifest


class SkillLoader:
    """蓝图 §7.4 Skill 加载器(PG 优先 + 文件系统回退)。"""

    def __init__(
        self,
        dev_dir: str = "./skills",
        runtime_source: str = "db_first",
    ):
        self.dev_dir = dev_dir
        self.runtime_source = runtime_source

    @classmethod
    def from_cfg(cls, cfg: dict) -> "SkillLoader":
        """从 cfg dict 构造(读 skills.storage.dev_dir / runtime_source)。"""
        storage = cfg.get("skills", {}).get("storage", {})
        return cls(
            dev_dir=storage.get("dev_dir", "./skills"),
            runtime_source=storage.get("runtime_source", "db_first"),
        )

    async def load(self, skill_name: str, conn=None) -> Skill:
        """加载 Skill(PG 优先 → 文件回退)。

        Args:
            skill_name: Skill 名(如 'office')。
            conn: asyncpg.Connection(db_first 时查 PG);None 时直接走文件系统。

        Returns:
            Skill 实例。

        Raises:
            SkillNotFoundError: PG + 文件系统均未找到。
        """
        if self.runtime_source == "db_first" and conn is not None:
            skill = await self._load_from_pg(skill_name, conn)
            if skill is not None:
                return skill
        skill = await self._load_from_filesystem(skill_name)
        if skill is not None:
            return skill
        raise SkillNotFoundError(
            f"Skill '{skill_name}' 不存在(PG + {self.dev_dir}/{skill_name}/ 均未找到)"
        )

    async def _load_from_pg(self, skill_name: str, conn) -> Skill | None:
        """从 PG skills 表加载(按 name + is_enabled + 最新 updated_at)。"""
        row = await conn.fetchrow(
            "SELECT name, version, description, manifest, system_prompt, tools, is_enabled "
            "FROM skills WHERE name = $1 AND is_enabled = TRUE "
            "ORDER BY updated_at DESC LIMIT 1",
            skill_name,
        )
        if row is None:
            return None
        manifest_dict = row["manifest"] if isinstance(row["manifest"], dict) else json.loads(row["manifest"])
        manifest = SkillManifest(**manifest_dict)
        return Skill(
            manifest=manifest,
            system_prompt=row["system_prompt"] or "",
            tools_yaml=row["tools"] if isinstance(row["tools"], list) else json.loads(row["tools"] or "[]"),
        )

    async def _load_from_filesystem(self, skill_name: str) -> Skill | None:
        """从 ./skills/{name}/ 加载(skill.yaml + system_prompt.md + tools.yaml)。"""
        skill_dir = Path(self.dev_dir) / skill_name
        skill_yaml = skill_dir / "skill.yaml"
        if not skill_yaml.exists():
            return None
        with skill_yaml.open(encoding="utf-8") as f:
            manifest_dict = yaml.safe_load(f)
        manifest = SkillManifest(**manifest_dict)
        prompt_file = skill_dir / "system_prompt.md"
        system_prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else ""
        tools_yaml = skill_dir / "tools.yaml"
        tools = []
        if tools_yaml.exists():
            with tools_yaml.open(encoding="utf-8") as f:
                tools = yaml.safe_load(f) or []
        return Skill(manifest=manifest, system_prompt=system_prompt, tools_yaml=tools)
