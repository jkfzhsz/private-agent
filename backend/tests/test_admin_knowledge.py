"""V1.2-6.4 基础 RAG 知识库测试。

覆盖:
- GET /admin/knowledge: 库列表(scenario 分组)
- POST /admin/knowledge/upload-file: 文件上传入库(文本)
- GET /admin/knowledge/{scenario}/documents: 文档列表
- DELETE /admin/knowledge/{scenario}: 软删库
"""
import base64
import os

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
async def schema():
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
    finally:
        await conn.close()


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


@pytest.mark.asyncio
async def test_kb_upload_and_list(client, schema):
    """文件上传入库 → 库列表可见 → 文档列表。"""
    resp = await client.post("/admin/knowledge/upload-file", json={
        "filename": "notes.md",
        "content_base64": _b64("# 标题\n这是一份知识库测试文档。\n" * 20),
        "scenario": "office",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["doc_id"] > 0
    assert data["chunks"] > 0

    resp = await client.get("/admin/knowledge")
    assert resp.status_code == 200
    kb = resp.json()
    assert kb["total_documents"] >= 1
    bases = {b["scenario"]: b for b in kb["bases"]}
    assert "office" in bases
    assert len(bases["office"]["documents"]) >= 1

    resp = await client.get("/admin/knowledge/office/documents")
    assert resp.status_code == 200
    docs = resp.json()
    assert any("notes.md" in d["source"] for d in docs)


@pytest.mark.asyncio
async def test_kb_upload_binary_rejected(client, schema):
    """二进制文件(非文本) → 400。"""
    resp = await client.post("/admin/knowledge/upload-file", json={
        "filename": "bin.dat",
        "content_base64": base64.b64encode(b"\x00\x01\x02\xff\xfe").decode("ascii"),
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_kb_delete_scenario(client, schema):
    """删除库 → 文档软删(列表不可见)。"""
    resp = await client.post("/admin/knowledge/upload-file", json={
        "filename": "del.md",
        "content_base64": _b64("待删除文档内容 " * 30),
        "scenario": "temp_kb",
    })
    assert resp.status_code == 200

    resp = await client.delete("/admin/knowledge/temp_kb")
    assert resp.status_code == 200
    assert resp.json()["deleted_documents"] >= 1

    resp = await client.get("/admin/knowledge")
    kb = resp.json()
    assert all(b["scenario"] != "temp_kb" for b in kb["bases"])


# ══════════════════════════════════════════════════════════════════════════
# V1.3-7.3 知识库专业升级: 切片配置 / 重索引 / 检索测试
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_kb_config_roundtrip(client, schema):
    """切片配置: GET 默认 → PUT 修改 → GET 反映。"""
    resp = await client.get("/admin/knowledge/config")
    assert resp.status_code == 200
    cfg = resp.json()
    assert "markdown" in cfg["chunking"]
    assert cfg["chunking"]["markdown"]["chunk_size"] >= 1

    resp = await client.put("/admin/knowledge/config", json={
        "chunking": {
            "markdown": {"chunk_size": 256, "chunk_overlap": 32},
            "plain": {"chunk_size": 200},
        }
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    resp = await client.get("/admin/knowledge/config")
    cfg = resp.json()
    assert cfg["chunking"]["markdown"]["chunk_size"] == 256
    assert cfg["chunking"]["markdown"]["chunk_overlap"] == 32
    assert cfg["chunking"]["plain"]["chunk_size"] == 200


@pytest.mark.asyncio
async def test_kb_search_test(client, schema):
    """检索测试: 上传文档后按关键词命中。"""
    await client.post("/admin/knowledge/upload-file", json={
        "filename": "search-test.md",
        "content_base64": _b64(
            "量子计算基础 这份文档介绍量子比特和叠加态原理。\n" * 25
        ),
        "scenario": "office",
    })

    resp = await client.post("/admin/knowledge/search_test", json={
        "query": "量子比特",
        "scenario": "office",
        "top_k": 3,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["results"]) > 0
    assert any("量子" in r["text"] for r in data["results"])


@pytest.mark.asyncio
async def test_kb_search_test_requires_query(client, schema):
    """空查询 → 400。"""
    resp = await client.post("/admin/knowledge/search_test", json={"query": "  "})
    assert resp.status_code == 400
    assert resp.json()["error"] == "query_required"


@pytest.mark.asyncio
async def test_kb_reindex(client, schema):
    """批量重索引: 已有文档 → 返回 documents/chunks; 空库 → 404。"""
    await client.post("/admin/knowledge/upload-file", json={
        "filename": "reindex.md",
        "content_base64": _b64("重索引测试内容 " * 40),
        "scenario": "office",
    })

    resp = await client.post("/admin/knowledge/reindex", json={"scenario": "office"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["documents"] >= 1
    assert data["chunks"] >= 1

    resp = await client.post("/admin/knowledge/reindex", json={"scenario": "ghost_kb"})
    assert resp.status_code == 404

    resp = await client.post("/admin/knowledge/reindex", json={"scenario": " "})
    assert resp.status_code == 400
