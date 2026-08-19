"""阶段二批次 1 - CORS 白名单收窄: 浏览器模式(vite dev 5173)跨域访问 8765 放行,
白名单外 origin 不返回 CORS 头(浏览器拦截读取)。鉴权用例见 test_admin_auth.py。"""
from fastapi.testclient import TestClient

from private_agent.main import app

AUTH_HEADERS = {"X-Admin-Token": "test-admin-token"}


def test_cors_headers_on_admin_skills_allowed_origin():
    """GET /admin/skills 携带白名单内跨域 Origin(5173)时返回对应 allow-origin 头。"""
    client = TestClient(app)
    resp = client.get(
        "/admin/skills",
        headers={**AUTH_HEADERS, "Origin": "http://localhost:5173"},
    )
    assert resp.status_code != 401
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_no_header_for_disallowed_origin():
    """白名单外 origin 不返回 access-control-allow-origin 头(浏览器跨域读取被拦截)。"""
    client = TestClient(app)
    resp = client.get(
        "/admin/skills",
        headers={**AUTH_HEADERS, "Origin": "https://attacker.example.com"},
    )
    assert resp.status_code != 401
    assert "access-control-allow-origin" not in resp.headers
