"""首页动态视频背景(V2)测试。

覆盖:
- 上传视频(data:video/mp4) → type=video + 文件落地
- GET wallpaper 返回 type=image|video(按扩展名)
- 图片/视频互斥(新上传替换旧背景)
- 视频超限(>50MB)拒绝; 非法 data URL 拒绝
- DELETE 清理背景
- files.py Content-Type: mp4 → video/mp4
"""
import base64
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from private_agent.api import admin, files as files_mod
from private_agent.api.admin import router
from private_agent.api.files import router as files_router

# 最小合法 mp4 头(真实 mp4 文件头, 后端只按扩展名/base64 处理, 不校验容器)
TINY_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64


@pytest.fixture
def client(tmp_path, monkeypatch):
    """ASGI 客户端 + outputs 目录指向临时目录(不污染真实 outputs)。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    app.include_router(files_router)
    monkeypatch.setattr(admin, "_get_outputs_dir", lambda: tmp_path)
    monkeypatch.setattr(files_mod, "_get_outputs_dir", lambda: tmp_path)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _video_data_url() -> str:
    return "data:video/mp4;base64," + base64.b64encode(TINY_MP4).decode()


def _image_data_url() -> str:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    return "data:image/png;base64," + base64.b64encode(png).decode()


@pytest.mark.asyncio
async def test_upload_video_returns_type_video(client, tmp_path):
    """上传视频 → wallpaper.mp4 + type=video。"""
    resp = await client.post(
        "/admin/wallpaper", json={"data_url": _video_data_url()}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "video"
    assert data["wallpaper"].endswith("wallpaper.mp4")
    assert (tmp_path / "wallpaper.mp4").exists()


@pytest.mark.asyncio
async def test_get_wallpaper_returns_type(client, tmp_path):
    """GET 按扩展名返回 type。"""
    # 无背景 → image 默认
    resp = await client.get("/admin/wallpaper")
    assert resp.json()["wallpaper"] is None
    assert resp.json()["type"] == "image"
    # 视频背景 → video
    await client.post("/admin/wallpaper", json={"data_url": _video_data_url()})
    resp = await client.get("/admin/wallpaper")
    assert resp.json()["type"] == "video"
    assert "wallpaper.mp4" in resp.json()["wallpaper"]


@pytest.mark.asyncio
async def test_upload_image_replaces_video(client, tmp_path):
    """新上传图片替换视频背景(互斥)。"""
    await client.post("/admin/wallpaper", json={"data_url": _video_data_url()})
    assert (tmp_path / "wallpaper.mp4").exists()
    resp = await client.post("/admin/wallpaper", json={"data_url": _image_data_url()})
    assert resp.json()["type"] == "image"
    assert not (tmp_path / "wallpaper.mp4").exists()
    assert (tmp_path / "wallpaper.png").exists()
    resp = await client.get("/admin/wallpaper")
    assert resp.json()["type"] == "image"


@pytest.mark.asyncio
async def test_upload_video_too_large_rejected(client, tmp_path):
    """视频超过 50MB 拒绝。"""
    big = base64.b64encode(b"\x00" * (50 * 1024 * 1024 + 1)).decode()
    resp = await client.post(
        "/admin/wallpaper", json={"data_url": "data:video/mp4;base64," + big}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "image_too_large"


@pytest.mark.asyncio
async def test_upload_invalid_data_url_rejected(client):
    """非法 data URL(非图片/视频)拒绝。"""
    resp = await client.post(
        "/admin/wallpaper",
        json={"data_url": "data:application/pdf;base64,AAAA"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_image"


@pytest.mark.asyncio
async def test_delete_wallpaper_removes_video(client, tmp_path):
    """DELETE 移除视频背景。"""
    await client.post("/admin/wallpaper", json={"data_url": _video_data_url()})
    assert (tmp_path / "wallpaper.mp4").exists()
    resp = await client.delete("/admin/wallpaper")
    assert resp.status_code == 200
    assert not (tmp_path / "wallpaper.mp4").exists()
    resp = await client.get("/admin/wallpaper")
    assert resp.json()["wallpaper"] is None


@pytest.mark.asyncio
async def test_outputs_file_serves_mp4_content_type(client, tmp_path):
    """静态服务: mp4 返回 video/mp4(浏览器 <video> 可播放)。"""
    (tmp_path / "wallpaper.mp4").write_bytes(TINY_MP4)
    resp = await client.get("/files/outputs/wallpaper.mp4")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
