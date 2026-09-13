"""会议室 admin 端点 —— W2b 测试(设计文档 §6.3 / §9)。

覆盖:
1. POST /admin/rooms: 建房 → 会话行(kind/locked_skill_name/workspace/room_meta)
   + 目录骨架(README/artifacts/notes) + README 任务契约内容;
2. 房间根目录两条解析路径: 显式 system.rooms_root / 由主持人工作区派生(同级 rooms/);
3. 角色校验失败 → 400(非法成员 / 空成员 / 主持人不在成员内);
4. GET /admin/rooms/{id}: 元数据 + 产物/交接清单; 非房间会话与不存在 → 404;
5. 历史列表携带 room_meta(需求 6: 历史树"会议室"分组的数据前提);
6. 防御: 通用建会话端点不得产出残缺房间(kind='room' 被降级)。
"""
from __future__ import annotations

import asyncio
import json
import os

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from private_agent.api import admin
from private_agent.api.admin import router
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


@pytest.fixture
def schema():
    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
        finally:
            await conn.close()

    asyncio.run(_run())


@pytest.fixture
def client(monkeypatch, schema):
    """裸 FastAPI + 路由(无鉴权中间件, 与既有 admin 用例同模式)。"""
    app = FastAPI()
    app.include_router(router)

    async def _fake_connect(*_a, **_k):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(admin.db, "connect", _fake_connect)
    return TestClient(app)


def _patch_rooms_root(monkeypatch, rooms_root: str | None) -> None:
    """覆写 config 的 system.rooms_root(None = 移除, 走"主持人工作区同级"派生)。"""
    import private_agent.config.loader as cfg_loader

    original_load = cfg_loader.load_config

    def _fake_load_config(*_a, **_k):
        cfg = original_load()
        system = {**(cfg.get("system") or {})}
        if rooms_root is None:
            system.pop("rooms_root", None)
        else:
            system["rooms_root"] = rooms_root
        return {**cfg, "system": system}

    monkeypatch.setattr(cfg_loader, "load_config", _fake_load_config)


def _insert_host_skill(name: str, workspace: str) -> None:
    """在 skills 表写入主持人技能(含 manifest.workspace, 供房间根目录派生)。

    manifest 必须是 **SkillManifest 可解析的完整对象**(name/version/scenario
    必填) —— 建房现经 SkillLoader 加载(PG 优先), 残缺 manifest 会在
    SkillManifest(**dict) 处抛 TypeError, 且被 create_room 的降级逻辑吞掉。
    """

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute(
                "INSERT INTO skills (name, version, manifest) "
                "VALUES ($1, '1.0.0', $2::jsonb) "
                "ON CONFLICT (name) DO UPDATE SET manifest = $2::jsonb",
                name,
                json.dumps(
                    {
                        "name": name,
                        "version": "1.0.0",
                        "scenario": name,
                        "workspace": workspace,
                    }
                ),
            )
        finally:
            await conn.close()

    asyncio.run(_run())


def _fetch_session(sid: int) -> dict:
    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return dict(
                await conn.fetchrow(
                    "SELECT id, title, kind, status, locked_skill_name, "
                    "workspace, room_meta FROM sessions WHERE id=$1",
                    sid,
                )
            )
        finally:
            await conn.close()

    return asyncio.run(_run())


# ────────────────────────────────────────────────────────────────────────────
# 1. 建房主路径
# ────────────────────────────────────────────────────────────────────────────


class TestCreateRoom:
    def test_happy_path_creates_session_and_layout(
        self, client, tmp_path, monkeypatch
    ):
        host_ws = tmp_path / "PA" / "zizhan"
        _insert_host_skill("office", str(host_ws))
        rooms_root = tmp_path / "rooms"
        _patch_rooms_root(monkeypatch, str(rooms_root))

        resp = client.post(
            "/admin/rooms",
            json={
                "goal": "做一份 Q3 经营分析汇报 PPT",
                "members": ["office", "frontend_design"],
                "host_role": "office",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["kind"] == "room"
        assert body["host_role"] == "office"
        assert body["members"] == ["office", "frontend_design"]

        from pathlib import Path

        room_dir = Path(body["room_dir"])
        assert room_dir.parent == rooms_root
        assert (room_dir / "artifacts").is_dir()
        assert (room_dir / "notes").is_dir()
        readme = (room_dir / "README.md").read_text(encoding="utf-8")
        assert "做一份 Q3 经营分析汇报 PPT" in readme
        assert "本次主持人" in readme
        assert "artifacts/" in readme

        row = _fetch_session(body["id"])
        assert row["kind"] == "room"
        assert row["status"] == "active"
        assert row["locked_skill_name"] == "office"
        assert str(row["workspace"]) == str(room_dir)
        from private_agent.core import room as room_core

        meta = room_core.parse_room_meta(row["room_meta"])
        assert meta["host_role"] == "office"
        assert meta["members"] == ["office", "frontend_design"]
        assert meta["goal"] == "做一份 Q3 经营分析汇报 PPT"

    def test_default_title_uses_goal(self, client, tmp_path, monkeypatch):
        _insert_host_skill("office", str(tmp_path / "PA" / "zizhan"))
        _patch_rooms_root(monkeypatch, str(tmp_path / "rooms"))
        resp = client.post(
            "/admin/rooms",
            json={
                "goal": "年度信贷复盘",
                "members": ["office"],
                "host_role": "office",
            },
        )
        assert resp.status_code == 200
        assert _fetch_session(resp.json()["id"])["title"] == "年度信贷复盘"


# ────────────────────────────────────────────────────────────────────────────
# 2. 房间根目录解析
# ────────────────────────────────────────────────────────────────────────────


class TestRoomsRootResolution:
    def test_derives_sibling_of_host_workspace(
        self, client, tmp_path, monkeypatch
    ):
        """无显式配置 → 由主持人工作区派生同级 rooms/(D:\\PA\\zizhan → D:\\PA\\rooms)。"""
        _patch_rooms_root(monkeypatch, None)
        host_ws = tmp_path / "PA" / "zizhan"
        _insert_host_skill("office", str(host_ws))

        resp = client.post(
            "/admin/rooms",
            json={"goal": "g", "members": ["office"], "host_role": "office"},
        )
        assert resp.status_code == 200, resp.text
        from pathlib import Path

        room_dir = Path(resp.json()["room_dir"])
        assert room_dir.parent == tmp_path / "PA" / "rooms"


# ────────────────────────────────────────────────────────────────────────────
# 3. 角色校验
# ────────────────────────────────────────────────────────────────────────────


class TestRoleValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {"goal": "g", "members": [], "host_role": "office"},
            {"goal": "g", "members": ["office", "monitor"], "host_role": "office"},
            {"goal": "g", "members": ["office"], "host_role": "data_analysis"},
            {"goal": "g", "members": ["office"], "host_role": "无涯"},
        ],
    )
    def test_invalid_roles_rejected(self, client, payload):
        resp = client.post("/admin/rooms", json=payload)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_roles"


# ────────────────────────────────────────────────────────────────────────────
# 4. 读取房间
# ────────────────────────────────────────────────────────────────────────────


class TestGetRoom:
    def test_returns_meta_and_file_lists(
        self, client, tmp_path, monkeypatch
    ):
        _insert_host_skill("office", str(tmp_path / "PA" / "zizhan"))
        _patch_rooms_root(monkeypatch, str(tmp_path / "rooms"))
        created = client.post(
            "/admin/rooms",
            json={
                "goal": "g",
                "members": ["office", "frontend_design"],
                "host_role": "office",
            },
        ).json()

        from pathlib import Path

        room_dir = Path(created["room_dir"])
        (room_dir / "artifacts" / "report.md").write_text("产物", encoding="utf-8")
        (room_dir / "notes" / "handoff.md").write_text("交接", encoding="utf-8")

        resp = client.get(f"/admin/rooms/{created['id']}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["host_role"] == "office"
        assert body["members"] == ["office", "frontend_design"]
        assert body["exists"] is True
        assert [f["name"] for f in body["artifacts"]] == ["report.md"]
        assert [f["name"] for f in body["notes"]] == ["handoff.md"]
        assert body["artifacts"][0]["size"] > 0

    def test_non_room_session_is_404(self, client):
        async def _run():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                return await conn.fetchval(
                    "INSERT INTO sessions (title, kind) VALUES ('普通', 'main') "
                    "RETURNING id"
                )
            finally:
                await conn.close()

        sid = asyncio.run(_run())
        resp = client.get(f"/admin/rooms/{sid}")
        assert resp.status_code == 404
        assert resp.json()["error"] == "room_not_found"

    def test_missing_session_is_404(self, client):
        resp = client.get("/admin/rooms/999999")
        assert resp.status_code == 404


# ────────────────────────────────────────────────────────────────────────────
# 5. 历史列表携带 room_meta
# ────────────────────────────────────────────────────────────────────────────


class TestHistoryCarriesRoomMeta:
    def test_room_with_message_appears_with_meta(
        self, client, tmp_path, monkeypatch
    ):
        _insert_host_skill("office", str(tmp_path / "PA" / "zizhan"))
        _patch_rooms_root(monkeypatch, str(tmp_path / "rooms"))
        created = client.post(
            "/admin/rooms",
            json={
                "goal": "会议室历史验证",
                "members": ["office", "data_analysis"],
                "host_role": "data_analysis",
            },
        ).json()

        # 历史树只收"发生过对话"的会话(至少一条 assistant 回复)
        async def _seed():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                await conn.execute(
                    "INSERT INTO messages (session_id, role, content, turn) "
                    "VALUES ($1, 'assistant', '好的', 1)",
                    created["id"],
                )
            finally:
                await conn.close()

        asyncio.run(_seed())

        resp = client.get("/admin/sessions")
        assert resp.status_code == 200
        items = {s["id"]: s for s in resp.json()}
        assert created["id"] in items
        item = items[created["id"]]
        assert item["kind"] == "room"
        assert item["room_meta"]["host_role"] == "data_analysis"
        assert item["room_meta"]["members"] == ["office", "data_analysis"]


# ────────────────────────────────────────────────────────────────────────────
# 6. 防御: 通用建会话端点不得产出残缺房间
# ────────────────────────────────────────────────────────────────────────────


class TestRoomListForMeetingView:
    """P2(会议室视图)依赖的列表契约: 刚建好、尚无对话的房间也必须可见。

    前端 MeetingRoomView 用 ``GET /admin/sessions?has_messages=false`` 取房间
    列表 —— 默认 ``has_messages=true`` 会按"至少一条 assistant 回复"过滤, 新房
    直接消失(用户会以为建房失败)。此用例守住该参数语义。
    """

    def test_fresh_room_visible_only_with_has_messages_false(
        self, client, tmp_path, monkeypatch
    ):
        _insert_host_skill("office", str(tmp_path / "PA" / "zizhan"))
        _patch_rooms_root(monkeypatch, str(tmp_path / "rooms"))
        created = client.post(
            "/admin/rooms",
            json={
                "goal": "做一份 Q3 经营分析汇报 PPT",
                "members": ["office", "frontend_design"],
                "host_role": "office",
            },
        ).json()

        # 默认(has_messages=true): 无 assistant 回复 → 不进历史列表
        default_items = {
            s["id"]: s for s in client.get("/admin/sessions").json()
        }
        assert created["id"] not in default_items

        # has_messages=false: 可见, 且 kind/room_meta 齐备(前端据此渲染房间行)
        items = {
            s["id"]: s
            for s in client.get("/admin/sessions?has_messages=false").json()
        }
        assert created["id"] in items
        row = items[created["id"]]
        assert row["kind"] == "room"
        assert row["room_meta"]["host_role"] == "office"
        assert row["room_meta"]["members"] == ["office", "frontend_design"]
        assert row["title"] == "做一份 Q3 经营分析汇报 PPT"


def test_generic_session_endpoint_downgrades_room(client):
    resp = client.post("/admin/sessions", json={"title": "假房间", "kind": "room"})
    assert resp.status_code == 200
    # 降级为 main —— 残缺房间(kind='room' 但无 workspace/room_meta)不可能产生
    assert resp.json()["kind"] == "main"
    assert _fetch_session(resp.json()["id"])["kind"] == "main"


class TestRoomRootFromFilesystemSkill:
    """镜像生产缺陷(2026-09-12 蒋先生实测: 房间落到了 backend/rooms)。

    根因链: 生产库 `skills` 表为空(技能走文件系统 skill.yaml) → 原实现直查
    PG 取 manifest.workspace 拿到 None → rooms_root 走第 3 兜底
    `workspace_root/rooms` = backend/rooms。修复后建房经 **SkillLoader**
    (PG 优先 + 文件回退)取 workspace, 与场景会话注入同源(skills/manager.py)。
    """

    def test_room_root_derived_from_filesystem_skill_yaml(
        self, client, tmp_path, monkeypatch
    ):
        from pathlib import Path
        import re as _re

        from private_agent.skills.loader import SkillLoader

        # 生产镜像: **不插** PG skills 行(表为空), 技能只存在于文件系统
        real_yaml = Path(__file__).resolve().parents[1] / "skills" / "office" / "skill.yaml"
        assert real_yaml.exists(), "真实 office/skill.yaml 缺失(测试前提)"
        host_ws = tmp_path / "PA" / "zizhan"
        fake_skill_dir = tmp_path / "skills" / "office"
        fake_skill_dir.mkdir(parents=True)
        # 复制真实 yaml 保证 manifest 字段合法, 仅改 workspace 指向 tmp
        # (replacement 用 lambda —— Windows 路径含 \U 会被 re 当转义)
        text = real_yaml.read_text(encoding="utf-8")
        text = _re.sub(
            r"(?m)^workspace:.*$",
            lambda _m: f"workspace: {host_ws}",
            text,
        )
        (fake_skill_dir / "skill.yaml").write_text(text, encoding="utf-8")

        def _fake_from_cfg(cfg):
            return SkillLoader(dev_dir=str(tmp_path / "skills"))

        monkeypatch.setattr(SkillLoader, "from_cfg", _fake_from_cfg)
        # 不覆盖 rooms_root → 走"主持人工作区同级 rooms/"派生
        _patch_rooms_root(monkeypatch, None)

        resp = client.post(
            "/admin/rooms",
            json={
                "goal": "做一份 Q3 经营分析汇报 PPT",
                "members": ["office", "frontend_design"],
                "host_role": "office",
            },
        )
        assert resp.status_code == 200, resp.text
        room_dir = Path(resp.json()["room_dir"])
        # 房间根 = 主持人工作区(zizhan)同级的 rooms/, **不是** backend/rooms
        assert room_dir.parent == tmp_path / "PA" / "rooms"
        assert "backend" not in room_dir.parts
        assert (room_dir / "artifacts").is_dir()

        row = _fetch_session(resp.json()["id"])
        assert str(row["workspace"]) == str(room_dir)

    def test_unknown_host_skill_still_creates_with_global_fallback(
        self, client, tmp_path, monkeypatch
    ):
        """主持人技能彻底不存在 → 降级 workspace_root/rooms, 建房不失败
        (房间可用性优先于目录位置; 代价在 README/提示词中可见可纠正)。"""
        from pathlib import Path

        from private_agent.skills.loader import SkillLoader

        empty_dir = tmp_path / "skills_empty"
        empty_dir.mkdir()

        def _fake_from_cfg(cfg):
            return SkillLoader(dev_dir=str(empty_dir))

        monkeypatch.setattr(SkillLoader, "from_cfg", _fake_from_cfg)
        fallback_root = tmp_path / "fallback" / "backend"
        _patch_rooms_root(monkeypatch, str(fallback_root / "rooms"))

        resp = client.post(
            "/admin/rooms",
            json={"goal": "g", "members": ["office"], "host_role": "office"},
        )
        assert resp.status_code == 200, resp.text
        assert Path(resp.json()["room_dir"]).parent == fallback_root / "rooms"
