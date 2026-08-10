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
        """从 cfg dict 构造(读 skills.storage.dev_dir / runtime_source)。

        2026-08-08 修复: config.yaml 的 dev_dir 可能是 "${PA_USER_DATA}/skills"
        占位符(config loader 不负责展开, 见 sandbox/service.py 注释), 此处必须
        expandvars, 否则打包版技能目录解析为字面量 ${PA_USER_DATA}/skills →
        永远不存在 → 技能全丢(0.4.4 引入 PA_USER_DATA 后技能丢失的根因)。
        """
        import os as _os

        storage = cfg.get("skills", {}).get("storage", {})
        dev_dir = storage.get("dev_dir", "./skills")
        dev_dir = _os.path.expandvars(str(dev_dir)) if isinstance(dev_dir, str) else dev_dir
        return cls(
            dev_dir=dev_dir,
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

    async def load_version(
        self,
        skill_name: str,
        version: str,
        conn=None,
    ) -> Skill:
        """按指定 version 加载历史 Skill 快照(蓝图 §8.10,AC-3)。

        从 version_snapshots 表读 scope='skill' + version 的 payload,
        反序列化为 Skill 模型。用于版本回滚 + 回放历史版本。

        Args:
            skill_name: Skill 名(用于校验 payload.manifest.name 一致)。
            version: 语义化版本号(如 '0.1.0')。
            conn: asyncpg.Connection(必须,从 PG 读快照)。

        Returns:
            Skill 实例(payload 反序列化)。

        Raises:
            SkillNotFoundError: 版本不存在,或 conn=None,或 payload.manifest.name 与 skill_name 不匹配。
        """
        if conn is None:
            raise SkillNotFoundError(
                f"load_version 需要 conn 参数(skill='{skill_name}', version='{version}')"
            )
        row = await conn.fetchrow(
            "SELECT payload FROM version_snapshots WHERE scope='skill' AND version=$1",
            version,
        )
        if row is None:
            raise SkillNotFoundError(
                f"Skill '{skill_name}' version '{version}' 不存在(version_snapshots 无记录)"
            )
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        manifest_dict = (
            payload["manifest"] if isinstance(payload["manifest"], dict)
            else json.loads(payload["manifest"])
        )
        manifest = SkillManifest(**manifest_dict)
        # 名字校验:防止 UNIQUE(scope, version) 约束下用 A skill version 误读 B skill
        if manifest.name != skill_name:
            raise SkillNotFoundError(
                f"Skill name mismatch: requested '{skill_name}' but snapshot version "
                f"'{version}' belongs to '{manifest.name}'"
            )
        tools_yaml = payload.get("tools_yaml", [])
        if isinstance(tools_yaml, str):
            tools_yaml = json.loads(tools_yaml)
        return Skill(
            manifest=manifest,
            system_prompt=payload.get("system_prompt", ""),
            tools_yaml=tools_yaml,
        )

    async def list_all(self, conn=None) -> list[Skill]:
        """列出所有 enabled Skill(plan step 17)。

        PG 优先:查 skills 表 is_enabled=TRUE;PG 无记录则扫文件系统 dev_dir/*/skill.yaml。

        Args:
            conn: asyncpg.Connection(可选)。

        Returns:
            Skill 列表(name 降序排列)。
        """
        if self.runtime_source == "db_first" and conn is not None:
            skills = await self._list_from_pg(conn)
            if skills:
                return skills
        return await self._list_from_filesystem()

    async def _list_from_pg(self, conn) -> list[Skill]:
        """从 PG skills 表列出所有 enabled(按 name 升序)。"""
        rows = await conn.fetch(
            "SELECT name, version, description, manifest, system_prompt, tools, is_enabled "
            "FROM skills WHERE is_enabled = TRUE ORDER BY name ASC"
        )
        skills = []
        for row in rows:
            manifest_dict = row["manifest"] if isinstance(row["manifest"], dict) else json.loads(row["manifest"])
            manifest = SkillManifest(**manifest_dict)
            skills.append(Skill(
                manifest=manifest,
                system_prompt=row["system_prompt"] or "",
                tools_yaml=row["tools"] if isinstance(row["tools"], list) else json.loads(row["tools"] or "[]"),
            ))
        return skills

    async def _list_from_filesystem(self) -> list[Skill]:
        """从 dev_dir/*/ 扫描所有 skill.yaml。"""
        root = Path(self.dev_dir)
        if not root.exists():
            return []
        skills = []
        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_yaml = skill_dir / "skill.yaml"
            if not skill_yaml.exists():
                continue
            try:
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
                skills.append(Skill(manifest=manifest, system_prompt=system_prompt, tools_yaml=tools))
            except Exception:
                # 单个 skill 加载失败不影响其他 skill 列表
                continue
        return skills

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
