"""2026-08-12 Phase1: 技能压缩包一键识别安装增强测试。

覆盖:
- 完整 zip(skill.yaml + system_prompt.md) → installed 含中文元数据字段
- 缺 system_prompt.md → failed 列出原因
- skill.yaml 缺 version → failed 列出原因
- 工具白名单引用不存在 → failed 列出原因
"""
import io
import os
import zipfile

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
    """ASGI 客户端 + db.connect 指向测试库 + 隔离 skill 目录。"""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    async def _fake_connect():
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)

    import private_agent.config.loader as cfg_loader

    original_load = cfg_loader.load_config
    dev_dir = tmp_path / "skills"
    dev_dir.mkdir(exist_ok=True)

    def _fake_load_config():
        cfg = original_load()
        return {**cfg, "skills": {"storage": {"dev_dir": str(dev_dir)}}}

    monkeypatch.setattr(cfg_loader, "load_config", _fake_load_config)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), dev_dir


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


SKILL_YAML_OK = """\
name: test_skill_a
version: "1.0.0"
description: A test skill for upload
scenario: testing uploads
"""


@pytest.mark.asyncio
async def test_upload_zip_ok_installs_with_metadata(client, schema):
    """完整 zip → installed 返回 display_name/description/scenario/tools。"""
    client, dev_dir = client
    zdata = _zip_bytes({
        "test_skill_a/skill.yaml": SKILL_YAML_OK,
        "test_skill_a/system_prompt.md": "You are a test skill assistant.",
    })
    resp = await client.post(
        "/admin/skills/upload-zip",
        files={"file": ("skill.zip", zdata, "application/zip")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert len(data["installed"]) == 1
    item = data["installed"][0]
    assert item["name"] == "test_skill_a"
    # LLM 翻译不可用(测试环境无 key) → 回退原始英文元数据, 不阻塞安装
    assert item["description"]
    assert item["scenario"]
    assert item["display_name"]
    assert item["files"] >= 2
    # 技能目录真实落盘
    assert (dev_dir / "test_skill_a" / "skill.yaml").exists()
    assert (dev_dir / "test_skill_a" / "system_prompt.md").exists()


@pytest.mark.asyncio
async def test_upload_zip_missing_system_prompt_fails_with_reason(client, schema):
    """缺 system_prompt.md → failed 列出字段与原因, 不安装。"""
    client, dev_dir = client
    zdata = _zip_bytes({
        "test_skill_b/skill.yaml": SKILL_YAML_OK,
    })
    resp = await client.post(
        "/admin/skills/upload-zip",
        files={"file": ("skill.zip", zdata, "application/zip")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert len(data["installed"]) == 0
    assert len(data["failed"]) == 1
    err_fields = [e["field"] for e in data["failed"][0]["errors"]]
    assert "system_prompt.md" in err_fields
    # 未落盘
    assert not (dev_dir / "test_skill_b").exists()


@pytest.mark.asyncio
async def test_upload_zip_missing_version_fails_with_reason(client, schema):
    """skill.yaml 缺 version → failed 列出字段。"""
    client, dev_dir = client
    zdata = _zip_bytes({
        "test_skill_c/skill.yaml": (
            "name: test_skill_c\nscenario: testing\n"
        ),
        "test_skill_c/system_prompt.md": "prompt",
    })
    resp = await client.post(
        "/admin/skills/upload-zip",
        files={"file": ("skill.zip", zdata, "application/zip")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert len(data["failed"]) == 1
    err_fields = [e["field"] for e in data["failed"][0]["errors"]]
    assert "version" in err_fields


@pytest.mark.asyncio
async def test_upload_zip_unknown_tool_fails_with_reason(client, schema):
    """dependencies.tools 引用不存在工具 → failed 列出原因。"""
    client, dev_dir = client
    yaml = SKILL_YAML_OK + (
        'dependencies:\n  tools:\n    - name: no_such_tool_xyz\n'
    )
    zdata = _zip_bytes({
        "test_skill_d/skill.yaml": yaml,
        "test_skill_d/system_prompt.md": "prompt",
    })
    resp = await client.post(
        "/admin/skills/upload-zip",
        files={"file": ("skill.zip", zdata, "application/zip")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    err_text = str(data["failed"][0]["errors"])
    assert "no_such_tool_xyz" in err_text
