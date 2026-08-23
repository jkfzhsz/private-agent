"""2026-08-12 Phase 2: 多技能注入 —— _get_frozen_tools 工具白名单并集测试。

主技能 + 附加技能的工具白名单应取并集; 只有附加技能时仅附加技能白名单。
用 mock conn + monkeypatch SkillLoader 隔离, 专注合并逻辑。
"""
import asyncio
import os

import pytest

from private_agent import main


class _FakeSkill:
    """最小 Skill 对象(manifest.dependencies.tools 白名单)。"""

    class _Manifest:
        class _Deps:
            def __init__(self, names: list[str]):
                self.tools = [type("T", (), {"name": n, "enabled": True})() for n in names]

        def __init__(self, names: list[str]):
            self.dependencies = self._Deps(names)

    def __init__(self, names: list[str]):
        self.manifest = self._Manifest(names)


class _FakeLoader:
    """mock SkillLoader: 按技能名返回白名单。"""

    def __init__(self, whitelists: dict[str, list[str]]):
        self._whitelists = whitelists

    async def load(self, name, conn):
        return _FakeSkill(self._whitelists.get(name, []))


class _FakeRegistry:
    """mock ToolRegistry: list_tools_for_session 原样返回白名单。"""

    def list_tools(self):
        return ["tool_a", "tool_b", "tool_c"]

    def list_tools_for_session(self, whitelist):
        return sorted(whitelist or [])


def _run_get_frozen_tools(monkeypatch, cfg, locked_skill, supp_skills):
    """组装 mock 并调用 _get_frozen_tools。"""
    class _Conn:
        def __init__(self, locked, supp):
            self._locked = locked
            self._supp = supp

        async def fetchval(self, q, *a):
            return self._locked

        async def fetch(self, q, *a):
            return [{"skill_name": s} for s in self._supp]

    conn = _Conn(locked_skill, supp_skills)

    # _get_frozen_tools 内部是函数级 import → patch 源模块
    import private_agent.skills.loader as sk_loader
    import private_agent.tools.registry as tool_reg
    import private_agent.tools.builtins as tool_builtins

    monkeypatch.setattr(
        sk_loader, "SkillLoader",
        type("SL", (), {"from_cfg": lambda cfg: _FakeLoader({
            "office": ["tool_a", "tool_b"],
            "data_analysis": ["tool_b", "tool_c"],
            "solo": ["tool_c"],
        })})
    )
    monkeypatch.setattr(tool_reg, "ToolRegistry", lambda: _FakeRegistry())
    monkeypatch.setattr(tool_builtins, "register_all_builtins", lambda reg: None)

    async def _run():
        return await main._get_frozen_tools(cfg, 1, conn)

    return asyncio.run(_run())


def test_merge_main_and_supplementary(monkeypatch):
    """主技能(office) + 附加技能(data_analysis) → 白名单并集 + 基础记忆工具豁免。"""
    tools = _run_get_frozen_tools(monkeypatch, {}, "office", ["data_analysis"])
    assert set(tools) >= {"tool_a", "tool_b", "tool_c"}
    assert "memory_save" in tools and "memory_search" in tools


def test_supplementary_only(monkeypatch):
    """无主技能 + 附加技能(solo) → 仅附加技能白名单 + 基础记忆工具豁免。"""
    tools = _run_get_frozen_tools(monkeypatch, {}, None, ["solo"])
    assert set(tools) >= {"tool_c"}
    assert "memory_save" in tools and "memory_search" in tools


def test_no_skill_all_tools(monkeypatch):
    """无主技能 + 无附加 → 全部内置工具。"""
    tools = _run_get_frozen_tools(monkeypatch, {}, None, [])
    assert tools == ["tool_a", "tool_b", "tool_c"]


def test_main_only_unchanged(monkeypatch):
    """仅主技能(office) → 主技能白名单(不因附加机制变化) + 基础记忆工具豁免。"""
    tools = _run_get_frozen_tools(monkeypatch, {}, "office", [])
    assert set(tools) >= {"tool_a", "tool_b"}
    assert "memory_save" in tools and "memory_search" in tools


def test_memory_save_exempt_from_whitelist(monkeypatch):
    """2026-08-13 修复B: memory_save/memory_search 即使不在 skill 白名单也始终保留。"""
    # 用一个白名单里完全没有记忆工具的技能(office 只有 tool_a/tool_b)
    tools = _run_get_frozen_tools(monkeypatch, {}, "office", [])
    assert "memory_save" in tools, "memory_save 应始终可用(用户反馈'工具列表没有 memory_save')"
    assert "memory_search" in tools
