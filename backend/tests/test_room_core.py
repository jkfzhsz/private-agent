"""会议室核心(core/room.py) + sessions.kind='room' 迁移 —— 单元与集成测试。

设计文档: docs/next-phase-plan-2026-09-11-meeting-room.md

覆盖:
1. 角色枚举与规范化(纯函数) —— 防模型传入任意 skill 名越权;
2. 房间键/根目录/目录骨架(纯函数 + 文件系统) —— 防路径穿越, README 幂等;
3. `resolve_role_whitelist` 与 `main._get_frozen_tools` 的**逐项一致性**
   (设计文档 §9 验收 3: 角色子代理工具集须与该角色独立场景会话一致);
4. 迁移: kind CHECK 含 'room'、room_meta 列存在、可重复执行;
5. `build_room_contract`(W3 系统提示词注入的房间约定文本) —— 主持人/成员
   两种视角的措辞与目录分工, 纯函数, 零依赖。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
import pytest

from private_agent.core import room as room_mod
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

#: 真实技能目录(不随 CWD 变化)。
REAL_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


def _setup_schema() -> None:
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


def _patch_skill_loader_to_real_skills(monkeypatch) -> None:
    """SkillLoader.from_cfg → dev_dir 指向真实 skills 目录(与 CWD 无关)。"""
    from private_agent.skills.loader import SkillLoader

    def _fake_from_cfg(cfg):
        return SkillLoader(dev_dir=str(REAL_SKILLS_DIR))

    monkeypatch.setattr(SkillLoader, "from_cfg", _fake_from_cfg)


def _run(coro_fn):
    return asyncio.run(coro_fn())


# ────────────────────────────────────────────────────────────────────────────
# 1. 角色枚举与规范化
# ────────────────────────────────────────────────────────────────────────────


class TestRoleWhitelist:
    def test_accepts_three_scene_roles(self):
        for r in ("office", "data_analysis", "frontend_design"):
            assert room_mod.is_room_role(r) is True

    @pytest.mark.parametrize(
        "bad",
        ["monitor", "无涯", "", None, 123, ["office"], "OFFICE", "office "],
    )
    def test_rejects_non_rooms(self, bad):
        """monitor(无涯)按设计不参与; 大小写/空白/非字符串一律拒绝。"""
        assert room_mod.is_room_role(bad) is False

    def test_normalize_dedupes_and_keeps_order(self):
        members, host = room_mod.normalize_roles(
            ["frontend_design", "office", "office"], "office"
        )
        assert members == ["frontend_design", "office"]
        assert host == "office"

    def test_normalize_rejects_empty_members(self):
        with pytest.raises(room_mod.RoomRoleError):
            room_mod.normalize_roles([], "office")

    def test_normalize_rejects_illegal_member(self):
        with pytest.raises(room_mod.RoomRoleError):
            room_mod.normalize_roles(["office", "monitor"], "office")

    def test_normalize_rejects_host_not_in_members(self):
        """主持人必须从参会成员中产生(§7.2 UI 语义)。"""
        with pytest.raises(room_mod.RoomRoleError):
            room_mod.normalize_roles(["office"], "data_analysis")


# ────────────────────────────────────────────────────────────────────────────
# 2. 房间键 / 根目录 / 目录骨架
# ────────────────────────────────────────────────────────────────────────────


class TestRoomKeyAndPaths:
    def test_room_key_format(self):
        key = room_mod.room_key()
        assert room_mod.is_valid_room_key(key), key
        assert len(key) == len("20260911-153012-abcd")

    def test_room_key_unique(self):
        keys = {room_mod.room_key() for _ in range(200)}
        # 同秒内 4 位 hex 允许碰撞, 但 200 次不应全部相同
        assert len(keys) > 1

    @pytest.mark.parametrize(
        "bad",
        [
            "../escape",
            "..\\escape",
            "2026-09-11",
            "20260911-153012",
            "20260911-153012-ZZZZ",
            "",
            None,
            123,
        ],
    )
    def test_is_valid_room_key_rejects_traversal(self, bad):
        assert room_mod.is_valid_room_key(bad) is False

    def test_room_dir_rejects_invalid_key(self):
        with pytest.raises(ValueError):
            room_mod.room_dir({}, "../etc")

    def test_rooms_root_explicit_config_wins(self):
        cfg = {"system": {"rooms_root": "D:\\custom\\rooms"}}
        assert room_mod.rooms_root(cfg, "D:\\PA\\zizhan") == Path(
            "D:\\custom\\rooms"
        )

    def test_rooms_root_derives_from_host_workspace_sibling(self):
        """派生规则: 主持人工作区同级 rooms/(D:\\PA\\zizhan → D:\\PA\\rooms)。"""
        cfg: dict = {}
        assert room_mod.rooms_root(cfg, "D:\\PA\\zizhan") == Path("D:\\PA\\rooms")

    def test_rooms_root_falls_back_to_workspace_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PA_ROOMS_TEST_ROOT", str(tmp_path))
        cfg = {"system": {"workspace_root": "${PA_ROOMS_TEST_ROOT}"}}
        assert room_mod.rooms_root(cfg) == tmp_path / "rooms"


class TestRoomLayout:
    def test_readme_marks_host_only(self):
        md = room_mod.build_room_readme(
            goal="做一份经营分析 PPT",
            host_role="office",
            members=["office", "frontend_design"],
            room_key_="20260911-153012-abcd",
        )
        assert "做一份经营分析 PPT" in md
        assert "20260911-153012-abcd" in md
        # 主持人标记只出现一次, 且落在主持人那一行
        assert md.count("本次主持人") == 1
        host_line = [ln for ln in md.splitlines() if "本次主持人" in ln][0]
        assert "子瞻" in host_line

    def test_ensure_layout_creates_dirs_and_readme(self, tmp_path):
        base = tmp_path / "20260911-153012-abcd"
        room_mod.ensure_room_layout(
            base, goal="g", host_role="office",
            members=["office"], room_key_="20260911-153012-abcd",
        )
        assert base.is_dir()
        assert (base / "artifacts").is_dir()
        assert (base / "notes").is_dir()
        assert (base / "README.md").is_file()

    def test_ensure_layout_is_idempotent_and_preserves_readme(self, tmp_path):
        """重复建房/重启不得覆盖已有记录。"""
        base = tmp_path / "20260911-153012-abcd"
        room_mod.ensure_room_layout(
            base, goal="原始目标", host_role="office",
            members=["office"], room_key_="k",
        )
        (base / "README.md").write_text("人工修订过", encoding="utf-8")
        room_mod.ensure_room_layout(
            base, goal="新目标", host_role="office",
            members=["office"], room_key_="k",
        )
        assert (base / "README.md").read_text(encoding="utf-8") == "人工修订过"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ({"a": 1}, {"a": 1}),
            ('{"host_role": "office"}', {"host_role": "office"}),
            (None, {}),
            ("", {}),
            ("not-json", {}),
            ("[1,2]", {}),
            (123, {}),
        ],
    )
    def test_parse_room_meta_is_robust(self, raw, expected):
        assert room_mod.parse_room_meta(raw) == expected


# ────────────────────────────────────────────────────────────────────────────
# 3. 角色工具白名单 —— 与场景会话逐项一致(设计文档 §9 验收 3)
# ────────────────────────────────────────────────────────────────────────────


class TestResolveRoleWhitelist:
    def test_rejects_illegal_role(self):
        async def _run_it():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                with pytest.raises(room_mod.RoomRoleError):
                    await room_mod.resolve_role_whitelist({}, conn, "monitor")
            finally:
                await conn.close()

        _setup_schema()
        _run(_run_it)

    @pytest.mark.parametrize(
        "role", ["office", "data_analysis", "frontend_design"]
    )
    def test_parity_with_scene_session_frozen_tools(self, monkeypatch, role):
        """角色白名单 ∪ 基础工具 == 该角色独立场景会话的 frozen_tools。

        这是「清和的脑子配子瞻的手」风险的守门测试: 角色子代理的工具集
        必须与该角色自己的场景会话完全一致。
        """
        import private_agent.main as main_mod
        from private_agent.tools.builtins import register_all_builtins
        from private_agent.tools.registry import ToolRegistry

        _setup_schema()
        _patch_skill_loader_to_real_skills(monkeypatch)

        async def _run_it():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                sid = await conn.fetchval(
                    "INSERT INTO sessions (title, locked_skill_name) "
                    "VALUES ($1, $2) RETURNING id",
                    "parity", role,
                )
                frozen = await main_mod._get_frozen_tools({}, sid, conn)
                frozen_names = {t.name for t in frozen}

                whitelist = await room_mod.resolve_role_whitelist(
                    {}, conn, role
                )

                registry = ToolRegistry()
                register_all_builtins(registry)
                registered = {t.name for t in registry.list_tools()}

                expected = (
                    whitelist | main_mod._ALWAYS_AVAILABLE_TOOLS
                ) & registered

                assert whitelist, f"{role} 白名单为空(skill 未加载?)"
                assert frozen_names == expected
            finally:
                await conn.close()

        _run(_run_it)


# ────────────────────────────────────────────────────────────────────────────
# 4. 迁移: kind='room' + room_meta
# ────────────────────────────────────────────────────────────────────────────


class TestRoomMigration:
    def test_kind_check_accepts_room_and_rejects_bogus(self):
        _setup_schema()

        async def _run_it():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                sid = await conn.fetchval(
                    "INSERT INTO sessions (title, kind) "
                    "VALUES ($1, 'room') RETURNING id",
                    "room-migration",
                )
                assert sid
                with pytest.raises(asyncpg.exceptions.CheckViolationError):
                    await conn.execute(
                        "INSERT INTO sessions (title, kind) VALUES ('x', 'bogus')"
                    )
            finally:
                await conn.close()

        _run(_run_it)

    def test_room_meta_column_roundtrip(self):
        _setup_schema()

        async def _run_it():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                raw = await conn.fetchval(
                    "INSERT INTO sessions (title, kind, room_meta) "
                    "VALUES ('r', 'room', $1::jsonb) RETURNING room_meta",
                    '{"host_role": "office", "members": ["office"]}',
                )
                assert room_mod.parse_room_meta(raw)["host_role"] == "office"
            finally:
                await conn.close()

        _run(_run_it)

    def test_migrate_all_is_repeatable(self):
        """老库二次启动场景: migrate_all 必须可重复执行不报错。"""
        _setup_schema()

        async def _run_it():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                await migrations.migrate_all(conn)
                await migrations.migrate_all(conn)
                # 约束仍生效
                with pytest.raises(asyncpg.exceptions.CheckViolationError):
                    await conn.execute(
                        "INSERT INTO sessions (title, kind) VALUES ('y', 'nope')"
                    )
            finally:
                await conn.close()

        _run(_run_it)


# ────────────────────────────────────────────────────────────────────────────
# 5. 房间约定文本(W3 系统提示词注入)
# ────────────────────────────────────────────────────────────────────────────

ROOM_DIR = "D:/PA/rooms/20260911-153012-abcd"


class TestRoleDisplay:
    def test_labels_cover_all_roles(self):
        """三个角色都必须有中文显示名(单一来源, 防 README 与提示词漂移)。"""
        for r in room_mod.ROOM_ROLES:
            assert r in room_mod.ROLE_LABELS
        assert room_mod.role_label("office") == "子瞻"
        assert room_mod.role_label("data_analysis") == "白圭"
        assert room_mod.role_label("frontend_design") == "清和"

    def test_unknown_role_degrades_without_raising(self):
        """展示路径不得抛异常(脏 room_meta 也要能渲染)。"""
        assert room_mod.role_label(None) == ""
        assert room_mod.role_label("ghost") == "ghost"
        assert room_mod.role_display("ghost") == "ghost"
        assert room_mod.role_display("office") == "子瞻(office)"


class TestBuildRoomContract:
    def _host(self, **kw) -> str:
        base = dict(
            goal="做一份 Q3 经营分析汇报 PPT",
            host_role="office",
            members=["office", "frontend_design"],
            room_dir=ROOM_DIR,
            self_role="office",
            is_host=True,
        )
        base.update(kw)
        return room_mod.build_room_contract(**base)

    def _member(self, **kw) -> str:
        base = dict(
            goal="做一份 Q3 经营分析汇报 PPT",
            host_role="office",
            members=["office", "frontend_design"],
            room_dir=ROOM_DIR,
            self_role="frontend_design",
            is_host=False,
        )
        base.update(kw)
        return room_mod.build_room_contract(**base)

    # ── 共性: 目录分工与任务上下文必须出现 ──────────────────────────────
    @pytest.mark.parametrize("variant", ["host", "member"])
    def test_both_variants_carry_dir_convention(self, variant):
        """核心契约: artifacts/ 放产物、notes/ 记交接 —— 缺了成员就会写错位置。"""
        text = self._host() if variant == "host" else self._member()
        assert "[会议室约定]" in text
        assert ROOM_DIR in text
        assert f"{ROOM_DIR}/artifacts/" in text
        assert f"{ROOM_DIR}/notes/" in text
        assert f"{ROOM_DIR}/README.md" in text

    @pytest.mark.parametrize("variant", ["host", "member"])
    def test_both_variants_carry_goal_and_members(self, variant):
        text = self._host() if variant == "host" else self._member()
        assert "做一份 Q3 经营分析汇报 PPT" in text
        assert "子瞻(office)" in text
        assert "清和(frontend_design)" in text

    # ── 主持人视角 ───────────────────────────────────────────────────────
    def test_host_variant_states_role_duty_and_delegation(self):
        text = self._host()
        assert "主持人" in text
        assert "子瞻(office)" in text
        # 委派方式与角色取值必须写明, 否则主持人无从下手
        assert "delegate_subtask" in text
        assert "subtasks[].role" in text
        for r in room_mod.ROOM_ROLES:
            assert f"`{r}`" in text
        # 产物交接的核心动作: 读上游产物而不是重新生成
        assert "file_read" in text
        assert "不要重复生成" in text

    def test_host_variant_does_not_use_member_wording(self):
        """主持人段不得自称成员(否则统筹职责被弱化)。"""
        assert "你是本次会议的**成员**" not in self._host()

    # ── 成员视角 ─────────────────────────────────────────────────────────
    def test_member_variant_marks_self_and_host(self):
        text = self._member()
        assert "**成员**" in text
        assert "清和(frontend_design)" in text
        # 成员须知道向谁负责
        assert "主持人（子瞻(office)）" in text

    def test_member_variant_forbids_delegating(self):
        """成员不得被赋予委派指引 —— 子代理嵌套深度恒 1, 指引会诱发失败调用。"""
        text = self._member()
        assert "delegate_subtask" not in text
        assert "subtasks[].role" not in text

    def test_member_variant_warns_about_overwrite_and_outside_write(self):
        text = self._member()
        assert "不要覆盖" in text
        assert "房间目录之外" in text

    # ── 降级与健壮性 ─────────────────────────────────────────────────────
    def test_degenerate_call_is_non_empty_and_never_raises(self):
        """缺字段降级为占位符(调用方无需判空, 读路径不崩)。"""
        text = room_mod.build_room_contract()
        assert text.startswith("[会议室约定]")
        assert "(未填写)" in text
        assert "(未配置)" in text

    def test_ignores_non_string_and_empty_members(self):
        text = self._host(members=["office", None, "", 123, "frontend_design"])
        assert "子瞻(office)" in text
        assert "清和(frontend_design)" in text
        assert "None" not in text

    def test_empty_members_degrades_to_placeholder(self):
        assert "(未记录)" in self._host(members=[])
