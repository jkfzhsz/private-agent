"""M3 Skills 框架 - SkillLoader(PG db_first + 文件回退,spec AC-9)。

Source: plan/m3-skills-office step 9, 蓝图 §7.4
- load(skill_name): PG skills 表优先 → 文件系统 ./skills/{name}/ 回退
- PG 无该 skill 但文件系统存在 → 回退成功(AC-9)
- 两处都无 → SkillNotFoundError
"""
import pytest

from private_agent.skills.errors import SkillNotFoundError
from private_agent.skills.loader import SkillLoader
from private_agent.skills.models import Skill


def _office_skill_yaml():
    """构造合法 skill.yaml 内容。"""
    return """\
name: office
version: "1.0.0"
description: "办公场景"
scenario: office
enabled: true
dependencies:
  tools:
    - name: code_execution
      safety_level_override: elevated
    - name: file_read
      safety_level_override: safe
permissions:
  allow_file_write: true
  sandbox_enabled: true
prompt_vars:
  - user.name
  - now
knowledge_base:
  enabled: true
  scenario: office
examples:
  enabled: true
  max_examples: 3
max_frozen_token: 4000
"""


def _office_system_prompt():
    return "你是办公助手,负责文档处理与信息检索。"


class _FakeConn:
    """模拟 asyncpg.Connection(PG 无 skill 时返回 None)。"""

    async def fetchrow(self, *args, **kwargs):
        return None


class _FakeConnWithSkill:
    """模拟 asyncpg.Connection(PG 有 skill 时返回 row)。"""

    def __init__(self, manifest_dict, system_prompt):
        self._row = {
            "name": manifest_dict["name"],
            "version": manifest_dict["version"],
            "description": manifest_dict.get("description", ""),
            "manifest": manifest_dict,
            "system_prompt": system_prompt,
            "tools": [],
            "is_enabled": True,
        }

    async def fetchrow(self, *args, **kwargs):
        return self._row


class TestSkillLoaderFilesystemFallback:
    """AC-9: PG 无 office skill 但 ./skills/office/ 存在 → 文件回退成功。"""

    def test_load_from_filesystem_when_pg_empty(self, tmp_path):
        """PG 无 skill,文件系统有 → 从文件加载。"""
        skill_dir = tmp_path / "office"
        skill_dir.mkdir()
        (skill_dir / "skill.yaml").write_text(_office_skill_yaml(), encoding="utf-8")
        (skill_dir / "system_prompt.md").write_text(_office_system_prompt(), encoding="utf-8")

        loader = SkillLoader(dev_dir=str(tmp_path))
        import asyncio
        skill = asyncio.run(loader.load("office", conn=_FakeConn()))

        assert skill is not None
        assert skill.manifest.name == "office"
        assert skill.manifest.version == "1.0.0"
        assert skill.system_prompt == _office_system_prompt()

    def test_raises_when_not_found_anywhere(self, tmp_path):
        """PG + 文件系统都无 → SkillNotFoundError。"""
        loader = SkillLoader(dev_dir=str(tmp_path))
        import asyncio
        with pytest.raises(SkillNotFoundError):
            asyncio.run(loader.load("nonexistent", conn=_FakeConn()))


class TestSkillLoaderPGFirst:
    """db_first: PG 有 skill 时优先用 PG。"""

    def test_load_from_pg_when_available(self, tmp_path):
        """PG 有 skill → 用 PG 数据(不读文件系统)。"""
        manifest = {
            "name": "office",
            "version": "2.0.0",
            "description": "PG 版本",
            "scenario": "office",
            "dependencies": {"tools": [{"name": "file_read"}]},
        }
        conn = _FakeConnWithSkill(manifest, "PG prompt")
        loader = SkillLoader(dev_dir=str(tmp_path))

        import asyncio
        skill = asyncio.run(loader.load("office", conn=conn))

        assert skill.manifest.version == "2.0.0"
        assert skill.system_prompt == "PG prompt"


class TestSkillLoaderConfig:
    """SkillLoader 从 cfg 读取 dev_dir / runtime_source。"""

    def test_construct_from_cfg(self):
        """从 cfg dict 构造 SkillLoader。"""
        cfg = {"skills": {"storage": {"dev_dir": "./skills", "runtime_source": "db_first"}}}
        loader = SkillLoader.from_cfg(cfg)
        assert loader.dev_dir == "./skills"
        assert loader.runtime_source == "db_first"

    def test_default_dev_dir(self):
        """无 cfg 时默认 dev_dir=./skills。"""
        loader = SkillLoader()
        assert loader.dev_dir == "./skills"
