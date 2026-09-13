"""阶段二批次 1 - admin 鉴权测试(审查 A.3.10/B.2.1)。

覆盖:
- 无 token / 错误 token → 401 + WWW-Authenticate
- 正确 token → 200 全功能
- /health、/ 不鉴权(健康检查豁免)
- CORS 白名单: 白名单内 origin 返回 allow-origin 头; 白名单外不返回
- WS /ws 不鉴权(聊天主链路豁免)
"""
import pytest
from fastapi.testclient import TestClient

from private_agent.main import app

# 与 conftest.py 的 autouse 注入一致(测试专用 token)
TEST_ADMIN_TOKEN = "test-admin-token"

AUTH_HEADERS = {"X-Admin-Token": TEST_ADMIN_TOKEN}


@pytest.fixture
def client():
    """每个用例独立的 TestClient(2026-09-13 收窄作用域)。

    原先为 ``scope="module"``: 15 个用例共用一个 TestClient(app) 及其 anyio
    portal —— portal 一旦在 ``__enter__``(app lifespan 握手)阶段死亡, **整组
    15 例同时全败**, 报 ``RuntimeError: This portal is not running``。该失败
    在单文件运行时不可复现, 曾以"仅全量出现的假回归"形式误导排查(根因是沙箱
    删除阈值掐断 pytest 临时目录清理, 见 tests/conftest.py 同类记录)。

    改为 function 作用域后, 单点故障的爆炸半径从 15 例降到 1 例; 代价是每个
    用例各跑一次 lifespan(DB 迁移幂等 + KB 一致性校验, 实测单次约 0.1s 量级)。
    """
    with TestClient(app) as c:
        yield c


# ── 401 系列 ──────────────────────────────────────────────────────────────────


def test_admin_no_token_returns_401(client):
    resp = client.get("/admin/skills")
    assert resp.status_code == 401


def test_admin_wrong_token_returns_401(client):
    resp = client.get("/admin/skills", headers={"X-Admin-Token": "wrong-token"})
    assert resp.status_code == 401


def test_admin_missing_header_returns_401(client):
    resp = client.get("/admin/disk-status")
    assert resp.status_code == 401


def test_401_has_www_authenticate(client):
    resp = client.get("/admin/skills")
    assert resp.headers.get("www-authenticate") == "Bearer"


def test_eval_router_requires_auth(client):
    resp = client.get("/admin/eval/review-queue")
    assert resp.status_code == 401


def test_files_router_requires_auth(client):
    # 2026-08-04 豁免: files.router 不挂 require_admin(壁纸/预览图 <img>/<video>
    # src 直连不带 X-Admin-Token, 整体鉴权导致壁纸 401 永远加载失败)。
    # 无 token 也走业务逻辑 → 文件不存在 → 404(而非 401)。
    resp = client.get("/files/outputs/nonexistent.txt")
    assert resp.status_code == 404


# ── 正确 token ────────────────────────────────────────────────────────────────


def test_admin_with_token_passes_auth(client):
    resp = client.get("/admin/skills", headers=AUTH_HEADERS)
    # 鉴权已通过(非 401); 业务 200/500 依赖 DB 环境, 不在此断言
    assert resp.status_code != 401


def test_eval_with_token_not_401(client):
    resp = client.get("/admin/eval/review-queue", headers=AUTH_HEADERS)
    # 可能因 DB 未连接返回其他错误, 但绝不应该是 401(鉴权已通过)
    assert resp.status_code != 401


# ── 豁免端点 ──────────────────────────────────────────────────────────────────


def test_health_exempt_from_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_exempt_from_auth(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_ws_exempt_from_auth(client):
    """WS /ws 聊天链路不鉴权(本机 Electron 独占端口通信)。"""
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


# ── CORS 白名单 ───────────────────────────────────────────────────────────────


def test_cors_allowed_origin_returns_header(client):
    resp = client.get(
        "/admin/skills",
        headers={**AUTH_HEADERS, "Origin": "http://localhost:5173"},
    )
    assert resp.status_code != 401
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_disallowed_origin_no_header(client):
    """白名单外 origin(恶意网页)不返回 CORS 头 → 浏览器拦截读取响应。"""
    resp = client.get(
        "/admin/skills",
        headers={**AUTH_HEADERS, "Origin": "http://evil.example.com"},
    )
    assert resp.status_code != 401
    assert "access-control-allow-origin" not in resp.headers


def test_cors_dev_wildcard_any_port(client, monkeypatch):
    """PA_ENV=dev 时 localhost/127.0.0.1 任意端口放行(vite 端口占用切换)。"""
    monkeypatch.setenv("PA_ENV", "dev")
    resp = client.get(
        "/admin/skills",
        headers={**AUTH_HEADERS, "Origin": "http://127.0.0.1:5199"},
    )
    assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:5199"


def test_cors_credentials_not_allowed(client):
    resp = client.get(
        "/admin/skills",
        headers={**AUTH_HEADERS, "Origin": "http://localhost:5173"},
    )
    assert resp.headers.get("access-control-allow-credentials") is None
