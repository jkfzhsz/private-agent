"""壁纸按主题(light/dark)独立保存测试(2026-08-08)。

覆盖:
- 上传 theme=dark → wallpaper-dark.*, GET ?theme=dark 返回
- 两主题互不影响(light/dark 各自独立)
- 旧版单背景 wallpaper.* 迁移兜底为亮色主题
- style 按主题分开保存(wallpaper-style-{theme}.json)
- theme 缺省时保持旧版单背景行为(向后兼容)
"""
import base64
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from private_agent.api import admin, files as files_mod
from private_agent.api.admin import router
from private_agent.api.files import router as files_router

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
async def test_upload_theme_dark_isolated(client, tmp_path):
    """上传 theme=dark → wallpaper-dark.png, 不影响亮色。"""
    resp = await client.post(
        "/admin/wallpaper",
        json={"data_url": _image_data_url(), "theme": "dark"},
    )
    assert resp.status_code == 200
    assert resp.json()["wallpaper"].endswith("wallpaper-dark.png")
    assert (tmp_path / "wallpaper-dark.png").exists()
    # 无旧版单背景时, light 主题没有背景
    resp = await client.get("/admin/wallpaper", params={"theme": "light"})
    assert resp.json()["wallpaper"] is None
    # dark 主题有背景
    resp = await client.get("/admin/wallpaper", params={"theme": "dark"})
    assert resp.json()["wallpaper"].endswith("wallpaper-dark.png")


@pytest.mark.asyncio
async def test_light_dark_independent(client, tmp_path):
    """两主题各自独立: 换 light 背景不影响 dark。"""
    await client.post(
        "/admin/wallpaper", json={"data_url": _image_data_url(), "theme": "dark"}
    )
    await client.post(
        "/admin/wallpaper", json={"data_url": _video_data_url(), "theme": "light"}
    )
    assert (tmp_path / "wallpaper-dark.png").exists()
    assert (tmp_path / "wallpaper-light.mp4").exists()
    resp = await client.get("/admin/wallpaper", params={"theme": "light"})
    assert resp.json()["type"] == "video"
    resp = await client.get("/admin/wallpaper", params={"theme": "dark"})
    assert resp.json()["type"] == "image"


@pytest.mark.asyncio
async def test_legacy_single_wallpaper_falls_back_to_light(client, tmp_path):
    """旧版单背景 wallpaper.png 迁移为亮色主题(兜底)。"""
    (tmp_path / "wallpaper.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
    resp = await client.get("/admin/wallpaper", params={"theme": "light"})
    assert resp.json()["wallpaper"].endswith("wallpaper.png")
    # 暗色主题不兜底旧文件
    resp = await client.get("/admin/wallpaper", params={"theme": "dark"})
    assert resp.json()["wallpaper"] is None


@pytest.mark.asyncio
async def test_style_saved_per_theme(client, tmp_path):
    """style 按主题分开保存。"""
    resp = await client.put(
        "/admin/wallpaper/style",
        json={"position_x": 30, "position_y": 40, "scale": 150, "rotate": 90, "theme": "dark"},
    )
    assert resp.status_code == 200
    assert (tmp_path / "wallpaper-style-dark.json").exists()
    assert not (tmp_path / "wallpaper-style-light.json").exists()
    # 亮色主题读取不到暗色的样式 → 默认值
    resp = await client.get("/admin/wallpaper", params={"theme": "light"})
    assert resp.json()["style"]["scale"] == 100.0
    resp = await client.get("/admin/wallpaper", params={"theme": "dark"})
    assert resp.json()["style"]["rotate"] == 90.0
    assert resp.json()["style"]["scale"] == 150.0


@pytest.mark.asyncio
async def test_delete_theme_only(client, tmp_path):
    """DELETE ?theme=dark 只删暗色背景。"""
    await client.post(
        "/admin/wallpaper", json={"data_url": _image_data_url(), "theme": "dark"}
    )
    await client.post(
        "/admin/wallpaper", json={"data_url": _video_data_url(), "theme": "light"}
    )
    resp = await client.delete("/admin/wallpaper", params={"theme": "dark"})
    assert resp.status_code == 200
    assert not (tmp_path / "wallpaper-dark.png").exists()
    assert (tmp_path / "wallpaper-light.mp4").exists()


@pytest.mark.asyncio
async def test_legacy_no_theme_keeps_old_behavior(client, tmp_path):
    """theme 缺省时保持旧版单背景行为(向后兼容)。"""
    await client.post("/admin/wallpaper", json={"data_url": _image_data_url()})
    assert (tmp_path / "wallpaper.png").exists()
    resp = await client.get("/admin/wallpaper")
    assert resp.json()["wallpaper"].endswith("wallpaper.png")
    resp = await client.delete("/admin/wallpaper")
    assert resp.status_code == 200
    assert not (tmp_path / "wallpaper.png").exists()
