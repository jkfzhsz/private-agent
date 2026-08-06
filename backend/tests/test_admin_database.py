"""2026-08-06 打包版首启能力 —— 数据库配置端点测试。

覆盖:
- GET /admin/settings/database: 返回连接配置 + 密钥状态(不回显明文)
- PUT /admin/settings/database: host/port/name/user → config_runtime;
  password/master key/admin token → Electron 用户配置 backend.env
- _ensure_master_key 稳定: 多次调用返回同一 key; 打包版只读场景写入 user_env
- password 缺失 → 400

测试隔离: monkeypatch APPDATA 指向临时目录, 避免污染真实用户配置。
"""
import asyncio
import json
import os
import tempfile

import asyncpg
import pytest
from fastapi.testclient import TestClient

from private_agent.api import admin as admin_mod
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


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


@pytest.fixture(scope="module", autouse=True)
def _schema_fixture():
    _setup_schema()


@pytest.fixture(autouse=True)
def _isolated_user_env(monkeypatch, tmp_path):
    """APPDATA 指向临时目录, 隔离真实用户配置(backend.env 写 tmp 下)。

    注意: 不删 PA_ADMIN_TOKEN(conftest 已注入 test-admin-token 供鉴权)。
    """
    appdata = tmp_path / "APPDATA"
    appdata.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    # 清理模块内可能缓存的环境(DB 密码/master key 走隔离目录)
    for k in ("PA_DB_PASSWORD", "PA_MASTER_KEY"):
        monkeypatch.delenv(k, raising=False)
    return appdata


async def _fake_connect(cfg=None):
    return await asyncpg.connect(TEST_DSN)


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    from private_agent.storage import db

    monkeypatch.setattr(db, "connect", _fake_connect)


def _client() -> TestClient:
    from private_agent.main import app

    return TestClient(app)


def _user_env() -> str:
    return admin_mod._user_env_path()


def test_get_database_settings_defaults():
    """GET /settings/database: 默认值来自 config.yaml, 密码未配置。"""
    client = _client()
    resp = client.get(
        "/admin/settings/database",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert resp.status_code == 200
    d = resp.json()
    assert d["host"] == "127.0.0.1"
    assert d["port"] == "5432"  # 2026-08-06: env(PA_DB_*) 字符串统一
    assert d["name"] == "private_agent"
    assert d["user"] == "postgres"
    assert d["password_configured"] is False
    assert d["env_file"] == _user_env()
    # 不回显明文密码
    assert "password" not in d
    # 2026-08-06: master key 明文返回(供查看/备份), 未持久化时自动生成
    assert len(d["master_key"]) == 64
    assert d["master_key_configured"] is True
    # 已持久化到用户配置(启动链路 ensure 的兜底)
    assert admin_mod._read_env_map(_user_env()).get("PA_MASTER_KEY") == d["master_key"]


def test_put_database_settings_persists():
    """PUT: config_runtime 记录连接参数 + backend.env 写入密钥三件套。"""

    async def _run() -> None:
        client = _client()
        resp = client.put(
            "/admin/settings/database",
            headers={"X-Admin-Token": "test-admin-token"},
            json={
                "host": "db.internal",
                "port": 5433,
                "name": "pa_db",
                "user": "pa_user",
                "password": "s3cret-pass",
            },
        )
        assert resp.status_code == 200
        d = resp.json()
        assert d["saved"] is True
        assert d["need_restart"] is True
        assert d["env_file"] == _user_env()

        # config_runtime: database.*(运行时覆盖)
        conn = await asyncpg.connect(TEST_DSN)
        try:
            assert await conn.fetchval(
                "SELECT value FROM config_runtime WHERE key='database.host'"
            ) is not None
            assert json.loads(
                await conn.fetchval(
                    "SELECT value FROM config_runtime WHERE key='database.port'"
                )
            ) == 5433
        finally:
            await conn.close()

        # backend.env: PA_DB_PASSWORD + 稳定密钥
        env_map = admin_mod._read_env_map(_user_env())
        assert env_map.get("PA_DB_PASSWORD") == "s3cret-pass"
        assert env_map.get("PA_MASTER_KEY")  # 已生成
        # PA_ADMIN_TOKEN: 环境已有(conftest 注入)则不重复写文件;
        # 生产首次启动由 ensure 生成并写入 user_env
        assert os.environ.get("PA_ADMIN_TOKEN") or env_map.get("PA_ADMIN_TOKEN")
        # master key 是 64 hex
        assert len(env_map["PA_MASTER_KEY"]) == 64

    asyncio.run(_run())


def test_master_key_stable_and_inherited(monkeypatch, tmp_path):
    """_ensure_master_key: 多次调用返回同一 key; 已存在时不再重新生成。"""
    key1 = admin_mod._ensure_master_key().hex()
    key2 = admin_mod._ensure_master_key().hex()
    assert key1 == key2
    assert len(key1) == 64
    # 已写入 user_env
    assert admin_mod._read_env_map(_user_env()).get("PA_MASTER_KEY") == key1
    # 环境变量被设置(进程内稳定)
    assert os.environ.get("PA_MASTER_KEY") == key1


def test_put_database_requires_password():
    """PUT 缺 password → 400。"""
    client = _client()
    resp = client.put(
        "/admin/settings/database",
        headers={"X-Admin-Token": "test-admin-token"},
        json={"host": "x"},
    )
    assert resp.status_code == 400


def test_put_database_persists_provided_master_key():
    """提供 master_key 时写入 user_env(与旧环境一致的 AES 密钥)。"""

    async def _run() -> None:
        client = _client()
        old_key = "ab" * 32  # 64 hex
        resp = client.put(
            "/admin/settings/database",
            headers={"X-Admin-Token": "test-admin-token"},
            json={"password": "pass1", "master_key": old_key},
        )
        assert resp.status_code == 200
        env_map = admin_mod._read_env_map(_user_env())
        assert env_map.get("PA_MASTER_KEY") == old_key
        assert os.environ.get("PA_MASTER_KEY") == old_key

    asyncio.run(_run())


def test_put_database_rejects_bad_master_key():
    """master_key 非 64 hex → 400。"""
    client = _client()
    resp = client.put(
        "/admin/settings/database",
        headers={"X-Admin-Token": "test-admin-token"},
        json={"password": "p", "master_key": "short"},
    )
    assert resp.status_code == 400


def test_put_database_password_optional_when_configured():
    """2026-08-06: 密码已配置后留空保存 = 不修改(改 host 无需重输密码)。"""

    async def _run() -> None:
        client = _client()
        # 首次配置密码
        r1 = client.put(
            "/admin/settings/database",
            headers={"X-Admin-Token": "test-admin-token"},
            json={"password": "my-pass"},
        )
        assert r1.status_code == 200
        assert admin_mod._read_env_map(_user_env()).get("PA_DB_PASSWORD") == "my-pass"
        # 留空保存: 只改 host, 密码保留
        r2 = client.put(
            "/admin/settings/database",
            headers={"X-Admin-Token": "test-admin-token"},
            json={"host": "db.new"},
        )
        assert r2.status_code == 200
        env = admin_mod._read_env_map(_user_env())
        assert env.get("PA_DB_PASSWORD") == "my-pass"
        assert env.get("PA_DB_HOST") == "db.new"
        # GET 反映: 密码已配置; host 生产由 sidecar 注入 env(userEnv 文件
        # → 后端进程), 测试环境 os.environ 未加载, 断言文件本身已生效
        g = client.get(
            "/admin/settings/database",
            headers={"X-Admin-Token": "test-admin-token"},
        ).json()
        assert g["password_configured"] is True
        assert admin_mod._read_env_map(_user_env()).get("PA_DB_HOST") == "db.new"

    asyncio.run(_run())


def test_put_database_succeeds_when_db_unreachable(monkeypatch):
    """核心场景(2026-08-06 修复): 首次配置 DB 连不上时保存仍 200,
    连接参数写入 user_env(PA_DB_*), 重启后 build_dsn 从 env 生效。"""
    import private_agent.storage.db as db_mod

    async def _boom(cfg=None):
        raise ConnectionRefusedError("db unreachable (first config)")

    monkeypatch.setattr(db_mod, "connect", _boom)
    # _patch_db fixture 也需失效: 直接覆盖 admin 模块引用的 db
    from private_agent.api import admin as _admin
    from private_agent.storage import db as db_ref

    monkeypatch.setattr(db_ref, "connect", _boom)
    assert _admin.db.connect is _boom

    client = _client()
    resp = client.put(
        "/admin/settings/database",
        headers={"X-Admin-Token": "test-admin-token"},
        json={
            "host": "db.internal", "port": 5433, "name": "pa_db",
            "user": "pa_user", "password": "s3cret",
        },
    )
    assert resp.status_code == 200
    d = resp.json()
    assert d["saved"] is True
    # env 已写入 user_env(重启即生效, 不依赖 DB)
    env_map = admin_mod._read_env_map(_user_env())
    assert env_map.get("PA_DB_HOST") == "db.internal"
    assert env_map.get("PA_DB_PORT") == "5433"
    assert env_map.get("PA_DB_NAME") == "pa_db"
    assert env_map.get("PA_DB_USER") == "pa_user"
    assert env_map.get("PA_DB_PASSWORD") == "s3cret"
    # build_dsn 从 env 构造(无需 DB); monkeypatch 保证 env 测试后恢复
    monkeypatch.setenv("PA_DB_HOST", "db.internal")
    monkeypatch.setenv("PA_DB_PORT", "5433")
    monkeypatch.setenv("PA_DB_NAME", "pa_db")
    monkeypatch.setenv("PA_DB_USER", "pa_user")
    monkeypatch.setenv("PA_DB_PASSWORD", "s3cret")
    from private_agent.storage.db import build_dsn

    assert "db.internal:5433/pa_db" in build_dsn()


def test_write_env_updates_preserves_other_keys(tmp_path):
    """_write_env_updates: 只更新目标 key, 保留注释与其他 key 与顺序。"""
    env_file = tmp_path / "backend.env"
    env_file.write_text(
        "# comment\nPA_ADMIN_TOKEN=abc\nPA_MASTER_KEY=oldkey\n", encoding="utf-8"
    )
    admin_mod._write_env_updates(str(env_file), {"PA_DB_PASSWORD": "newpass"})
    content = env_file.read_text(encoding="utf-8")
    assert "# comment" in content
    assert "PA_ADMIN_TOKEN=abc" in content
    assert "PA_MASTER_KEY=oldkey" in content
    assert "PA_DB_PASSWORD=newpass" in content
    # 更新已有 key(不产生重复行)
    admin_mod._write_env_updates(str(env_file), {"PA_ADMIN_TOKEN": "def"})
    content2 = env_file.read_text(encoding="utf-8")
    assert content2.count("PA_ADMIN_TOKEN") == 1
    assert "PA_ADMIN_TOKEN=def" in content2


# ──────────────────────────────────────────────────────────────────────────────
# PA_ADMIN_TOKEN 持久化(2026-08-06 打包版 401 根治)
# ──────────────────────────────────────────────────────────────────────────────


def test_ensure_admin_token_persists_to_user_env(monkeypatch):
    """ensure_admin_token 在 env 缺失时生成并写入用户配置 backend.env;
    重启(清 env)后返回同一 token(稳定, 前端不再 401)。"""
    from private_agent.security import auth

    # env 无 token + 屏蔽 dev backend/.env 兜底 → 模拟打包版全新首启
    monkeypatch.delenv("PA_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(auth, "_env_path", lambda: None)
    t1 = auth.ensure_admin_token()
    assert len(t1) == 64
    # 已持久化到用户配置
    assert auth._read_token_from(auth._user_env_path(), "PA_ADMIN_TOKEN") == t1
    # 模拟重启: 清 env 后 ensure 仍返回同一 token(从 user_env 继承)
    monkeypatch.delenv("PA_ADMIN_TOKEN", raising=False)
    t2 = auth.ensure_admin_token()
    assert t2 == t1
    # get_admin_token 同样读到
    monkeypatch.delenv("PA_ADMIN_TOKEN", raising=False)
    assert auth.get_admin_token() == t1


def test_ensure_admin_token_env_priority(monkeypatch):
    """env 已有 token 时不覆盖用户配置(env 优先)。"""
    from private_agent.security import auth

    monkeypatch.setenv("PA_ADMIN_TOKEN", "from-env")
    assert auth.ensure_admin_token() == "from-env"
    # 用户配置未被写入
    assert auth._read_token_from(auth._user_env_path(), "PA_ADMIN_TOKEN") != "from-env"
