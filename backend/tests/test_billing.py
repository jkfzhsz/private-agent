"""B4 P0-4 AC-9..10 - BillingRecorder 测试。

Source: plan/b4-compress-billing step 14 (AC-9, AC-10)
"""
import asyncio
import json
import os

import asyncpg

from private_agent.core.billing import BillingRecorder, TokenUsage

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _setup_schema() -> None:
    from private_agent.storage import migrations

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


# ── AC-9: token_usage 事件 ──

def test_record_usage_writes_token_usage_event():
    """AC-9: record_usage 写入 react_events(event_type='token_usage')。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            recorder = BillingRecorder()
            usage = TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150)
            await recorder.record_usage(
                conn,
                session_id=session_id,
                turn=1,
                model_id="mock",
                usage=usage,
                cost_type="dialogue",
            )
            row = await conn.fetchrow(
                "SELECT event_type, payload FROM react_events WHERE session_id=$1",
                session_id,
            )
            assert row is not None
            assert row["event_type"] == "token_usage"
            payload = json.loads(row["payload"])
            assert payload["model_id"] == "mock"
            assert payload["cost_type"] == "dialogue"
            assert payload["input_tokens"] == 100
            assert payload["output_tokens"] == 50
            assert payload["cached_tokens"] == 0  # 未传 cached 默认 0
        finally:
            await conn.close()

    asyncio.run(_run())


# ── cost_type 写入 ──

def test_record_usage_cost_type_dialogue():
    """dialogue cost_type 写入正确。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            recorder = BillingRecorder()
            await recorder.record_usage(
                conn, session_id=session_id, turn=1, model_id="mock",
                usage=TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150),
                cost_type="dialogue",
            )
            row = await conn.fetchrow(
                "SELECT payload FROM react_events WHERE session_id=$1", session_id
            )
            assert json.loads(row["payload"])["cost_type"] == "dialogue"
        finally:
            await conn.close()

    asyncio.run(_run())


def test_record_usage_cost_type_compress():
    """compress cost_type 写入正确。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
            )
            recorder = BillingRecorder()
            await recorder.record_usage(
                conn, session_id=session_id, turn=1, model_id="mock",
                usage=TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150),
                cost_type="compress",
            )
            row = await conn.fetchrow(
                "SELECT payload FROM react_events WHERE session_id=$1", session_id
            )
            assert json.loads(row["payload"])["cost_type"] == "compress"
        finally:
            await conn.close()

    asyncio.run(_run())