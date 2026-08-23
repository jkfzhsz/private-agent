"""A-1 Agent Harness 工程化测试(设计文档 next-phase-plan-2026-08-15 §4 批次 A-1)。

覆盖:
- SkillManifest.harness 字段解析(默认 {} 向后兼容)
- 三场景 skill.yaml harness 段解析(office/data_analysis/frontend_design)
- harness.render_prompt_vars: {{var}} 占位符渲染(缺失回退空)
- harness.build_scene_profile_block: [Scene Profile] 块渲染(空 scene_profile → 空串)
- harness.apply_tool_descriptions: 描述覆盖归一化
- SkillManager.build_system_prompt: harness 启用时追加 [Scene Profile] 块;
  未配置 → 输出与现状一致(零回归)
- ReactLoop._to_schema: 工具描述覆盖生效 / 未命中不变
- agent-profile.json harness 段读取(_load_agent_profile_harness)
"""
import asyncio
import json
import os
from pathlib import Path

import pytest
import yaml

from private_agent.core.react_loop import ReactLoop
from private_agent.skills.harness import (
    apply_tool_descriptions,
    build_scene_profile_block,
    render_prompt_vars,
)
from private_agent.skills.loader import SkillLoader
from private_agent.skills.models import SkillManifest
from private_agent.tools.defs import ToolDef, ToolResult

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
SCENES = ("office", "data_analysis", "frontend_design")


def _load_manifest(name: str) -> SkillManifest:
    with (SKILLS_DIR / name / "skill.yaml").open(encoding="utf-8") as f:
        return SkillManifest(**yaml.safe_load(f))


# ── SkillManifest.harness 字段 ────────────────────────────────────────────


def test_manifest_harness_field_default_empty():
    """harness 缺省空 dict(向后兼容, 旧 manifest 无该字段不报错)。"""
    m = SkillManifest(name="x", version="1.0.0", scenario="office")
    assert m.harness == {}


def test_manifest_harness_field_parses():
    """harness 段可解析为 dict。"""
    m = SkillManifest(
        name="office",
        version="1.0.0",
        scenario="office",
        harness={
            "enabled": True,
            "prompt_vars": {"audience": "信贷人员"},
            "tool_descriptions": {"web_search": "检索公开网页信息"},
            "compression": {"keep_turns": 8, "keep_ratio": 0.15},
            "middleware": [],
        },
    )
    assert m.harness["enabled"] is True
    assert m.harness["prompt_vars"]["audience"] == "信贷人员"
    assert m.harness["compression"]["keep_ratio"] == 0.15
    assert m.harness["middleware"] == []


@pytest.mark.parametrize("scene", SCENES)
def test_scene_skill_harness_configured(scene):
    """三场景 skill.yaml 均配置 harness(enabled + prompt_vars + tool_descriptions)。"""
    m = _load_manifest(scene)
    assert m.harness.get("enabled") is True
    assert m.harness.get("prompt_vars", {}).get("audience")
    assert isinstance(m.harness.get("tool_descriptions", {}), dict)
    assert m.harness.get("tool_descriptions", {}).get("code_execution")
    # compression 段含场景级 keep_turns/keep_ratio(B-1 消费)
    comp = m.harness.get("compression", {})
    assert comp.get("keep_turns", 0) > 0
    assert 0 < comp.get("keep_ratio", 0) <= 1
    # middleware 恒空(预留, 零行为变化)
    assert m.harness.get("middleware") == []


# ── harness 纯函数渲染 ─────────────────────────────────────────────────────


def test_render_prompt_vars_replaces_known_and_drops_unknown():
    """已知占位符替换; 未匹配占位符回退为空; 空 vars 零变化。"""
    prompt = "面向 {{audience}}, 语气 {{tone}}, 未知 {{missing_var}}"
    out = render_prompt_vars(prompt, {"audience": "信贷人员", "tone": "专业"})
    assert "面向 信贷人员" in out
    assert "语气 专业" in out
    assert "{{missing_var}}" not in out
    # 空 prompt_vars → 原文不变
    assert render_prompt_vars(prompt, None) == prompt
    assert render_prompt_vars(prompt, {}) == prompt


def test_build_scene_profile_block_renders_all_fields():
    """[Scene Profile] 块渲染 persona/role/values/workflow/rules + prompt_vars。"""
    sp = {
        "persona": "苏轼",
        "role": "工作与学习伙伴",
        "values": "核心价值观",
        "workflow": ["理解需求", "规划步骤", "执行"],
        "rules": ["引用标注来源", "面向 {{audience}}"],
    }
    block = build_scene_profile_block(sp, {"audience": "信贷人员"})
    assert block.startswith("## [Scene Profile]")
    assert "人格: 苏轼" in block
    assert "职责: 工作与学习伙伴" in block
    assert "价值观: 核心价值观" in block
    assert "工作流: 理解需求 → 规划步骤 → 执行" in block
    assert "面向 信贷人员" in block  # prompt_vars 渲染进 rules


def test_build_scene_profile_block_empty_profile_returns_empty():
    """scene_profile 为空 → 返回空串(零回归: 不追加空块)。"""
    assert build_scene_profile_block(None) == ""
    assert build_scene_profile_block({}) == ""


def test_apply_tool_descriptions_normalizes():
    """描述覆盖归一化; 空 dict → 空映射。"""
    descs = apply_tool_descriptions({"web_search": "检索公开网页信息", "x": ""})
    assert descs["web_search"]["description"] == "检索公开网页信息"
    assert "x" not in descs  # 空值被过滤
    assert apply_tool_descriptions(None) == {}
    assert apply_tool_descriptions({}) == {}


# ── SkillManager.build_system_prompt [Scene Profile] 注入 ──────────────────


def _make_skill(manifest_dict: dict, system_prompt: str = "你是场景助手"):
    from private_agent.skills.models import Skill

    return Skill(
        manifest=SkillManifest(**manifest_dict),
        system_prompt=system_prompt,
    )


async def _build_prompt_with(manifest_dict: dict) -> str:
    """构造 SkillManager 并调 build_system_prompt(免 DB: fetchrow 返回 None)。"""
    from unittest.mock import AsyncMock

    from private_agent.skills.example_loader import ExampleLoader
    from private_agent.skills.manager import SkillManager

    conn = AsyncMock()
    conn.fetchrow.return_value = None  # created_at 无 → None
    loader = AsyncMock()
    loader.load = AsyncMock(return_value=_make_skill(manifest_dict))
    mgr = SkillManager(
        loader=loader,
        example_loader=ExampleLoader(),
        tool_registry=__import__(
            "private_agent.tools.registry", fromlist=["ToolRegistry"]
        ).ToolRegistry(),
    )
    return await mgr.build_system_prompt(
        _make_skill(manifest_dict), "office", 1, conn
    )


def test_build_system_prompt_injects_scene_profile_block():
    """harness.enabled + scene_profile 非空 → 追加 [Scene Profile] 块。"""
    manifest = {
        "name": "office",
        "version": "1.0.0",
        "scenario": "office",
        "scene_profile": {
            "persona": "苏轼",
            "role": "工作与学习伙伴",
            "workflow": ["理解需求"],
            "rules": ["面向 {{audience}}"],
        },
        "harness": {
            "enabled": True,
            "prompt_vars": {"audience": "国有商业银行信贷人员"},
        },
        "examples": {"enabled": False},
    }
    out = asyncio.run(_build_prompt_with(manifest))
    assert "## [Scene Profile]" in out
    assert "人格: 苏轼" in out
    assert "面向 国有商业银行信贷人员" in out


def test_build_system_prompt_zero_regression_without_harness():
    """harness 未配置 → 输出与无 harness 时代完全一致(不含 [Scene Profile])。"""
    base = {
        "name": "office",
        "version": "1.0.0",
        "scenario": "office",
        "scene_profile": {"persona": "苏轼", "role": "伙伴"},
        "examples": {"enabled": False},
    }
    out = asyncio.run(_build_prompt_with(base))
    assert "## [Scene Profile]" not in out
    assert "你是场景助手" in out


def test_build_system_prompt_harness_disabled_skips():
    """harness.enabled=false → 不注入(与现状一致)。"""
    manifest = {
        "name": "office",
        "version": "1.0.0",
        "scenario": "office",
        "scene_profile": {"persona": "苏轼"},
        "harness": {"enabled": False, "prompt_vars": {"audience": "x"}},
        "examples": {"enabled": False},
    }
    out = asyncio.run(_build_prompt_with(manifest))
    assert "## [Scene Profile]" not in out


# ── ReactLoop._to_schema 工具描述覆盖 ──────────────────────────────────────


def _make_tool(name: str, description: str | None = None) -> ToolDef:
    async def _h(args):  # noqa: ARG001
        return ToolResult(output="ok")

    return ToolDef(
        name=name,
        description=description or f"默认描述 {name}",
        parameters_schema={"type": "object"},
        handler=_h,
    )


def test_react_loop_tool_schema_description_override():
    """命中 harness.tool_descriptions 的工具描述被覆盖; 未命中不变。"""
    tools = [_make_tool("web_search"), _make_tool("calculator")]
    loop = ReactLoop(
        session_id=1,
        context_manager=None,
        adapter=None,
        tools=tools,
        conn=None,
        tool_descriptions={"web_search": "检索公开网页信息。研究类任务优先。"},
    )
    schemas = loop._tool_schemas
    by_name = {s["function"]["name"]: s["function"]["description"] for s in schemas}
    assert by_name["web_search"] == "检索公开网页信息。研究类任务优先。"
    assert by_name["calculator"] == "默认描述 calculator"


def test_react_loop_tool_schema_zero_regression_without_overrides():
    """无 tool_descriptions → schema 与默认完全一致。"""
    tools = [_make_tool("web_search", "原始描述")]
    loop = ReactLoop(
        session_id=1,
        context_manager=None,
        adapter=None,
        tools=tools,
        conn=None,
    )
    assert loop._tool_schemas[0]["function"]["description"] == "原始描述"


# ── agent-profile.json harness 通道(monitor) ──────────────────────────────


def test_load_agent_profile_harness(monkeypatch, tmp_path):
    """agent-profile.json harness 段读取; 缺失返回 None(零回归)。"""
    from private_agent import main as pa_main

    # 无文件 → None
    monkeypatch.setenv("PA_USER_DATA", str(tmp_path))
    assert pa_main._load_agent_profile_harness({}) is None

    # 有 harness 段 → 返回 dict
    profile_path = tmp_path / "agent-profile.json"
    profile_path.write_text(
        json.dumps(
            {
                "display_name": "无涯",
                "harness": {
                    "enabled": True,
                    "tool_descriptions": {"optim_plan": "提交项目进化方案"},
                    "compression": {"keep_turns": 8, "keep_ratio": 0.15},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    harness = pa_main._load_agent_profile_harness({})
    assert harness is not None
    assert harness["enabled"] is True
    assert "optim_plan" in harness["tool_descriptions"]
    assert harness["compression"]["keep_ratio"] == 0.15


def test_monitor_scene_profile_block_renders():
    """无涯内置画像渲染为 [Scene Profile] 块(含职责与规则)。"""
    from private_agent.skills.harness import (
        MONITOR_SCENE_PROFILE,
        build_scene_profile_block,
    )

    block = build_scene_profile_block(MONITOR_SCENE_PROFILE)
    assert block.startswith("## [Scene Profile]")
    assert "无涯" in block
    assert "项目进化" in block
    assert "optim_plan" in block  # 工作流含提议进化
