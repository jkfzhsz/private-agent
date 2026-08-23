"""M3 plan step 19 - POST /admin/sessions/{id}/activate 端点。

Source: plan/m3-skills-office step 19, spec AC-1/4/5
- 200 成功: {locked_version, frozen_hash}
- session 不存在时懒创建(与 WS user_message 一致)
- 409 锁定冲突 (SkillSwitchNotAllowedError)
- 400 校验失败 (SkillValidationError)
- 404 skill 不存在 (SkillNotFoundError)

用 mock SkillManager 隔离 DB/skill 文件系统,专注 HTTP 路径。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from private_agent.api import admin


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin.router)
    return app


class _FakeConn:
    """mock asyncpg.Connection:fetchrow 用于 session 存在检查,execute 记录懒创建。"""

    def __init__(self, session_exists: bool = True):
        self._session_exists = session_exists
        self.executed: list[tuple] = []

    async def fetchrow(self, query, *args):
        if "FROM sessions WHERE id" in query:
            return {"id": args[0]} if self._session_exists else None
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))

    async def close(self):
        pass


class _FakeSkillManager:
    """mock SkillManager,按 preset 抛异常或返回成功结果。"""

    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    async def activate_skill(self, skill_name, session_id, conn):
        if self._exc:
            raise self._exc
        return self._result


def _patch_deps(monkeypatch, session_exists=True, skill_mgr_result=None, skill_mgr_exc=None):
    """patch db.connect 返回 _FakeConn + SkillManager 工厂返回 _FakeSkillManager。"""
    fake_conn = _FakeConn(session_exists=session_exists)

    async def _fake_connect(cfg=None):
        return fake_conn

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    fake_mgr = _FakeSkillManager(result=skill_mgr_result, exc=skill_mgr_exc)
    monkeypatch.setattr(admin, "_build_skill_manager", lambda cfg: fake_mgr)
    return fake_conn


class TestActivateSkillEndpoint:
    """plan step 19: POST /admin/sessions/{id}/activate。"""

    def test_activate_success_returns_200(self, monkeypatch):
        """200 成功激活 → {locked_version, frozen_hash}。"""
        _patch_deps(
            monkeypatch,
            session_exists=True,
            skill_mgr_result={
                "locked_version": "1.0.0",
                "frozen_hash": "a" * 64,
                "filtered_tools": [],
                "system_prompt": "test",
            },
        )
        client = TestClient(_make_app())
        resp = client.post(
            "/admin/sessions/1/activate",
            json={"skill_name": "office"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["locked_version"] == "1.0.0"
        assert body["frozen_hash"] == "a" * 64

    def test_activate_lazily_creates_session(self, monkeypatch):
        """session 不存在时懒创建(与 WS user_message 一致),激活成功而非 404。"""
        fake_conn = _patch_deps(
            monkeypatch,
            session_exists=False,
            skill_mgr_result={
                "locked_version": "1.0.0",
                "frozen_hash": "b" * 64,
                "filtered_tools": [],
                "system_prompt": "test",
            },
        )
        client = TestClient(_make_app())
        resp = client.post(
            "/admin/sessions/999/activate",
            json={"skill_name": "office"},
        )
        assert resp.status_code == 200
        assert resp.json()["locked_version"] == "1.0.0"
        # 懒创建 INSERT 已执行
        inserts = [q for q, _ in fake_conn.executed if "INSERT INTO sessions" in q]
        assert len(inserts) == 1

    def test_activate_lock_conflict_returns_409(self, monkeypatch):
        """409 已锁定不同 skill → SkillSwitchNotAllowedError。"""
        from private_agent.skills.errors import SkillSwitchNotAllowedError
        _patch_deps(
            monkeypatch,
            session_exists=True,
            skill_mgr_exc=SkillSwitchNotAllowedError("locked to other"),
        )
        client = TestClient(_make_app())
        resp = client.post(
            "/admin/sessions/1/activate",
            json={"skill_name": "office"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "skill_switch_not_allowed"

    def test_activate_validation_error_returns_400(self, monkeypatch):
        """400 校验失败(工具不存在)→ SkillValidationError。"""
        from private_agent.skills.errors import SkillValidationError
        _patch_deps(
            monkeypatch,
            session_exists=True,
            skill_mgr_exc=SkillValidationError("bad tool"),
        )
        client = TestClient(_make_app())
        resp = client.post(
            "/admin/sessions/1/activate",
            json={"skill_name": "office"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "skill_validation_failed"

    def test_activate_skill_not_found_returns_404(self, monkeypatch):
        """404 skill 不存在 → SkillNotFoundError。"""
        from private_agent.skills.errors import SkillNotFoundError
        _patch_deps(
            monkeypatch,
            session_exists=True,
            skill_mgr_exc=SkillNotFoundError("office not found"),
        )
        client = TestClient(_make_app())
        resp = client.post(
            "/admin/sessions/1/activate",
            json={"skill_name": "nonexistent"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "skill_not_found"
