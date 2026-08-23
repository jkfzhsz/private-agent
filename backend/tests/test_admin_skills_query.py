"""M3 plan step 17-18 - GET /admin/skills 列表 + GET /admin/skills/{name} 详情。

Source: plan/m3-skills-office step 17-18
- GET /admin/skills → 200 [{name, version, description, enabled}]
- GET /admin/skills/{name} → 200 {manifest + system_prompt 前 500 字 + tools 白名单}
- skill 不存在 → 404

用 mock SkillLoader 隔离文件系统,专注 HTTP 路径。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from private_agent.api import admin


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin.router)
    return app


class _FakeToolDep:
    """mock ToolDependency。"""
    def __init__(self, name, safety_level_override="safe", enabled=True):
        self.name = name
        self.safety_level_override = safety_level_override
        self.enabled = enabled


class _FakeExamplesConfig:
    def __init__(self, enabled=True, max_examples=2):
        self.enabled = enabled
        self.max_examples = max_examples


class _FakeManifest:
    """mock SkillManifest。"""
    def __init__(self, name, version="1.0.0", description="", enabled=True,
                 tools=None, examples=None):
        self.name = name
        self.version = version
        self.description = description
        self.enabled = enabled
        self.scenario = name
        from types import SimpleNamespace
        self.dependencies = SimpleNamespace(tools=tools or [])
        self.examples = examples or _FakeExamplesConfig()
        self.max_frozen_token = 4000
        from types import SimpleNamespace
        self.permissions = SimpleNamespace()
        from types import SimpleNamespace
        self.knowledge_base = SimpleNamespace()


class _FakeSkill:
    """mock Skill。"""
    def __init__(self, manifest, system_prompt):
        self.manifest = manifest
        self.system_prompt = system_prompt


class _FakeSkillLoader:
    """mock SkillLoader,持有预设 skill 列表。"""

    def __init__(self, skills: dict[str, _FakeSkill] | None = None):
        self._skills = skills or {}

    async def load(self, name, conn):
        if name not in self._skills:
            from private_agent.skills.errors import SkillNotFoundError
            raise SkillNotFoundError(f"Skill '{name}' not found")
        return self._skills[name]

    async def list_all(self, conn):
        return list(self._skills.values())


def _patch_loader(monkeypatch, skills: dict[str, _FakeSkill]):
    """patch admin._build_skill_loader + db.connect 返回 mock。"""
    fake_loader = _FakeSkillLoader(skills=skills)
    monkeypatch.setattr(admin, "_build_skill_loader", lambda cfg: fake_loader)

    class _FakeConn:
        async def close(self):
            pass

    async def _fake_connect(cfg=None):
        return _FakeConn()

    monkeypatch.setattr(admin.db, "connect", _fake_connect)


def _make_office_skill():
    """构造测试用 office skill。"""
    manifest = _FakeManifest(
        name="office",
        version="1.0.0",
        description="办公场景助手",
        enabled=True,
        tools=[
            _FakeToolDep(name="calculator"),
            _FakeToolDep(name="datetime"),
            _FakeToolDep(name="http_request", enabled=False),
        ],
    )
    return _FakeSkill(
        manifest=manifest,
        system_prompt="你是办公助手,负责文档处理。" * 20,  # >500 字
    )


class TestListSkillsEndpoint:
    """plan step 17: GET /admin/skills。"""

    def test_list_skills_returns_200_with_summary(self, monkeypatch):
        """GET /admin/skills → 200 [{name, version, description, enabled}]。"""
        _patch_loader(monkeypatch, {"office": _make_office_skill()})
        client = TestClient(_make_app())
        resp = client.get("/admin/skills")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        item = body[0]
        assert item["name"] == "office"
        assert item["version"] == "1.0.0"
        assert item["description"] == "办公场景助手"
        assert item["enabled"] is True

    def test_list_skills_empty_returns_200_empty_list(self, monkeypatch):
        """无 skill → 200 []。"""
        _patch_loader(monkeypatch, {})
        client = TestClient(_make_app())
        resp = client.get("/admin/skills")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetSkillDetailEndpoint:
    """plan step 18: GET /admin/skills/{name}。"""

    def test_get_skill_detail_returns_200(self, monkeypatch):
        """GET /admin/skills/office → 200 {manifest + system_prompt 前 500 字 + tools 白名单}。"""
        _patch_loader(monkeypatch, {"office": _make_office_skill()})
        client = TestClient(_make_app())
        resp = client.get("/admin/skills/office")
        assert resp.status_code == 200
        body = resp.json()
        # manifest 字段
        assert body["name"] == "office"
        assert body["version"] == "1.0.0"
        assert body["description"] == "办公场景助手"
        # system_prompt 前 500 字
        assert "system_prompt_preview" in body
        assert len(body["system_prompt_preview"]) <= 500
        assert "你是办公助手" in body["system_prompt_preview"]
        # tools 白名单(全部工具,含 enabled=false)
        assert "tools" in body
        tool_names = [t["name"] for t in body["tools"]]
        assert "calculator" in tool_names
        assert "datetime" in tool_names
        assert "http_request" in tool_names

    def test_get_skill_not_found_returns_404(self, monkeypatch):
        """GET /admin/skills/nonexistent → 404。"""
        _patch_loader(monkeypatch, {"office": _make_office_skill()})
        client = TestClient(_make_app())
        resp = client.get("/admin/skills/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "skill_not_found"
