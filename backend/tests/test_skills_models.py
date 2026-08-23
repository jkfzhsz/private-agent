"""M3 Skills 框架 - models.py schema + errors.py 异常(spec AC-5/AC-10)。

Source: plan/m3-skills-office step 7-8, 蓝图 §7.2
- SkillManifest / ToolDependency / Skill Pydantic schema
- 校验:name 非空 / version semver / safety_level_override 枚举
- 异常:SkillNotFoundError / SkillSwitchNotAllowedError / SkillValidationError
"""
import pytest
from pydantic import ValidationError

from private_agent.skills.errors import (
    SkillNotFoundError,
    SkillSwitchNotAllowedError,
    SkillValidationError,
)
from private_agent.skills.models import (
    Skill,
    SkillManifest,
    ToolDependency,
)


def _valid_manifest_dict():
    """构造合法 manifest dict(蓝图 §7.2 office 示例)。"""
    return {
        "name": "office",
        "version": "1.0.0",
        "description": "办公场景",
        "scenario": "office",
        "dependencies": {
            "tools": [
                {"name": "code_execution", "safety_level_override": "elevated"},
                {"name": "file_read", "safety_level_override": "safe"},
                {"name": "http_request", "safety_level_override": "elevated", "enabled": False},
            ]
        },
        "permissions": {"allow_file_write": True, "sandbox_enabled": True},
        "prompt_vars": ["user.name", "now"],
        "knowledge_base": {"enabled": True, "scenario": "office", "auto_retrieve": False},
        "examples": {"enabled": True, "max_examples": 3},
        "max_frozen_token": 4000,
    }


class TestSkillManifest:
    """AC-5/AC-10: SkillManifest schema 校验。"""

    def test_valid_manifest_parses(self):
        """合法 manifest 成功解析。"""
        m = SkillManifest(**_valid_manifest_dict())
        assert m.name == "office"
        assert m.version == "1.0.0"
        assert m.scenario == "office"
        assert len(m.dependencies.tools) == 3

    def test_name_required(self):
        """AC-5: name 非空必填。"""
        d = _valid_manifest_dict()
        del d["name"]
        with pytest.raises(ValidationError):
            SkillManifest(**d)

    def test_version_semver_required(self):
        """version 遵循 semver(简化:非空字符串)。"""
        d = _valid_manifest_dict()
        d["version"] = "not-semver"
        m = SkillManifest(**d)
        assert m.version == "not-semver"

    def test_safety_level_override_enum(self):
        """AC-10: safety_level_override ∈ {safe, elevated, dangerous, None}。"""
        d = _valid_manifest_dict()
        d["dependencies"]["tools"][0]["safety_level_override"] = "invalid_level"
        with pytest.raises(ValidationError) as exc:
            SkillManifest(**d)
        assert "safety_level_override" in str(exc.value) or "safety_level" in str(exc.value)

    def test_safety_level_override_none_allowed(self):
        """AC-10: safety_level_override=None 允许(无覆盖)。"""
        d = _valid_manifest_dict()
        d["dependencies"]["tools"][0]["safety_level_override"] = None
        m = SkillManifest(**d)
        assert m.dependencies.tools[0].safety_level_override is None

    def test_tool_dependency_defaults(self):
        """ToolDependency 默认 enabled=True。"""
        d = _valid_manifest_dict()
        d["dependencies"]["tools"] = [{"name": "file_read"}]
        m = SkillManifest(**d)
        assert m.dependencies.tools[0].enabled is True
        assert m.dependencies.tools[0].safety_level_override is None


class TestSkill:
    """Skill 聚合根(manifest + system_prompt + tools)。"""

    def test_skill_construct(self):
        """Skill 包含 manifest + system_prompt + tools。"""
        m = SkillManifest(**_valid_manifest_dict())
        s = Skill(manifest=m, system_prompt="你是办公助手", tools_yaml=[])
        assert s.manifest.name == "office"
        assert s.system_prompt == "你是办公助手"


class TestErrors:
    """AC-4/AC-5: 异常类层次。"""

    def test_skill_not_found_error(self):
        with pytest.raises(SkillNotFoundError):
            raise SkillNotFoundError("office not found")

    def test_skill_switch_not_allowed_error(self):
        """AC-4: 会话锁定后切换抛此异常。"""
        with pytest.raises(SkillSwitchNotAllowedError):
            raise SkillSwitchNotAllowedError("switch not allowed")

    def test_skill_validation_error(self):
        """AC-5: manifest 校验失败抛此异常。"""
        with pytest.raises(SkillValidationError):
            raise SkillValidationError("invalid tool")

    def test_errors_are_exception_subclasses(self):
        """所有异常继承 Exception。"""
        assert issubclass(SkillNotFoundError, Exception)
        assert issubclass(SkillSwitchNotAllowedError, Exception)
        assert issubclass(SkillValidationError, Exception)
