"""A-2 场景评测集加载与校验(设计文档 next-phase-plan-2026-08-15 §3.1-A3)。

backend/eval/scenes/scenarios-*.yaml 的加载/校验/汇总:
- load_all(): 加载全部 4 个场景任务清单
- validate(): 结构校验(meta 必填 + tasks 每项字段完整 + id 唯一)
- summary(): 输出任务数统计(供基线文档/测试断言)

用途: harness 提示词/工具描述迭代的回归护栏 —— 评测任务清单随 skill.yaml
harness 演进, 可版本化、可 diff、可自动核验。
"""
from __future__ import annotations

from pathlib import Path

import yaml

__all__ = [
    "SCENES_DIR",
    "SCENE_FILES",
    "load_scene_file",
    "load_all_scenes",
    "validate_scene",
    "validate_all",
    "scene_summary",
]

# backend/eval/scenes/ —— 从 private_agent/eval/scenes_loader.py 上溯:
# private_agent/eval → private_agent → backend → eval/scenes
SCENES_DIR = Path(__file__).resolve().parents[2] / "eval" / "scenes"
SCENE_FILES = {
    "office": SCENES_DIR / "scenarios-office.yaml",
    "data_analysis": SCENES_DIR / "scenarios-data_analysis.yaml",
    "frontend_design": SCENES_DIR / "scenarios-frontend_design.yaml",
    "monitor": SCENES_DIR / "scenarios-monitor.yaml",
}

# 每任务必填字段
_REQUIRED_TASK_FIELDS = ("id", "title", "description", "success_criteria")
# meta 必填字段
_REQUIRED_META_FIELDS = ("scenario", "skill_name", "scene_name")


class SceneValidationError(ValueError):
    """场景评测集结构非法异常。"""


def load_scene_file(scene: str) -> dict:
    """加载单个场景 yaml(文件缺失/解析失败抛异常)。"""
    path = SCENE_FILES.get(scene)
    if path is None:
        raise SceneValidationError(f"未知场景: {scene!r} (可选: {list(SCENE_FILES)})")
    if not path.exists():
        raise SceneValidationError(f"评测集文件缺失: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise SceneValidationError(f"{path.name}: 顶层必须是 dict")
    return data


def load_all_scenes() -> dict[str, dict]:
    """加载全部场景任务清单。"""
    return {scene: load_scene_file(scene) for scene in SCENE_FILES}


def validate_scene(scene: str) -> dict:
    """校验单个场景结构, 返回规范化数据(校验失败抛 SceneValidationError)。"""
    data = load_scene_file(scene)
    meta = data.get("meta")
    if not isinstance(meta, dict):
        raise SceneValidationError(f"{scene}: 缺 meta 段")
    for f in _REQUIRED_META_FIELDS:
        if not meta.get(f):
            raise SceneValidationError(f"{scene}: meta 缺必填字段 {f!r}")
    if meta.get("scenario") != scene:
        raise SceneValidationError(
            f"{scene}: meta.scenario={meta.get('scenario')!r} 与文件名不一致"
        )
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise SceneValidationError(f"{scene}: tasks 必须为非空数组")
    ids: set[str] = set()
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            raise SceneValidationError(f"{scene}: tasks[{i}] 必须是 dict")
        for f in _REQUIRED_TASK_FIELDS:
            if f not in t:
                raise SceneValidationError(f"{scene}: tasks[{i}] 缺必填字段 {f!r}")
        tid = t["id"]
        if tid in ids:
            raise SceneValidationError(f"{scene}: 任务 id 重复 {tid!r}")
        ids.add(tid)
        if not isinstance(t["success_criteria"], list) or not t["success_criteria"]:
            raise SceneValidationError(f"{scene}: tasks[{i}] success_criteria 非空数组")
    data["_task_count"] = len(tasks)
    return data


def validate_all() -> dict[str, dict]:
    """校验全部场景, 返回 {scene: validated_data}。"""
    return {scene: validate_scene(scene) for scene in SCENE_FILES}


def scene_summary() -> dict[str, int]:
    """任务数统计(供基线文档/测试)。"""
    return {
        scene: len(validate_scene(scene)["tasks"]) for scene in SCENE_FILES
    }
