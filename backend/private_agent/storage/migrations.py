"""蓝图 §2.15 storage/migrations.py - Postgres schema 迁移。

B4.1:执行 schema.sql 创建 13 张表(蓝图 §9.14 全表清单)。
后续:版本化迁移(M1+ 需要时引入 alembic)。
"""
from __future__ import annotations

from pathlib import Path

import asyncpg

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


async def migrate_all(conn: asyncpg.Connection) -> None:
    """执行 schema.sql 创建全部表与索引(蓝图 §9.14)。

    幂等:CREATE TABLE/INDEX 无 IF NOT EXISTS 时会在重复执行时报错;
    调用方应先 DROP SCHEMA public CASCADE 再调用(见 test_migrations.py fixture)。
    """
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    await conn.execute(sql)
