"""M3 Skills 框架 - SkillManager.activate_skill 全流程(spec AC-1/4/5)。

Source: plan/m3-skills-office step 13, 蓝图 §7.3/§7.4
- activate_skill: load → 校验 → 模板替换 → 少样本 → 白名单 → Frozen Zone → hash → 锁定
- AC-1: 成功 activate 返回 locked_version + frozen_hash,sessions 表写入
- AC-4: 已锁定 session 再次 activate 不同 skill → SkillSwitchNotAllowedError
- AC-5: manifest 引用不存在工具 → SkillValidationError
"""
import asyncio
import json
from datetime import datetime

import pytest

from private_agent.skills.errors import (
    SkillNotFoundError,
    SkillSwitchNotAllowedError,
    SkillValidationError,
)
from private_agent.skills.example_loader import ExampleLoader
from private_agent.skills.loader import SkillLoader
from private_agent.skills.manager import SkillManager
from private_agent.skills.models import Skill, SkillManifest
from private_agent.tools.defs import ToolDef, ToolResult
from private_agent.tools.registry import ToolRegistry


def _office_skill_yaml():
    return """\
name: office
version: "1.0.0"
description: "办公场景"
scenario: office
enabled: true
dependencies:
  tools:
    - name: file_read
      safety_level_override: safe
    - name: file_write
      safety_level_override: elevated
    - name: http_request
      safety_level_override: elevated
      enabled: false
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
    return "你是办公助手 {{user.name}},当前场景 {{skills.active}}。"


def _make_registry_with_tools() -> ToolRegistry:
    """构造含 file_read/file_write/http_request 的 ToolRegistry。"""
    reg = ToolRegistry()
    async def _h(args): return ToolResult(output="ok")
    for name in ["file_read", "file_write", "http_request", "calculator"]:
        reg.register_builtin(name, ToolDef(
            name=name, description=name, parameters_schema={"type": "object"}, handler=_h
        ))
    return reg


class _FakeConn:
    """模拟 asyncpg.Connection(支持 fetchrow + execute)。"""

    def __init__(self, locked_skill=None):
        self._locked_skill = locked_skill
        self.updates = []

    async def fetchrow(self, query, *args):
        if "sessions" in query.lower():
            if self._locked_skill is not None:
                return {"locked_skill_name": self._locked_skill}
            return {"locked_skill_name": None, "created_at": datetime(2026, 1, 1)}
        if "skills" in query.lower():
            return None
        return None

    async def execute(self, query, *args):
        self.updates.append((query, args))


def _setup_office_skill(tmp_path):
    """在 tmp_path 下创建 office skill 文件。"""
    skill_dir = tmp_path / "office"
    skill_dir.mkdir()
    (skill_dir / "skill.yaml").write_text(_office_skill_yaml(), encoding="utf-8")
    (skill_dir / "system_prompt.md").write_text(_office_system_prompt(), encoding="utf-8")
    ex_dir = skill_dir / "examples"
    ex_dir.mkdir()
    (ex_dir / "01_excel.md").write_text("Excel 汇总示例", encoding="utf-8")


class TestActivateSkillSuccess:
    """AC-1: 成功 activate 返回 locked_version + frozen_hash。"""

    def test_activate_returns_version_and_hash(self, tmp_path):
        """activate office → 返回 locked_version=1.0.0 + frozen_hash(64 hex)。"""
        _setup_office_skill(tmp_path)
        loader = SkillLoader(dev_dir=str(tmp_path))
        ex_loader = ExampleLoader(dev_dir=str(tmp_path))
        registry = _make_registry_with_tools()
        mgr = SkillManager(loader=loader, example_loader=ex_loader, tool_registry=registry)
        conn = _FakeConn()

        result = asyncio.run(mgr.activate_skill("office", session_id=1, conn=conn))

        assert result["locked_version"] == "1.0.0"
        assert len(result["frozen_hash"]) == 64
        int(result["frozen_hash"], 16)

    def test_activate_writes_session_lock(self, tmp_path):
        """AC-1: sessions 表 UPDATE locked_skill_name/version/frozen_hash。"""
        _setup_office_skill(tmp_path)
        loader = SkillLoader(dev_dir=str(tmp_path))
        ex_loader = ExampleLoader(dev_dir=str(tmp_path))
        registry = _make_registry_with_tools()
        mgr = SkillManager(loader=loader, example_loader=ex_loader, tool_registry=registry)
        conn = _FakeConn()

        asyncio.run(mgr.activate_skill("office", session_id=1, conn=conn))

        assert any("locked_skill_name" in u[0] and "office" in str(u[1]) for u in conn.updates)

    def test_activate_returns_filtered_tools(self, tmp_path):
        """AC-3: 返回过滤后的 tools(http_request enabled=false 不含)。"""
        _setup_office_skill(tmp_path)
        loader = SkillLoader(dev_dir=str(tmp_path))
        ex_loader = ExampleLoader(dev_dir=str(tmp_path))
        registry = _make_registry_with_tools()
        mgr = SkillManager(loader=loader, example_loader=ex_loader, tool_registry=registry)
        conn = _FakeConn()

        result = asyncio.run(mgr.activate_skill("office", session_id=1, conn=conn))

        tool_names = [t.name for t in result["filtered_tools"]]
        assert "file_read" in tool_names
        assert "file_write" in tool_names
        assert "http_request" not in tool_names
        assert "calculator" not in tool_names

    def test_template_vars_replaced(self, tmp_path):
        """模板变量 {{user.name}} / {{skills.active}} 被替换。"""
        _setup_office_skill(tmp_path)
        loader = SkillLoader(dev_dir=str(tmp_path))
        ex_loader = ExampleLoader(dev_dir=str(tmp_path))
        registry = _make_registry_with_tools()
        mgr = SkillManager(loader=loader, example_loader=ex_loader, tool_registry=registry)
        conn = _FakeConn()

        result = asyncio.run(mgr.activate_skill("office", session_id=1, conn=conn))

        prompt = result["system_prompt"]
        assert "{{user.name}}" not in prompt
        assert "{{skills.active}}" not in prompt
        assert "office" in prompt


class TestActivateSkillSwitchRejected:
    """AC-4: 已锁定 session 再次 activate 不同 skill → SkillSwitchNotAllowedError。"""

    def test_switch_rejected(self, tmp_path):
        """session 已锁定 data_analysis → activate office 抛 SkillSwitchNotAllowedError。"""
        _setup_office_skill(tmp_path)
        loader = SkillLoader(dev_dir=str(tmp_path))
        ex_loader = ExampleLoader(dev_dir=str(tmp_path))
        registry = _make_registry_with_tools()
        mgr = SkillManager(loader=loader, example_loader=ex_loader, tool_registry=registry)
        conn = _FakeConn(locked_skill="data_analysis")

        with pytest.raises(SkillSwitchNotAllowedError):
            asyncio.run(mgr.activate_skill("office", session_id=1, conn=conn))

    def test_same_skill_reactivate_allowed(self, tmp_path):
        """已锁定 office → 再次 activate office 允许(幂等)。"""
        _setup_office_skill(tmp_path)
        loader = SkillLoader(dev_dir=str(tmp_path))
        ex_loader = ExampleLoader(dev_dir=str(tmp_path))
        registry = _make_registry_with_tools()
        mgr = SkillManager(loader=loader, example_loader=ex_loader, tool_registry=registry)
        conn = _FakeConn(locked_skill="office")

        result = asyncio.run(mgr.activate_skill("office", session_id=1, conn=conn))
        assert result["locked_version"] == "1.0.0"


class TestActivateSkillValidation:
    """AC-5: manifest 引用不存在工具 → SkillValidationError。"""

    def test_invalid_tool_raises_validation_error(self, tmp_path):
        """skill.yaml 引用 fake_tool → SkillValidationError。"""
        skill_dir = tmp_path / "bad"
        skill_dir.mkdir()
        bad_yaml = _office_skill_yaml().replace("file_read", "fake_tool")
        (skill_dir / "skill.yaml").write_text(bad_yaml, encoding="utf-8")
        (skill_dir / "system_prompt.md").write_text("prompt", encoding="utf-8")

        loader = SkillLoader(dev_dir=str(tmp_path))
        ex_loader = ExampleLoader(dev_dir=str(tmp_path))
        registry = _make_registry_with_tools()
        mgr = SkillManager(loader=loader, example_loader=ex_loader, tool_registry=registry)
        conn = _FakeConn()

        with pytest.raises(SkillValidationError):
            asyncio.run(mgr.activate_skill("bad", session_id=1, conn=conn))


class TestActivateSkillNotFound:
    """Skill 不存在 → SkillNotFoundError(loader 抛,manager 透传)。"""

    def test_skill_not_found(self, tmp_path):
        loader = SkillLoader(dev_dir=str(tmp_path))
        ex_loader = ExampleLoader(dev_dir=str(tmp_path))
        registry = _make_registry_with_tools()
        mgr = SkillManager(loader=loader, example_loader=ex_loader, tool_registry=registry)
        conn = _FakeConn()

        with pytest.raises(SkillNotFoundError):
            asyncio.run(mgr.activate_skill("nonexistent", session_id=1, conn=conn))
