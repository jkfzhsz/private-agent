"""M1 Phase 1 step 5 - GET /admin/disk-status HTTP 端点。

Source: plan/m1-react-loop step 5 (蓝图 §2.10 第 6 条 + §9.4 Done Criteria 4 / AC-4)

GET /admin/disk-status 返回 {"level":"none|yellow|orange|red", "message":"...", "size_bytes":N}。
异常时返回 503 + {"error": "disk_status_unavailable"}。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from private_agent.api import admin


def _make_app() -> FastAPI:
    """构造包含 admin router 的 FastAPI app(测试用)。"""
    app = FastAPI()
    app.include_router(admin.router)
    return app


class _FakeAcquire:
    async def __aenter__(self):
        return object()  # mock conn(get_disk_status 被 mock,不真正使用)

    async def __aexit__(self, *args):
        return None


class _FakePool:
    def acquire(self):
        return _FakeAcquire()


def _patch_deps(monkeypatch, level: str, message: str, size_bytes: int) -> None:
    """替换 get_disk_status + db.get_pool,避免真实 DB 查询。"""
    async def _fake_get_disk_status(conn, cfg=None):
        return {"level": level, "message": message, "size_bytes": size_bytes}

    async def _fake_get_pool():
        return _FakePool()

    monkeypatch.setattr(admin, "get_disk_status", _fake_get_disk_status)
    monkeypatch.setattr(admin.db, "get_pool", _fake_get_pool)


def test_get_disk_status_returns_200_with_fields(monkeypatch):
    """GET /admin/disk-status 返回 200 + {level, message, size_bytes}。"""
    _patch_deps(
        monkeypatch,
        level="yellow",
        message="存储空间即将不足,建议清理",
        size_bytes=int(1.6 * 1024 ** 3),
    )

    client = TestClient(_make_app())
    resp = client.get("/admin/disk-status")
    assert resp.status_code == 200
    body = resp.json()
    required = {"level", "message", "size_bytes"}
    assert required.issubset(body.keys()), f"缺少字段: {required - body.keys()}"
    assert body["level"] == "yellow"
    assert body["size_bytes"] == int(1.6 * 1024 ** 3)


def test_get_disk_status_returns_none_when_small(monkeypatch):
    """disk 较小时 level='none'(mock get_disk_status 返回 none)。"""
    _patch_deps(monkeypatch, level="none", message="", size_bytes=100)

    client = TestClient(_make_app())
    resp = client.get("/admin/disk-status")
    assert resp.status_code == 200
    assert resp.json()["level"] == "none"
    assert resp.json()["message"] == ""


def test_get_disk_status_returns_503_on_exception(monkeypatch):
    """get_disk_status 抛异常时返回 503 + {"error": "disk_status_unavailable"}。"""
    async def _raise(conn, cfg=None):
        raise RuntimeError("db down")

    async def _fake_get_pool():
        return _FakePool()

    monkeypatch.setattr(admin, "get_disk_status", _raise)
    monkeypatch.setattr(admin.db, "get_pool", _fake_get_pool)

    client = TestClient(_make_app())
    resp = client.get("/admin/disk-status")
    assert resp.status_code == 503
    assert resp.json() == {"error": "disk_status_unavailable"}
