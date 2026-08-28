"""0.6.0 F1-4: EnvironmentProfile 加载与规划注入片段单测。

验收: 注入内容快照断言(默认合并/自定义覆盖/片段关键要素齐全)。
"""
import pytest

from private_agent.core.environment_profile import (
    DEFAULT_ENVIRONMENT,
    build_environment_fragment,
    load_environment_profile,
)


# ── load_environment_profile ────────────────────────────────────────────

def test_load_defaults_when_absent():
    """config 无 environment 节 → 完整默认档案。"""
    p = load_environment_profile({})
    assert p["network"]["pip_index"] == DEFAULT_ENVIRONMENT["network"]["pip_index"]
    assert "google.com" in p["network"]["blocked_domains"]
    assert p["hardware"]["mem_budget_mb"] == 500
    assert p["guard"] is True


def test_load_none_cfg():
    assert load_environment_profile(None)["guard"] is True


def test_load_partial_override():
    """部分覆盖: 覆盖键生效, 未覆盖键保默认(浅层标量)。"""
    cfg = {"environment": {"hardware": {"mem_budget_mb": 300}}}
    p = load_environment_profile(cfg)
    assert p["hardware"]["mem_budget_mb"] == 300
    assert "cold_start_note" in p["hardware"]           # 同级未覆盖键保留
    assert "google.com" in p["network"]["blocked_domains"]  # 其他段保留


def test_load_list_override_replaces():
    """列表整体覆盖(非合并)—— blocked_domains 显式配置即权威。"""
    cfg = {"environment": {"network": {"blocked_domains": ["example.com"]}}}
    p = load_environment_profile(cfg)
    assert p["network"]["blocked_domains"] == ["example.com"]


def test_load_guard_disable():
    p = load_environment_profile({"environment": {"guard": False}})
    assert p["guard"] is False


# ── build_environment_fragment(注入内容快照) ────────────────────────────

def test_fragment_contains_all_key_sections():
    """片段含: 镜像三源/不可达域名/可达域名/代理陷阱/内存预算/冷启动/预检守卫。"""
    frag = build_environment_fragment(None)
    assert "运行环境约束" in frag
    assert "mirrors.aliyun.com" in frag
    assert "registry.npmmirror.com" in frag
    assert "hf-mirror.com" in frag
    assert "google.com" in frag          # 不可达域名列出(规划时绕开)
    assert "禁止运行时反复尝试" in frag
    assert "NO_PROXY" in frag            # 代理陷阱
    assert "500MB" in frag               # 内存预算
    assert "16s" in frag                 # 冷启动
    assert "install_preflight" in frag   # 预检守卫


def test_fragment_reflects_custom_config():
    """自定义配置反映到片段(镜像/预算/禁用守卫)。"""
    cfg = {
        "environment": {
            "network": {
                "pip_index": "https://custommirror.internal/simple",
                "blocked_domains": ["blocked.example"],
            },
            "hardware": {"mem_budget_mb": 800},
            "guard": False,
        }
    }
    frag = build_environment_fragment(cfg)
    assert "custommirror.internal" in frag
    assert "blocked.example" in frag
    assert "800MB" in frag
    assert "install_preflight" not in frag  # guard=false 不注入守卫要求


def test_fragment_line_count_bounded():
    """片段限长(按需自省理念: 规划轮注入, 不应超过 ~15 行)。"""
    frag = build_environment_fragment(None)
    assert len(frag.splitlines()) <= 15
