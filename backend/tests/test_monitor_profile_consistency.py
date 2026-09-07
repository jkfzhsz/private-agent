"""2026-09-07 S5(自检 B2 方案②): 无涯定义双源一致性校验。

背景: 无涯(monitor)无 skill.yaml 标准件, 运行时注入的 MONITOR_SCENE_PROFILE
(harness.py) 与 skills/monitor/system_prompt.md 是双源手工同步 ——
自检发现已发生真实漂移(profile 称"一切代码改动禁止未审批",
md 2026-08-16 已修订为"低风险直接做 + 核心改动审批", 行为级矛盾)。

本测试做双向锚点校验(非全文 diff —— 结构化画像与散文 prompt 无法逐字
对齐, 锚点校验概念级一致性, 任一源删除/变更核心概念即变红):
- 正向: profile 各字段的核心概念锚点必须存在于 system_prompt.md;
- 反向: system_prompt.md 的关键纪律锚点必须存在于 profile 序列化文本。

维护纪律: 修改 MONITOR_SCENE_PROFILE 或 system_prompt.md 任一源,
先跑本测试; 新增概念时同步补锚点。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from private_agent.skills.harness import MONITOR_SCENE_PROFILE

MONITOR_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "skills" / "monitor" / "system_prompt.md"
)


@pytest.fixture(scope="module")
def prompt_text() -> str:
    assert MONITOR_PROMPT_PATH.exists(), f"缺少 {MONITOR_PROMPT_PATH}"
    return MONITOR_PROMPT_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def profile_text() -> str:
    return json.dumps(MONITOR_SCENE_PROFILE, ensure_ascii=False)


# 正向锚点: (profile 字段, 概念, 须在 md 出现的 token 组)
FORWARD_ANCHORS: list[tuple[str, str, list[str]]] = [
    ("persona", "人格来源", ["无涯", "庄子", "项目进化者"]),
    ("role", "监控诊断职责", ["监控", "诊断"]),
    ("role", "评估闭环", ["评估"]),
    ("role", "经验库管理", ["经验库"]),
    ("role", "场景 prompt 优化", ["system_prompt", "子瞻", "白圭", "清和"]),
    ("values", "证据驱动", ["证据", "不臆造"]),
    ("values", "不冒充场景智能体", ["冒充"]),
    ("workflow", "状态感知", ["状态感知"]),
    ("workflow", "分级决策", ["低风险", "optim_plan", "审批"]),
    ("workflow", "改前备份", ["备份"]),
    ("workflow", "测试验证", ["验证"]),
    ("workflow", "反思沉淀", ["反思", "沉淀"]),
    ("rules", "核心改动审批通道", ["optim_plan", "apply_optim"]),
    ("rules", "低风险直接做", ["低风险", "直接"]),
    ("rules", "改前备份纪律", ["备份", ".bak"]),
    ("rules", "人格边界", ["人格化设定", "不可改"]),
    ("rules", "密钥/数据禁区", ["密钥", "用户数据", ".env"]),
]

# 反向锚点: md 中的关键纪律必须在 profile 有序列化对应
BACKWARD_ANCHORS: list[tuple[str, list[str]]] = [
    ("审批通道", ["optim_plan"]),
    ("低风险直接做", ["低风险"]),
    ("改前备份", ["备份"]),
    ("人格边界", ["人格"]),
    ("禁区", ["密钥"]),
]


def test_prompt_exists_and_nonempty(prompt_text: str):
    assert len(prompt_text) > 500


@pytest.mark.parametrize(
    "field,concept,tokens",
    FORWARD_ANCHORS,
    ids=[f"{f}:{c}" for f, c, _ in FORWARD_ANCHORS],
)
def test_profile_concepts_present_in_prompt(
    prompt_text: str, field: str, concept: str, tokens: list[str]
):
    """profile 每个核心概念在 system_prompt.md 有对应定义(正向)。"""
    assert field in MONITOR_SCENE_PROFILE, f"profile 缺字段 {field}"
    for token in tokens:
        assert token in prompt_text, (
            f"漂移告警: profile[{field}] 的概念「{concept}」锚点 "
            f"'{token}' 在 system_prompt.md 中不存在 —— "
            f"双源已漂移, 请同步后重跑本测试"
        )


@pytest.mark.parametrize(
    "concept,tokens",
    BACKWARD_ANCHORS,
    ids=[c for c, _ in BACKWARD_ANCHORS],
)
def test_prompt_disciplines_present_in_profile(
    profile_text: str, concept: str, tokens: list[str]
):
    """system_prompt.md 的关键纪律在 profile 有对应(反向)。"""
    for token in tokens:
        assert token in profile_text, (
            f"漂移告警: system_prompt.md 的纪律「{concept}」锚点 "
            f"'{token}' 在 MONITOR_SCENE_PROFILE 中不存在 —— "
            f"双源已漂移, 请同步后重跑本测试"
        )


def test_profile_required_fields():
    """profile 结构完整性(渲染依赖的五个字段)。"""
    for field in ("persona", "role", "values", "workflow", "rules"):
        assert MONITOR_SCENE_PROFILE.get(field), f"profile 缺字段 {field}"
    assert isinstance(MONITOR_SCENE_PROFILE["workflow"], list)
    assert isinstance(MONITOR_SCENE_PROFILE["rules"], list)
