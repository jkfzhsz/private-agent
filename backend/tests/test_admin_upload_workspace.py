"""P2(2026-08-15): 上传文件按会话工作区落盘测试。

画地为牢一致性: 会话选定工作区后, POST /admin/files/upload 带 session_id
应落 {会话工作区}/uploads/, 否则 file_read 路径校验(base_dir=会话 workspace)
会拦截(Path traversal)。

覆盖:
- 无 workspace 会话 → 回退全局 uploads(monkeypatch _get_outputs_dir)
- 有 workspace 会话 → 落 {workspace}/uploads/ 且返回路径正确
- 无效 session_id → 回退全局(不报错)
"""
import base64
import os
from pathlib import Path

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from private_agent.api import admin
from private_agent.api.admin import router
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """ASGI 客户端 + db.connect 指向测试库 + 固定全局 outputs 目录。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)
    # 固定全局 workspace_root 派生目录(避免测试写入真实 backend)
    global_ws = tmp_path / "global-ws"
    global_ws.mkdir(exist_ok=True)

    def _fake_outputs_dir() -> Path:
        return global_ws / "outputs"

    monkeypatch.setattr(admin, "_get_outputs_dir", _fake_outputs_dir)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def session_id(client):
    """建 schema + 一个测试会话。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
        sid = await conn.fetchval(
            "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
        )
        return sid
    finally:
        await conn.close()


def _payload(filename: str, session_id: int | None = None) -> dict:
    return {
        "filename": filename,
        "content_base64": base64.b64encode(b"hello-p2").decode(),
        "session_id": session_id,
    }


@pytest.mark.asyncio
async def test_upload_no_workspace_falls_back_global(client, session_id, tmp_path):
    """会话无 workspace → 落全局 uploads(monkeypatch 的 global-ws/uploads)。"""
    resp = await client.post("/admin/files/upload", json=_payload("no-ws.txt", session_id))
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "no-ws.txt"
    assert "global-ws" in data["path"]
    assert (tmp_path / "global-ws" / "outputs" / ".." / "uploads" / "no-ws.txt").resolve().exists()


@pytest.mark.asyncio
async def test_upload_with_workspace_lands_in_session_uploads(client, session_id, tmp_path):
    """会话选定工作区 → 文件落 {workspace}/uploads/, 返回路径含工作区。"""
    ws = tmp_path / "ws-qinghe"
    ws.mkdir(exist_ok=True)
    await client.put(
        f"/admin/sessions/{session_id}/workspace",
        json={"workspace": str(ws)},
    )
    resp = await client.post(
        "/admin/files/upload",
        json=_payload("美的集团建仓方案.md", session_id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "美的集团建仓方案.md"
    assert str(ws) in data["path"]
    target = ws / "uploads" / "美的集团建仓方案.md"
    assert target.exists(), f"expected file at {target}, got {data['path']}"
    assert target.read_bytes() == b"hello-p2"


@pytest.mark.asyncio
async def test_upload_invalid_session_falls_back_global(client, tmp_path):
    """无效 session_id(不存在) → 回退全局, 不报错。"""
    resp = await client.post("/admin/files/upload", json=_payload("ghost.txt", 999999))
    assert resp.status_code == 200
    data = resp.json()
    assert "global-ws" in data["path"]


@pytest.mark.asyncio
async def test_upload_without_session_id_backward_compatible(client, tmp_path):
    """不带 session_id(旧调用) → 回退全局, 兼容 FilePanel 工作区面板上传。"""
    resp = await client.post(
        "/admin/files/upload",
        json={"filename": "panel.txt", "content_base64": base64.b64encode(b"x").decode()},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "global-ws" in data["path"]
