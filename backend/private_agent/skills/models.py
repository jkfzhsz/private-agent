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


class SkillPermissionRule(BaseModel):
    """阶段三批次3(T3.1, 调研 round2 §4.3.2) - 细粒度权限规则声明。

    示例:
      - tool: file_write
        paths: ["//sandbox/**"]          # 路径模式(fnmatch, 匹配 args.path)
      - tool: http_request
        domains: ["api.example.com"]     # 域名白名单(匹配 args.url)
    """

    tool: str
    paths: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class SkillPermissions(BaseModel):
    """蓝图 §7.2 permissions 段。

    阶段三批次3(T3.1) 新增 rules: 细粒度工具权限规则声明,
    激活时合入权限规则层(source=skill, 见 main._build_skill_permission_rules)。
    """

    allow_file_write: bool = False
    allow_network: bool = False
    sandbox_enabled: bool = False
    max_file_size_mb: int = 50
    rules: list[SkillPermissionRule] = Field(default_factory=list)


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
    # 2026-08-08: reasonix-skills 转换技能限定模型(空=通用)。匹配规则:
    # 会话模型名大小写不敏感包含任一 scope 项即命中(如 "deepseek-v4-flash"
    # 命中 ["deepseek"]); 前端技能面板按会话模型过滤/分组展示。
    model_scope: list[str] = Field(default_factory=list)
    # V1.1-3.6 智能体基础配置: 可视化元数据(头像/标签/对话参数覆盖)
    avatar: str = ""
    tags: list[str] = Field(default_factory=list)
    model_params: dict = Field(default_factory=dict)  # {temperature, top_p, max_tokens}
    # V1.1-3.6 改名: 用户可改的显示名(空=回退 name)。不改标识符 name,
    # 0 破坏 skill_binding / sessions.locked_skill_name / 文件目录引用。
    display_name: str = ""
    # 0.5.0 M1(2026-08-08): 场景专属名字(子瞻/白圭/清和, 与 display_name
    # 同步语义更明确; 前端展示一律 scene_name 回退 display_name → name)。
    scene_name: str = ""
    # 0.5.0 M1: 场景职责画像(角色/人格/价值观/工作流/规则, 结构化版本,
    # 供前端配置界面与注入模板复用; system_prompt 保持人读主版本)。
    scene_profile: dict = Field(default_factory=dict)
    # 0.5.0 M1: 场景专属 skill 挂载列表(空=通用, 所有场景均挂载)。
    # 用户 2026-08-08 确认: reasonix 15 技能本轮全部留空(三场景通用),
    # 机制保留供后续确有需要时按场景分流。
    scene_scope: list[str] = Field(default_factory=list)
    # 2026-08-15(蒋先生需求): 场景工作区 —— 该场景智能体的产物(文件/
    # 脚本/输出)默认落在自己的工作区目录。新会话创建时写入
    # sessions.workspace(ReactLoop 据此路由 file_write/sandbox 等)。
    # 空 = 使用全局默认 workspace_root。
    workspace: str = ""
    # 2026-08-15(A-1 Agent Harness 工程化): 场景级 harness 配置单元
    # (可版本化, 随 skill.yaml 文件存储; 缺省空 dict = 零行为变化)。
    # 结构见 docs/next-phase-plan-2026-08-15-agent-harness.md §3.1-A1:
    #   enabled: bool            # false 时整体跳过本 harness
    #   prompt_vars: dict        # system_prompt.md 中 {{var}} 占位符渲染
    #   tool_descriptions: dict  # 工具描述覆盖(仅影响暴露给模型的 schema)
    #   compression: dict        # 场景级压缩参数覆盖(keep_turns/keep_ratio)
    #   middleware: list         # 预留(当前恒空 = 零行为变化)
    harness: dict = Field(default_factory=dict)
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
