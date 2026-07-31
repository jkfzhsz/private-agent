"""M1 Phase 4 Behavior 4-1 - main.py 挂载 admin router。

Source: plan/m1-react-loop Phase 4 (蓝图 §2.10 第 6 条 + §9.4 AC-4)

main.app 应 include admin.router,使 GET /admin/disk-status 可通过 main.app 访问。
"""
from fastapi.testclient import TestClient

from private_agent.api import admin
from private_agent.main import app


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


def test_main_app_has_admin_disk_status_route(monkeypatch):
    """main.app 挂载 admin router 后,GET /admin/disk-status 返回 200(mock get_disk_status)。"""
    _patch_deps(monkeypatch, level="yellow", message="存储空间即将不足", size_bytes=1024)

    client = TestClient(app)
    resp = client.get("/admin/disk-status")
    assert resp.status_code == 200
    body = resp.json()
    required = {"level", "message", "size_bytes"}
    assert required.issubset(body.keys()), f"缺少字段: {required - body.keys()}"
    assert body["level"] == "yellow"
    assert body["size_bytes"] == 1024


def test_main_app_admin_disk_status_returns_503_on_exception(monkeypatch):
    """main.app 的 /admin/disk-status 异常时返回 503 + {"error": "disk_status_unavailable"}。"""
    async def _raise(conn, cfg=None):
        raise RuntimeError("db down")

    async def _fake_get_pool():
        return _FakePool()

    monkeypatch.setattr(admin, "get_disk_status", _raise)
    monkeypatch.setattr(admin.db, "get_pool", _fake_get_pool)

    client = TestClient(app)
    resp = client.get("/admin/disk-status")
    assert resp.status_code == 503
    assert resp.json() == {"error": "disk_status_unavailable"}
