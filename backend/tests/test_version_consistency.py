"""2026-09-07 S6(自检 B3): 版本号单一来源一致性校验。

背景(自检 P1-#7): 版本号三处漂移 —— config.yaml system.version=0.1.0、
frontend/package.json=0.5.0、实际 0.6.0。排障/打包时易误判。

裁决: config.yaml system.version 为单一版本源, 前端打包版本与之对齐。
本测试守护两处不再漂移(改版本只改 config.yaml, 同步前端后测试即绿)。
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_YAML = REPO_ROOT / "backend" / "config" / "config.yaml"
PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"
PACKAGE_LOCK = REPO_ROOT / "frontend" / "package-lock.json"


def _config_version() -> str:
    cfg = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8"))
    return str(cfg["system"]["version"])


def test_frontend_package_version_matches_config():
    """frontend/package.json version == config.yaml system.version。"""
    cfg_ver = _config_version()
    pkg = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    assert pkg["version"] == cfg_ver, (
        f"版本漂移: package.json={pkg['version']} vs "
        f"config.yaml system.version={cfg_ver} —— "
        f"config.yaml 为单一版本源, 请同步前端后重跑"
    )


def test_package_lock_root_version_matches_config():
    """package-lock.json 根版本(name/version 两处)与单一版本源一致。"""
    cfg_ver = _config_version()
    lock = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
    assert lock["version"] == cfg_ver
    root_pkg = lock.get("packages", {}).get("", {})
    assert root_pkg.get("version") == cfg_ver


def test_version_format_semver():
    """版本号为 semver 三段格式(防误写)。"""
    ver = _config_version()
    parts = ver.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), (
        f"system.version='{ver}' 非 semver 三段格式"
    )
