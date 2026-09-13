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


# ── 2026-09-13 加固: PG 分支异常不得中断文件回退 ──────────────────────────
# 背景(实测事故): 建房取主持人 workspace 时, PG skills 行 manifest 残缺
# (jsonb 缺 version/scenario) → SkillManifest(**dict) 抛 TypeError → 异常
# 直接冒泡出 load(), 磁盘上合法的 skill.yaml 永远读不到。语义应与"PG 无该行"
# 一致: 告警 + 回退, 而不是失败。

class _FakeConnBrokenManifest:
    """PG 有行但 manifest 残缺(SkillManifest 构造会抛 TypeError)。"""

    def __init__(self, name: str = "office"):
        self._row = {
            "name": name,
            "version": "9.9.9",
            "description": "",
            # 缺 scenario(SkillManifest 必填) → 解析必失败
            "manifest": {"name": name, "version": "9.9.9"},
            "system_prompt": "PG prompt",
            "tools": [],
            "is_enabled": True,
        }

    async def fetchrow(self, *args, **kwargs):
        return self._row


class _FakeConnRaising:
    """PG 访问直接抛异常(模拟连接/查询故障)。"""

    def __init__(self, exc: Exception | None = None):
        self._exc = exc or RuntimeError("pg down")

    async def fetchrow(self, *args, **kwargs):
        raise self._exc

    async def fetch(self, *args, **kwargs):
        raise self._exc


class _FakeConnList:
    """list_all 用: 返回多行(可含残缺行)。"""

    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *args, **kwargs):
        return self._rows


def _row(name: str, manifest: dict, prompt: str = "PG prompt"):
    return {
        "name": name,
        "version": manifest.get("version", "1.0.0"),
        "description": "",
        "manifest": manifest,
        "system_prompt": prompt,
        "tools": [],
        "is_enabled": True,
    }


def _write_fs_skill(root, name: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "skill.yaml").write_text(
        _office_skill_yaml().replace("name: office", f"name: {name}"),
        encoding="utf-8",
    )
    (d / "system_prompt.md").write_text(_office_system_prompt(), encoding="utf-8")


class TestSkillLoaderPGErrorFallback:
    """PG 分支异常 → 必须回退文件系统(而非中断加载)。"""

    def test_broken_pg_manifest_falls_back_to_filesystem(self, tmp_path):
        """PG 行 manifest 残缺 → 读到的是文件系统技能(此前会抛 TypeError)。"""
        import asyncio

        _write_fs_skill(tmp_path, "office")
        loader = SkillLoader(dev_dir=str(tmp_path))
        skill = asyncio.run(loader.load("office", conn=_FakeConnBrokenManifest()))
        # 文件系统版本为 1.0.0; PG 行为 9.9.9(证明回退生效)
        assert skill.manifest.version == "1.0.0"
        assert skill.system_prompt == _office_system_prompt()

    def test_pg_connection_error_falls_back_to_filesystem(self, tmp_path):
        """PG 查询抛异常 → 同样回退文件系统。"""
        import asyncio

        _write_fs_skill(tmp_path, "office")
        loader = SkillLoader(dev_dir=str(tmp_path))
        skill = asyncio.run(loader.load("office", conn=_FakeConnRaising()))
        assert skill.manifest.name == "office"

    def test_both_broken_still_raises_not_found(self, tmp_path):
        """PG 异常 + 文件系统也没有 → 仍应抛 SkillNotFoundError(不静默吞掉)。"""
        import asyncio

        loader = SkillLoader(dev_dir=str(tmp_path))
        with pytest.raises(SkillNotFoundError):
            asyncio.run(loader.load("ghost", conn=_FakeConnRaising()))

    def test_valid_pg_still_wins(self, tmp_path):
        """零回归: PG 行合法时仍以 PG 为准(不被回退覆盖)。"""
        import asyncio

        _write_fs_skill(tmp_path, "office")
        manifest = {
            "name": "office",
            "version": "2.0.0",
            "description": "PG 版本",
            "scenario": "office",
        }
        conn = _FakeConnWithSkill(manifest, "PG prompt")
        loader = SkillLoader(dev_dir=str(tmp_path))
        skill = asyncio.run(loader.load("office", conn=conn))
        assert skill.manifest.version == "2.0.0"
        assert skill.system_prompt == "PG prompt"


class TestSkillLoaderListHardening:
    """list_all: 单行坏数据只跳过该行, 不丢弃整批 PG 技能。"""

    def test_broken_row_skipped_healthy_row_kept(self, tmp_path):
        import asyncio

        _write_fs_skill(tmp_path, "office")
        healthy = _row(
            "data_analysis",
            {"name": "data_analysis", "version": "3.1.0", "scenario": "data_analysis"},
        )
        broken = _row("office", {"name": "office"})  # 缺 version/scenario
        conn = _FakeConnList([healthy, broken])
        loader = SkillLoader(dev_dir=str(tmp_path))

        skills = asyncio.run(loader.list_all(conn=conn))
        names = sorted(s.manifest.name for s in skills)
        # 健康的 PG 行保留(没有整体回退到文件系统)
        assert names == ["data_analysis"]
        assert skills[0].manifest.version == "3.1.0"

    def test_all_rows_broken_falls_back_to_filesystem(self, tmp_path):
        import asyncio

        _write_fs_skill(tmp_path, "office")
        conn = _FakeConnList([_row("office", {"name": "office"})])
        loader = SkillLoader(dev_dir=str(tmp_path))

        skills = asyncio.run(loader.list_all(conn=conn))
        assert [s.manifest.name for s in skills] == ["office"]
        assert skills[0].manifest.version == "1.0.0"

    def test_pg_error_falls_back_to_filesystem(self, tmp_path):
        import asyncio

        _write_fs_skill(tmp_path, "office")
        loader = SkillLoader(dev_dir=str(tmp_path))
        skills = asyncio.run(loader.list_all(conn=_FakeConnRaising()))
        assert [s.manifest.name for s in skills] == ["office"]
