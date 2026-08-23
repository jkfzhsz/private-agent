"""B3.2a - WS 数据面可连接(最小 /ws 端点 ping/pong)+ replay 健壮性。

Source: plan/m0-implementation step 3 (蓝图 §9.6 step3 + §2.3)
"""
from fastapi.testclient import TestClient

from private_agent.main import app


def test_ws_can_connect_and_ping_pong():
    """WS /ws 端点可连接,ping 帧返回 pong(蓝图 §2.3 WS 数据面)。"""
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ping"})
        msg = ws.receive_json()
    assert msg == {"type": "pong"}


def test_ws_replay_invalid_session_id_returns_error():
    """replay 消息 session_id 非法(<=0)→ 返回 error,不断开连接。"""
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "replay", "session_id": 0, "last_turn": 0})
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "session_id" in msg["message"]


def test_ws_replay_negative_last_turn_returns_error():
    """replay 消息 last_turn<0 → 返回 error。"""
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "replay", "session_id": 1, "last_turn": -1})
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "last_turn" in msg["message"]


def test_ws_replay_missing_session_id_returns_error():
    """replay 消息缺 session_id → 返回 error。"""
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "replay", "last_turn": 0})
        msg = ws.receive_json()
    assert msg["type"] == "error"


def test_ws_replay_non_int_session_id_returns_error():
    """replay 消息 session_id 非整数 → 返回 error。"""
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "replay", "session_id": "abc", "last_turn": 0})
        msg = ws.receive_json()
    assert msg["type"] == "error"


def test_ws_replay_db_failure_returns_error_not_disconnect(monkeypatch):
    """DB 连接失败时返回 error 消息,不断开 WS(模拟 PG 不可用)。

    通过 monkeypatch 让 db.connect 抛异常,验证 WS 循环不中断。
    """
    from private_agent.storage import db

    async def _fail_connect(*args, **kwargs):
        raise ConnectionError("simulated PG down")

    monkeypatch.setattr(db, "connect", _fail_connect)
    # 确保 PA_DB_PASSWORD 未设置,触发 build_dsn 失败的兜底
    monkeypatch.delenv("PA_DB_PASSWORD", raising=False)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "replay", "session_id": 1, "last_turn": 0})
        msg = ws.receive_json()
        # DB 失败后连接仍可用,可继续 ping
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
    assert msg["type"] == "error"
    assert msg["message"] == "replay_failed"
    assert pong == {"type": "pong"}
