"""B3.2a - WS 数据面可连接(最小 /ws 端点 ping/pong)。

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
