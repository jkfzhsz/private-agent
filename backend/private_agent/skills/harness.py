"""A-1 Agent Harness 工程化 —— scene_profile 结构化渲染 + 工具描述覆盖。

设计文档: docs/next-phase-plan-2026-08-15-agent-harness.md §3.1-A1/A2

职责(纯函数, 无 DB 依赖, 测试友好):
- build_scene_profile_block(scene_profile, prompt_vars): 渲染 [Scene Profile] 块
  (persona/role/values/workflow/rules + prompt_vars 占位符渲染)
- apply_tool_descriptions(tool_descriptions): 工具描述覆盖(仅影响暴露给
  模型的 schema 描述, 不改 handler)
- render_prompt_vars(prompt, prompt_vars): {{var}} 占位符替换(缺失回退空)

零回归保障:
- harness 未配置 / scene_profile 为空 → 返回空串, 注入结果与现状完全一致
- prompt_vars 缺失的占位符替换为空串, 不引入异常
"""
from __future__ import annotations

import re
from typing import Any

__all__ = [
    "build_scene_profile_block",
    "render_prompt_vars",
    "apply_tool_descriptions",
    "MONITOR_SCENE_PROFILE",
]

# 无涯(monitor)内置场景画像 —— 非 skill.yaml 驱动, 由 agent-profile.json
# harness 通道激活(设计文档 §3.1-A4)。内容与 skills/monitor/system_prompt.md
# 保持语义一致(结构化版本, 供 [Scene Profile] 块渲染)。
MONITOR_SCENE_PROFILE: dict[str, Any] = {
    "persona": "无涯 · 项目进化者(取自《庄子》'吾生也有涯，而知也无涯')",
    "role": "系统监控与优化者: 监控运行状态、诊断代码与架构缺陷、驱动评估闭环、管理经验库、优化子瞻/白圭/清和的 system_prompt 与工具实现",
    "values": "进化建议基于证据(代码+指标+评估结果), 不臆造; 不冒充场景智能体; 尊重用户定义的人格边界",
    "workflow": ["状态感知", "诊断分析", "提议进化(optim_plan)", "用户审批", "执行改动", "测试验证", "反思沉淀"],
    "rules": [
        "未经审批直接修改代码禁止(必须先 optim_plan → approved → 执行)",
        "修改代码前先备份原文件",
        "不修改场景智能体的人格化设定(用户定义不可改)",
    ],
}


def render_prompt_vars(prompt: str, prompt_vars: dict | None) -> str:
    """渲染 {{var}} 占位符(缺失回退为空串)。

    Args:
        prompt: system_prompt.md 原文(含 {{var}} 占位符)。
        prompt_vars: harness.prompt_vars dict {var: value}。

    Returns:
        替换后的 prompt。prompt_vars 为空/None 时返回原文(零变化)。
    """
    if not prompt or not prompt_vars:
        return prompt
    result = prompt
    for key, value in prompt_vars.items():
        result = result.replace("{{" + str(key) + "}}", str(value))
    # 未匹配的占位符回退为空(避免模板残留 {{undefined}})
    result = re.sub(r"\{\{\s*[A-Za-z_][A-Za-z0-9_.]*\s*\}\}", "", result)
    return result


def build_scene_profile_block(
    scene_profile: dict | None,
    prompt_vars: dict | None = None,
) -> str:
    """渲染结构化 [Scene Profile] 块(供系统提示词追加)。

    Args:
        scene_profile: skill.yaml scene_profile 段(persona/role/values/
            workflow/rules)。
        prompt_vars: harness.prompt_vars(渲染到块内文本占位符)。

    Returns:
        [Scene Profile] 块文本;scene_profile 为空 → 返回空串(零回归)。
    """
    if not scene_profile:
        return ""
    prompt_vars = prompt_vars or {}
    lines: list[str] = ["## [Scene Profile]"]
    # persona/role/values 为单行字符串
    for field, label in (
        ("persona", "人格"),
        ("role", "职责"),
        ("values", "价值观"),
    ):
        value = scene_profile.get(field)
        if value:
            value = render_prompt_vars(str(value), prompt_vars)
            lines.append(f"- {label}: {value}")
    # workflow 为 list[str] → 单行箭头连接
    workflow = scene_profile.get("workflow")
    if workflow:
        steps = [str(w) for w in workflow]
        wf = render_prompt_vars(" → ".join(steps), prompt_vars)
        lines.append(f"- 工作流: {wf}")
    # rules 为 list[str] → 多行
    rules = scene_profile.get("rules")
    if rules:
        lines.append("- 规则:")
        for r in rules:
            rr = render_prompt_vars(str(r), prompt_vars)
            lines.append(f"  - {rr}")
    return "\n".join(lines)


def apply_tool_descriptions(
    tool_descriptions: dict | None,
) -> dict[str, dict[str, str]]:
    """工具描述覆盖 → 内部映射(供 ReactLoop schema 组装时按工具名命中替换)。

    Args:
        tool_descriptions: harness.tool_descriptions {tool_name: description}。

    Returns:
        归一化后的映射(tool_name → {"description": ...})。
    """
    if not tool_descriptions:
        return {}
    return {
        str(name): {"description": str(desc)}
        for name, desc in tool_descriptions.items()
        if desc
    }
