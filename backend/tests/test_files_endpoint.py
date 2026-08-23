"""M3 数据分析前端预览卡片 - HTTP 端点测试(spec AC-8)。

Source: plan/m3-remaining-done-criteria step 11/16
- GET /files/outputs/{filename} 文件存在 → 200 + image content-type
- 文件不存在 → 404
- 路径穿越拒绝(如 ../foo、含 os.sep 的路径)
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from private_agent.api import files


def _make_app() -> FastAPI:
    """构造包含 files router 的 FastAPI app(测试用)。"""
    app = FastAPI()
    app.include_router(files.router)
    return app


def _patch_outputs_dir(monkeypatch, outputs_dir: Path) -> None:
    """替换 _get_outputs_dir 返回指定目录。"""
    monkeypatch.setattr(files, "_get_outputs_dir", lambda: outputs_dir)


class TestFilesEndpoint:
    """AC-8: GET /files/outputs/{filename} 行为。"""

    def test_returns_200_with_image_content_type(self, tmp_path, monkeypatch):
        """文件存在 → 200 + image content-type。"""
        outputs_dir = tmp_path / "outputs"
        outputs_dir.mkdir()
        img_file = outputs_dir / "chart.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-content")

        _patch_outputs_dir(monkeypatch, outputs_dir)
        client = TestClient(_make_app())
        resp = client.get("/files/outputs/chart.png")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/")
        assert resp.content == b"\x89PNG\r\n\x1a\nfake-png-content"

    def test_returns_404_when_not_found(self, tmp_path, monkeypatch):
        """文件不存在 → 404。"""
        outputs_dir = tmp_path / "outputs"
        outputs_dir.mkdir()

        _patch_outputs_dir(monkeypatch, outputs_dir)
        client = TestClient(_make_app())
        resp = client.get("/files/outputs/nonexistent.png")

        assert resp.status_code == 404

    def test_returns_404_when_outputs_dir_missing(self, tmp_path, monkeypatch):
        """outputs 目录不存在 → 404(不报 500)。"""
        _patch_outputs_dir(monkeypatch, tmp_path / "nonexistent_outputs")
        client = TestClient(_make_app())
        resp = client.get("/files/outputs/anything.png")

        assert resp.status_code == 404

    def test_path_traversal_rejected(self, tmp_path, monkeypatch):
        """路径穿越(../) → 404(不泄漏目录结构)。"""
        outputs_dir = tmp_path / "outputs"
        outputs_dir.mkdir()
        # 在 tmp_path 下放一个 secret 文件,确保穿越不会得逞
        secret = tmp_path / "secret.txt"
        secret.write_text("top-secret")

        _patch_outputs_dir(monkeypatch, outputs_dir)
        client = TestClient(_make_app())
        # FastAPI 路由参数本身不含 /,但 URL 编码的 ../ 仍可能命中
        resp = client.get("/files/outputs/..%2Fsecret.txt")

        assert resp.status_code == 404
        # 关键:不返回 secret 内容
        assert b"top-secret" not in resp.content

    def test_jpeg_content_type(self, tmp_path, monkeypatch):
        """jpg 文件 → image/jpeg content-type。"""
        outputs_dir = tmp_path / "outputs"
        outputs_dir.mkdir()
        (outputs_dir / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpg")

        _patch_outputs_dir(monkeypatch, outputs_dir)
        client = TestClient(_make_app())
        resp = client.get("/files/outputs/photo.jpg")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/")
