"""M3 Skills 框架 - Pydantic schema(蓝图 §7.2,spec AC-5/AC-10)。

Source: plan/m3-skills-office step 7
- SkillManifest: skill.yaml 解析结果
- ToolDependency: 工具白名单条目(name + safety_level_override + enabled)
- Skill: 聚合根(manifest + system_prompt + tools_yaml)
- safety_level_override 枚举校验:∈ {safe, elevated, dangerous, None}
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SafetyLevel = Literal["safe", "elevated", "dangerous"]


class ToolDependency(BaseModel):
    """蓝图 §7.2 工具白名单条目。"""

    name: str
    safety_level_override: SafetyLevel | None = None
    enabled: bool = True


class SkillDependencies(BaseModel):
    """蓝图 §7.2 dependencies 段。"""

    tools: list[ToolDependency] = Field(default_factory=list)


class SkillPermissions(BaseModel):
    """蓝图 §7.2 permissions 段。"""

    allow_file_write: bool = False
    allow_network: bool = False
    sandbox_enabled: bool = False
    max_file_size_mb: int = 50


class SkillKnowledgeBase(BaseModel):
    """蓝图 §7.2 knowledge_base 段。"""

    enabled: bool = False
    scenario: str | None = None
    auto_retrieve: bool = False


class SkillExamples(BaseModel):
    """蓝图 §7.2 examples 段。"""

    enabled: bool = True
    max_examples: int = 3
    inject_to: str = "frozen_zone"


class SkillManifest(BaseModel):
    """蓝图 §7.2 skill.yaml 解析结果。"""

    name: str
    version: str
    description: str = ""
    scenario: str
    author: str = ""
    created_at: str = ""
    enabled: bool = True
    dependencies: SkillDependencies = Field(default_factory=SkillDependencies)
    permissions: SkillPermissions = Field(default_factory=SkillPermissions)
    prompt_vars: list[str] = Field(default_factory=list)
    knowledge_base: SkillKnowledgeBase = Field(default_factory=SkillKnowledgeBase)
    examples: SkillExamples = Field(default_factory=SkillExamples)
    max_frozen_token: int = 4000


class Skill(BaseModel):
    """Skill 聚合根:manifest + system_prompt 内容 + tools.yaml 内容。"""

    manifest: SkillManifest
    system_prompt: str
    tools_yaml: list[dict] = Field(default_factory=list)
