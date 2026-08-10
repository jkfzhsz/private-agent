"""0.5.0 M1 场景技能测试(设计文档 §8: test_scene_skill.py)。

覆盖:
- SkillManifest 扩展字段解析: scene_name / scene_profile / scene_scope
- 三场景命名与人格写入(子瞻=苏轼 / 白圭=商祖 / 清和=谢安)
- scene_scope 留空 = 通用(所有场景可挂载) —— reasonix 技能不声明 scene_scope
- skill_binding 配置: office 与 data_analysis 均装配 ifind 金融系
- auto_retrieve 打开(三场景 knowledge_base)
"""

import os
from pathlib import Path

import pytest
import yaml

from private_agent.skills.models import SkillManifest

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
REASONIX_SCENES = {
    "article-writer", "doc-coauthor", "docx", "duan-nian-jian", "git-worktree",
    "novelist", "novel-workflow", "pdf", "pptx", "prompts-chat-guide",
    "search-first", "systematic-debug", "tdd", "writing-humanizer", "xlsx",
}


def _load_manifest(name: str) -> dict:
    with (SKILLS_DIR / name / "skill.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _manifest(name: str) -> SkillManifest:
    return SkillManifest(**_load_manifest(name))


# ── SkillManifest 扩展字段 ───────────────────────────────────────────────


def test_manifest_new_fields_parse():
    """scene_name/scene_profile/scene_scope 字段可解析(带默认值向后兼容)。"""
    m = SkillManifest(
        name="office", version="1.0.0", scenario="office",
        scene_name="子瞻",
        scene_profile={"persona": "苏轼", "role": "工作学习"},
        scene_scope=[],
    )
    assert m.scene_name == "子瞻"
    assert m.scene_profile["persona"] == "苏轼"
    assert m.scene_scope == []
    # 缺省值: 空串/空 dict/空 list
    m2 = SkillManifest(name="x", version="1.0.0", scenario="office")
    assert m2.scene_name == ""
    assert m2.scene_profile == {}
    assert m2.scene_scope == []


# ── 三场景命名与人格 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "skill,scene_name,persona_keyword",
    [
        ("office", "子瞻", "苏轼"),
        ("data_analysis", "白圭", "白圭"),
        ("frontend_design", "清和", "谢安"),
    ],
)
def test_scene_naming_and_persona(skill, scene_name, persona_keyword):
    """三场景 scene_name + scene_profile.persona 写入正确。"""
    m = _manifest(skill)
    assert m.scene_name == scene_name
    assert m.display_name == scene_name  # 与 display_name 同步
    assert persona_keyword in m.scene_profile.get("persona", "")
    assert m.scene_profile.get("role", "")  # 职责非空
    assert isinstance(m.scene_profile.get("workflow", []), list)
    assert isinstance(m.scene_profile.get("rules", []), list)


def test_office_values_field():
    """子瞻(office) scene_profile.values 含政治意识三位统一。"""
    m = _manifest("office")
    values = m.scene_profile.get("values", "")
    assert "社会主义核心价值" in values
    assert "三位统一" in values


def test_baigui_investment_rules():
    """白圭(data_analysis) 规则含风险提示/数据来源/不承诺收益。"""
    m = _manifest("data_analysis")
    rules = " ".join(m.scene_profile.get("rules", []))
    assert "风险提示" in rules
    assert "不构成投资建议" in rules or "不承诺收益" in rules


def test_qinghe_health_and_beautify_rules():
    """清和(frontend_design) 规则含专业医师 + 美化职责。"""
    m = _manifest("frontend_design")
    rules = " ".join(m.scene_profile.get("rules", []))
    assert "专业医师" in rules
    assert "美化" in rules


# ── scene_scope 留空 = 通用 ──────────────────────────────────────────────


@pytest.mark.parametrize("skill", sorted(REASONIX_SCENES))
def test_reasonix_skills_scene_scope_generic(skill):
    """reasonix 15 技能 scene_scope 留空(通用, 三场景均挂载)。"""
    manifest_dict = _load_manifest(skill)
    assert "scene_scope" not in manifest_dict, (
        f"{skill} 不应声明 scene_scope(本轮全部留空=通用)"
    )
    m = SkillManifest(**manifest_dict)
    assert m.scene_scope == []


def test_reasonix_skills_present_on_disk():
    """reasonix 15 技能目录齐全(skill.yaml 存在)。"""
    missing = [s for s in sorted(REASONIX_SCENES) if not (SKILLS_DIR / s / "skill.yaml").exists()]
    assert not missing, f"缺失技能: {missing}"


# ── knowledge_base.auto_retrieve 打开(M2 前置) ──────────────────────────


@pytest.mark.parametrize("skill", ["office", "data_analysis", "frontend_design"])
def test_kb_auto_retrieve_enabled(skill):
    """三场景 auto_retrieve=True(M2 场景自动注入 KB 片段)。"""
    m = _manifest(skill)
    assert m.knowledge_base.enabled is True
    assert m.knowledge_base.auto_retrieve is True
    assert m.knowledge_base.scenario == skill


# ── skill_binding(0.5.0 M1: office 与 data_analysis 均装 ifind) ────────


def test_skill_binding_ifind_both_scenes():
    """config.yaml skill_binding: office 与 data_analysis 均含 ifind 全系通配。"""
    import private_agent.config.loader as loader

    cfg = loader.load_config()
    binding = cfg.get("tools", {}).get("mcp", {}).get("skill_binding", {})
    office = binding.get("office", [])
    data_analysis = binding.get("data_analysis", [])
    frontend = binding.get("frontend_design", [])
    assert "hexin-ifind-ds-*" in office, f"office 应装配 ifind 全系, 实际 {office}"
    assert "hexin-ifind-ds-*" in data_analysis
    assert "mempalace" in office and "Searchpin" in office
    # 清和通用: 不装金融系
    assert not any("ifind" in b for b in frontend), f"frontend_design 不应装 ifind, 实际 {frontend}"
    assert "mempalace" in frontend and "Searchpin" in frontend
