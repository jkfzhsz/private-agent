"""CORS 配置:浏览器模式(vite dev 5173)跨域访问 8765 必须放行。"""
from fastapi.testclient import TestClient

from private_agent.main import app


def test_cors_headers_on_admin_skills():
    """GET /admin/skills 携带跨域 Origin 时返回 access-control-allow-origin 头。"""
    client = TestClient(app)
    resp = client.get(
        "/admin/skills",
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"
