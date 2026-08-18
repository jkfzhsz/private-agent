"""A-2 场景评测集结构测试(设计文档 next-phase-plan-2026-08-15 §3.1-A3)。

覆盖:
- 4 个 scenarios.yaml 可加载/校验(meta + tasks 结构)
- 任务 id 唯一 / 必填字段完整 / success_criteria 非空
- 任务总数 = 38(子瞻 10 + 白圭 10 + 清和 10 + 无涯 8)
- harness_checks 存在(harness 迭代回归锚点)
"""
import pytest

from private_agent.eval.scenes_loader import (
    SCENE_FILES,
    load_all_scenes,
    scene_summary,
    validate_all,
    validate_scene,
)

EXPECTED_COUNTS = {
    "office": 10,
    "data_analysis": 10,
    "frontend_design": 10,
    "monitor": 8,
}


def test_all_scene_files_exist():
    """4 个 scenarios.yaml 文件齐全。"""
    missing = [s for s, p in SCENE_FILES.items() if not p.exists()]
    assert not missing, f"缺失评测集文件: {missing}"


def test_all_scenes_validate():
    """全部场景结构校验通过(meta + tasks 必填字段 + id 唯一)。"""
    validated = validate_all()
    assert set(validated) == set(EXPECTED_COUNTS)


@pytest.mark.parametrize("scene", sorted(EXPECTED_COUNTS))
def test_scene_task_count(scene):
    """各场景任务数符合设计文档(子瞻 10/白圭 10/清和 10/无涯 8)。"""
    data = validate_scene(scene)
    assert len(data["tasks"]) == EXPECTED_COUNTS[scene]


def test_total_task_count_38():
    """全部 38 项任务。"""
    assert scene_summary() == EXPECTED_COUNTS
    assert sum(EXPECTED_COUNTS.values()) == 38


def test_tasks_have_required_fields():
    """每任务含 id/title/description/success_criteria, 且判据非空。"""
    for scene, data in load_all_scenes().items():
        for t in data["tasks"]:
            assert t["id"], f"{scene}: 任务缺 id"
            assert t["title"], f"{scene}: {t['id']} 缺 title"
            assert t["description"], f"{scene}: {t['id']} 缺 description"
            assert isinstance(t["success_criteria"], list) and t["success_criteria"], (
                f"{scene}: {t['id']} success_criteria 非空"
            )


def test_task_ids_unique():
    """任务 id 全局唯一(跨场景不冲突)。"""
    all_ids = []
    for data in load_all_scenes().values():
        all_ids.extend(t["id"] for t in data["tasks"])
    assert len(all_ids) == len(set(all_ids)) == 38


def test_scene_ids_prefix_matches():
    """任务 id 前缀 = 场景名(便于筛选/追踪)。"""
    for scene, data in load_all_scenes().items():
        for t in data["tasks"]:
            assert t["id"].startswith(scene + "-"), (
                f"{scene}: {t['id']} 前缀应与场景一致"
            )


def test_harness_checks_present():
    """meta.harness_checks 存在且非空(harness 迭代回归锚点)。"""
    for scene, data in load_all_scenes().items():
        checks = data["meta"].get("harness_checks")
        assert isinstance(checks, list) and checks, (
            f"{scene}: meta.harness_checks 非空(至少一条注入/覆盖校验)"
        )


def test_duplicate_id_detected(tmp_path, monkeypatch):
    """id 重复被校验拦截(结构校验防御)。"""
    import yaml

    from private_agent.eval import scenes_loader

    bad = {
        "meta": {"scenario": "office", "skill_name": "office", "scene_name": "x"},
        "tasks": [
            {"id": "dup-1", "title": "a", "description": "d", "success_criteria": ["c"]},
            {"id": "dup-1", "title": "b", "description": "d", "success_criteria": ["c"]},
        ],
    }
    p = tmp_path / "scenarios-office.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    monkeypatch.setattr(scenes_loader, "SCENE_FILES", {"office": p})
    with pytest.raises(Exception):
        validate_scene("office")
