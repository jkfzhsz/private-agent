"""B3.1 - HTTP 控制面 /health 返回 200。

Source: plan/m0-implementation step 3 (蓝图 §9.6 step3 + §2.3)
"""
from fastapi.testclient import TestClient

from private_agent.main import app


def test_health_returns_200():
    """GET /health 返回 HTTP 200。"""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_ok_status():
    """GET /health 返回 {"status":"ok"}。"""
    client = TestClient(app)
    response = client.get("/health")
    assert response.json() == {"status": "ok"}
