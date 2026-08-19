"""V1.1-3.7 文件管理闭环测试。

覆盖:
- GET /admin/files/tree: 目录树(排除噪音目录)
- GET /admin/files/content: 文本直出 / 二进制提示
- GET /admin/files/download: 文件下载
- POST /admin/files/mkdir / PUT rename / DELETE delete
- 越界路径 → 400; 非空目录删除 → 400
"""
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
def client(monkeypatch, tmp_path):
    """ASGI 客户端 + db.connect 指向测试库 + workspace_root 指向 tmp。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    import private_agent.config.loader as cfg_loader

    original_load = cfg_loader.load_config
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)

    def _fake_load_config():
        cfg = original_load()
        return {**cfg, "system": {**cfg.get("system", {}), "workspace_root": str(ws)}}

    monkeypatch.setattr(cfg_loader, "load_config", _fake_load_config)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), ws


@pytest.mark.asyncio
async def test_tree_and_content(client, schema):
    """tree 返回结构; content 读文本; 排除噪音目录。"""
    client, ws = client
    (ws / "docs").mkdir()
    (ws / "docs" / "a.md").write_text("# 标题\n正文", encoding="utf-8")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "x").write_text("noise", encoding="utf-8")

    resp = await client.get("/admin/files/tree")
    assert resp.status_code == 200
    data = resp.json()
    names = [c["name"] for c in data["tree"]["children"]]
    assert "docs" in names
    assert "node_modules" not in names  # 排除噪音目录

    resp = await client.get("/admin/files/content", params={"path": "docs/a.md"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "text"
    assert "正文" in body["content"]


@pytest.mark.asyncio
async def test_content_binary(client, schema):
    """二进制文件 → type=binary。"""
    client, ws = client
    (ws / "pic.bin").write_bytes(b"\x00\x01\x02\xff\xfe")

    resp = await client.get("/admin/files/content", params={"path": "pic.bin"})
    assert resp.status_code == 200
    assert resp.json()["type"] == "binary"


@pytest.mark.asyncio
async def test_mkdir_rename_delete(client, schema):
    """新建/重命名/删除闭环。"""
    client, ws = client
    resp = await client.post("/admin/files/mkdir", json={"path": "proj/src"})
    assert resp.status_code == 200
    assert (ws / "proj" / "src").is_dir()

    (ws / "proj" / "src" / "f.txt").write_text("x", encoding="utf-8")
    resp = await client.put("/admin/files/rename", json={"path": "proj/src/f.txt", "to_path": "proj/f2.txt"})
    assert resp.status_code == 200
    assert (ws / "proj" / "f2.txt").exists()
    assert not (ws / "proj" / "src" / "f.txt").exists()

    resp = await client.delete("/admin/files/delete", params={"path": "proj/f2.txt"})
    assert resp.status_code == 200
    assert not (ws / "proj" / "f2.txt").exists()

    # 删除非空目录 → 400
    resp = await client.delete("/admin/files/delete", params={"path": "proj"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_path_traversal_blocked(client, schema):
    """越界路径 → 400。"""
    client, _ = client
    resp = await client.get("/admin/files/content", params={"path": "../secret.txt"})
    assert resp.status_code == 400

    resp = await client.post("/admin/files/mkdir", json={"path": "../../etc/hack"})
    assert resp.status_code == 400

    resp = await client.get("/admin/files/content", params={"path": "C:/Windows/win.ini"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_download_file(client, schema):
    """下载文件返回内容。"""
    client, ws = client
    (ws / "d.txt").write_text("download-me", encoding="utf-8")

    resp = await client.get("/admin/files/download", params={"path": "d.txt"})
    assert resp.status_code == 200
    assert resp.content == b"download-me"


# ══════════════════════════════════════════════════════════════════════════
# V1.3-7.4 高级文件能力: 解压(防穿越) + 批量打包下载
# ══════════════════════════════════════════════════════════════════════════


def _make_zip(entries: dict[str, bytes]) -> bytes:
    """构造 zip 字节流(entries: 相对路径 → 内容)。"""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_extract_zip(client, schema):
    """解压 zip(含子目录) → 文件落盘到目标目录。"""
    client, ws = client
    (ws / "archives").mkdir()
    (ws / "archives" / "pkg.zip").write_bytes(
        _make_zip({
            "hello.txt": b"hello world",
            "sub/nested.md": b"# nested",
        })
    )

    resp = await client.post("/admin/files/extract", json={
        "archive": "archives/pkg.zip",
        "to_dir": "extracted",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["extracted"] == 2
    assert (ws / "extracted" / "hello.txt").read_text() == "hello world"
    assert (ws / "extracted" / "sub" / "nested.md").read_text() == "# nested"


@pytest.mark.asyncio
async def test_extract_zip_traversal_blocked(client, schema):
    """路径穿越条目(../) → 拒绝, 不写出工作区。"""
    client, ws = client
    (ws / "p.zip").write_bytes(
        _make_zip({
            "../evil.txt": b"pwned",
            "ok.txt": b"fine",
        })
    )

    resp = await client.post("/admin/files/extract", json={"archive": "p.zip"})
    assert resp.status_code == 200
    assert resp.json()["extracted"] == 1  # 仅 ok.txt
    assert not (ws.parent / "evil.txt").exists()
    assert (ws / "ok.txt").exists()


@pytest.mark.asyncio
async def test_extract_unsupported(client, schema):
    """非 zip/tar → 400; 不存在 → 404。"""
    client, ws = client
    (ws / "x.rar").write_bytes(b"r")
    resp = await client.post("/admin/files/extract", json={"archive": "x.rar"})
    assert resp.status_code == 400

    resp = await client.post("/admin/files/extract", json={"archive": "ghost.zip"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_extract_tar_gz(client, schema):
    """tar.gz 解压。"""
    import io
    import tarfile

    client, ws = client
    # 正常构造 tar.gz
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"tar content"
        info = tarfile.TarInfo("tar-file.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    (ws / "t.tgz").write_bytes(buf.getvalue())

    resp = await client.post("/admin/files/extract", json={"archive": "t.tgz"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["extracted"] == 1
    assert (ws / "tar-file.txt").read_text() == "tar content"


@pytest.mark.asyncio
async def test_download_zip_batch(client, schema):
    """批量打包: paths 多文件 → zip 含全部; 目录递归。"""
    import io
    import zipfile

    client, ws = client
    (ws / "a.txt").write_text("A", encoding="utf-8")
    (ws / "docs").mkdir()
    (ws / "docs" / "b.md").write_text("B", encoding="utf-8")

    resp = await client.get(
        "/admin/files/download_zip",
        params={"paths": "a.txt,docs", "name": "exp"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        assert "a.txt" in names
        assert "docs/b.md" in names
        assert zf.read("a.txt") == b"A"


@pytest.mark.asyncio
async def test_download_zip_invalid(client, schema):
    """空 paths → 400; 全无效 → 400。"""
    client, ws = client
    resp = await client.get("/admin/files/download_zip", params={"paths": ""})
    assert resp.status_code == 400

    resp = await client.get("/admin/files/download_zip", params={"paths": "ghost1,ghost2"})
    assert resp.status_code == 400
