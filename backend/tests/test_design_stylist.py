"""2026-08-15 design-stylist 附加技能 + 三载体技能版式增强测试。

覆盖（设计文档 next-phase-plan-2026-08-14-design-stylist.md §6 步骤 4）:
- design-stylist skill.yaml 可解析为 SkillManifest，关键字段正确
- system_prompt.md 含 4 风格预设 / generate+revise 双模式 / 评审清单 / 硬性禁忌 / Typst 技术指引
- pptx / pdf / frontend_design 三个载体技能已追加"版式增强规范"增量段
- 增量段关键约束存在性（字体家族 / Typst+Edge / vendored）

注: 附加技能挂载机制本身由 test_admin_supplementary_skills.py 覆盖（需 DB），
本测试为纯文件级断言，不连接数据库（避免测试库并发互踩）。
"""

from pathlib import Path

import pytest
import yaml

from private_agent.skills.models import SkillManifest

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
STYLIST = "design-stylist"


def _load_yaml(name: str) -> dict:
    with (SKILLS_DIR / name / "skill.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _manifest(name: str) -> SkillManifest:
    return SkillManifest(**_load_yaml(name))


def _prompt(name: str) -> str:
    return (SKILLS_DIR / name / "system_prompt.md").read_text(encoding="utf-8")


# ── design-stylist manifest ──────────────────────────────────────────────


def test_stylist_manifest_parses():
    m = _manifest(STYLIST)
    assert m.name == STYLIST
    assert m.version
    assert m.display_name == "版式设计"
    assert m.enabled is True


def test_stylist_tools_whitelist():
    m = _manifest(STYLIST)
    tools = {t.name for t in m.dependencies.tools}
    assert {"file_read", "file_write", "code_execution"} <= tools
    by_name = {t.name: t for t in m.dependencies.tools}
    assert by_name["file_write"].safety_level_override == "elevated"
    assert by_name["file_read"].safety_level_override == "safe"


def test_stylist_no_network_and_frozen_budget():
    m = _manifest(STYLIST)
    assert m.permissions.allow_network is False
    assert m.max_frozen_token <= 4000


# ── design-stylist system_prompt 关键内容 ────────────────────────────────


def test_stylist_prompt_modes():
    p = _prompt(STYLIST)
    assert "generate" in p and "revise" in p
    assert "评审清单" in p and "修复清单" in p


@pytest.mark.parametrize(
    "preset",
    ["business-minimal", "tech-dark", "academic-clean", "light-luxury"],
)
def test_stylist_prompt_style_presets(preset):
    assert preset in _prompt(STYLIST)


def test_stylist_prompt_hard_rules():
    p = _prompt(STYLIST)
    for kw in ["彩色总数 ≤4", "8pt 网格", "45–75 字符", "无 3D", "硬性禁忌", "≤2"]:
        assert kw in p, f"缺少硬性约束关键词: {kw}"


def test_stylist_prompt_typst_guidance():
    """环境验证定稿的技术指引（typst.compile 传文件路径、页脚写法）。"""
    p = _prompt(STYLIST)
    assert "typst.compile" in p
    assert "文件路径" in p
    assert "counter(page).display()" in p
    assert "Microsoft YaHei" in p
    assert "Edge headless" in p


# ── 三载体技能增量段 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("skill", ["pptx", "pdf", "frontend_design"])
def test_carrier_skills_have_enhancement_section(skill):
    p = _prompt(skill)
    assert "版式增强规范" in p, f"{skill} 缺少版式增强段"
    assert "design-stylist 对齐" in p, f"{skill} 增量段未标注对齐来源"


def test_pptx_enhancement_constraints():
    p = _prompt("pptx")
    for kw in ["统一字体家族", "每页一个核心观点", "页眉页脚", "design-stylist 风格预设"]:
        assert kw in p, f"pptx 缺少约束: {kw}"


def test_pdf_enhancement_tech_selection():
    """pdf 增量段应声明 Typst 主方案 + Edge 兜底 + weasyprint 弃用。"""
    p = _prompt("pdf")
    for kw in ["Typst", "Edge headless", "weasyprint 弃用", "Microsoft YaHei", "输出前自查"]:
        assert kw in p, f"pdf 缺少选型声明: {kw}"


def test_frontend_enhancement_vendored():
    p = _prompt("frontend_design")
    for kw in ["vendored", "CDN", "卡片柔和圆角", "彩色总数 ≤4"]:
        assert kw in p, f"frontend_design 缺少约束: {kw}"


# ── 目录完整性 ───────────────────────────────────────────────────────────


def test_stylist_dir_complete():
    assert (SKILLS_DIR / STYLIST / "skill.yaml").is_file()
    assert (SKILLS_DIR / STYLIST / "system_prompt.md").is_file()
