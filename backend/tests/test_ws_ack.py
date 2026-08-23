"""M1 Phase 4 Behavior 4-2 - WS ack 消息处理 (AC-6)。

Source: plan/m1-react-loop Phase 4 (蓝图 §2.3 line 447 + §9.4 AC-6)

客户端发 {type:"ack", session_id, turn}:
- 服务端调用 ws_offset.handle_ack 回写 config_runtime ws_offset:{session_id}=turn
- 返回 {type:"ack_confirm", session_id, turn}
- session_id 缺失/非整数 → {type:"error", message:"..."}
- DB 失败 → {type:"error", message:"ack_failed"},不断开连接
"""
import asyncio
import os

import asyncpg
from fastapi.testclient import TestClient

from private_agent.main import app
from private_agent.storage import db, migrations, ws_offset

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
    """重建 schema + 跑 migrate_all。"""
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
        finally:
            await conn.close()

    asyncio.run(_run())


def _patch_db_connect(monkeypatch) -> None:
    """让 main 中使用的 db.connect 返回指向 TEST_DSN 的真实连接。"""
    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


# ──────────────────────────────────────────────────────────────────────────────
# ack 成功:回写 config_runtime + 返回 ack_confirm
# ──────────────────────────────────────────────────────────────────────────────


def test_ack_writes_config_runtime_and_returns_ack_confirm(monkeypatch):
    """ack 消息触发 handle_ack 回写 config_runtime,并返回 ack_confirm。"""
    _setup_schema()
    _patch_db_connect(monkeypatch)

    # 预创建 session(config_runtime 无 FK,但保持真实场景)
    async def _seed() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await conn.fetchval(
                "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
                "ack-test", "mock-glm",
            )
        finally:
            await conn.close()

    session_id = asyncio.run(_seed())

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ack", "session_id": session_id, "turn": 5})
        msg = ws.receive_json()

    assert msg["type"] == "ack_confirm"
    assert msg["session_id"] == session_id
    assert msg["turn"] == 5

    # 验证 config_runtime 已回写
    async def _verify() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await ws_offset.get_ws_offset(conn, session_id=session_id)
        finally:
            await conn.close()

    assert asyncio.run(_verify()) == 5


# ──────────────────────────────────────────────────────────────────────────────
# ack 参数校验:session_id 缺失/非整数 → error
# ──────────────────────────────────────────────────────────────────────────────


def test_ack_missing_session_id_returns_error(monkeypatch):
    """ack 消息缺 session_id → 返回 error,不断开连接。"""
    _setup_schema()
    _patch_db_connect(monkeypatch)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ack", "turn": 5})
        msg = ws.receive_json()
        # 连接仍可用
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
    assert msg["type"] == "error"
    assert "session_id" in msg["message"]
    assert pong == {"type": "pong"}


def test_ack_non_int_session_id_returns_error(monkeypatch):
    """ack 消息 session_id 非整数 → 返回 error。"""
    _setup_schema()
    _patch_db_connect(monkeypatch)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ack", "session_id": "abc", "turn": 5})
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "session_id" in msg["message"]


def test_ack_non_int_turn_returns_error(monkeypatch):
    """ack 消息 turn 非整数 → 返回 error。"""
    _setup_schema()
    _patch_db_connect(monkeypatch)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ack", "session_id": 1, "turn": "abc"})
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "turn" in msg["message"]


def test_ack_missing_turn_returns_error(monkeypatch):
    """ack 消息缺 turn → 返回 error。"""
    _setup_schema()
    _patch_db_connect(monkeypatch)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ack", "session_id": 1})
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "turn" in msg["message"]


# ──────────────────────────────────────────────────────────────────────────────
# ack DB 失败 → error,不断开连接
# ──────────────────────────────────────────────────────────────────────────────


def test_ack_db_failure_returns_error_not_disconnect(monkeypatch):
    """ack 时 DB 连接失败 → 返回 {type:error, message:ack_failed},不断开连接。"""
    _setup_schema()

    async def _fail_connect(*args, **kwargs):
        raise ConnectionError("simulated PG down")

    monkeypatch.setattr(db, "connect", _fail_connect)

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "ack", "session_id": 1, "turn": 5})
        msg = ws.receive_json()
        # DB 失败后连接仍可用
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
    assert msg["type"] == "error"
    assert msg["message"] == "ack_failed"
    assert pong == {"type": "pong"}
