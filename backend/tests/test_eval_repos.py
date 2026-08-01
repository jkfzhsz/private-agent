"""M4 §8.11 + §7.3 eval/repos.py - 三个仓储层测试。

Source: plan/m4-eval-foundation step 16 (AC-3, AC-4, AC-5, AC-6)
- EvalDatasetRepo: insert(合法+非法) / load_test_set / load_by_split / get_by_sample_id
- EvalRunRepo: create_run / update_run_metrics / complete_run / fail_run / list_runs / get_run / get_low_score_samples
- VersionSnapshotRepo: save / get / list_by_scope / get_latest

依赖: 真实 PostgreSQL(TEST_DSN)
"""
from __future__ import annotations

import os

import asyncpg
import pytest

from private_agent.eval.models import (
    EvalSample,
    ExpectedToolCall,
    ExpectedTrace,
    InvalidSampleFormatError,
)
from private_agent.eval.repos import (
    EvalDatasetRepo,
    EvalRunRepo,
    VersionSnapshotRepo,
)
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


async def _setup_schema() -> None:
    """重建 schema + 跑 migrate_all。"""
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
    finally:
        await conn.close()


@pytest.fixture
async def conn():
    await _setup_schema()
    c = await asyncpg.connect(TEST_DSN)
    try:
        yield c
    finally:
        await c.close()


def _make_sample(
    sample_id: str = "office_001_normal",
    scenario: str = "office",
    skill_version: str = "1.0.0",
    split: str = "test",
    case_type: str = "normal",
) -> EvalSample:
    return EvalSample(
        sample_id=sample_id,
        scenario=scenario,
        skill_name=scenario,
        skill_version=skill_version,
        case_type=case_type,  # type: ignore[arg-type]
        difficulty="easy",
        split=split,  # type: ignore[arg-type]
        input="test input",
        expected_react_trace=ExpectedTrace(
            tool_calls=[ExpectedToolCall(tool="calculator", args={"expr": "1+1"})],
            expected_output_contains=["2"],
        ),
        expected_output="2",
    )


# ── EvalDatasetRepo ─────────────────────────────────────────────────────


async def test_eval_dataset_repo_insert_returns_id(conn: asyncpg.Connection):
    """AC-3: 合法样本入库,返回 id(int > 0)。"""
    repo = EvalDatasetRepo(conn)
    sid = await repo.insert(_make_sample())
    assert isinstance(sid, int)
    assert sid > 0


async def test_eval_dataset_repo_insert_invalid_raises(conn: asyncpg.Connection, monkeypatch):
    """AC-3: validate_expected_trace 抛 InvalidSampleFormatError 时 insert 不入库并向上抛。"""
    repo = EvalDatasetRepo(conn)
    sample = _make_sample()

    def _raise_invalid(_trace: dict) -> ExpectedTrace:
        raise InvalidSampleFormatError("mocked invalid")

    # Patch repos 模块内的 validate_expected_trace,断言 insert 真的调用了校验入口
    from private_agent.eval import repos as repos_mod
    monkeypatch.setattr(repos_mod, "validate_expected_trace", _raise_invalid)

    with pytest.raises(InvalidSampleFormatError):
        await repo.insert(sample)
    # 验证未入库
    count = await conn.fetchval("SELECT COUNT(*) FROM eval_datasets")
    assert count == 0


async def test_eval_dataset_repo_load_test_set_empty(conn: asyncpg.Connection):
    """AC-4: 空表 load_test_set 返回空列表。"""
    repo = EvalDatasetRepo(conn)
    result = await repo.load_test_set(scenario="office", skill_version="1.0.0")
    assert result == []


async def test_eval_dataset_repo_load_test_set_returns_samples(
    conn: asyncpg.Connection,
):
    """AC-4: load_test_set 返回 list[EvalSample],过滤 scenario+skill_version+split='test'。"""
    repo = EvalDatasetRepo(conn)
    await repo.insert(_make_sample("s1", scenario="office", skill_version="1.0.0", split="test"))
    await repo.insert(_make_sample("s2", scenario="office", skill_version="1.0.0", split="test"))
    # train 样本不应被 load_test_set 返回
    await repo.insert(_make_sample("s3", scenario="office", skill_version="1.0.0", split="train"))
    # 其他 scenario 不应被返回
    await repo.insert(_make_sample("s4", scenario="data", skill_version="1.0.0", split="test"))

    result = await repo.load_test_set(scenario="office", skill_version="1.0.0")
    assert len(result) == 2
    assert {s.sample_id for s in result} == {"s1", "s2"}
    assert all(isinstance(s, EvalSample) for s in result)


async def test_eval_dataset_repo_load_by_split(conn: asyncpg.Connection):
    """load_by_split 按 scenario + split 过滤。"""
    repo = EvalDatasetRepo(conn)
    await repo.insert(_make_sample("s1", scenario="office", split="test"))
    await repo.insert(_make_sample("s2", scenario="office", split="train"))
    await repo.insert(_make_sample("s3", scenario="office", split="train"))

    train = await repo.load_by_split(scenario="office", split="train")
    test = await repo.load_by_split(scenario="office", split="test")
    assert {s.sample_id for s in train} == {"s2", "s3"}
    assert {s.sample_id for s in test} == {"s1"}


async def test_eval_dataset_repo_get_by_sample_id(conn: asyncpg.Connection):
    """get_by_sample_id 命中返回 EvalSample,未命中返回 None。"""
    repo = EvalDatasetRepo(conn)
    await repo.insert(_make_sample("find-me"))
    found = await repo.get_by_sample_id("find-me")
    missing = await repo.get_by_sample_id("nope")
    assert found is not None
    assert found.sample_id == "find-me"
    assert missing is None


# ── EvalRunRepo ─────────────────────────────────────────────────────────


async def test_eval_run_repo_create_run_returns_run_id(conn: asyncpg.Connection):
    """AC-5: create_run 返回 run_id(str),eval_runs 记录 finished_at IS NULL(隐含 running)。"""
    repo = EvalRunRepo(conn)
    run_id = await repo.create_run(
        skill_name="office",
        skill_version="1.0.0",
        model_id="glm-4-flash",
        dataset_version="20260801",
        eval_mode="offline",
        mock_enabled=False,
    )
    assert isinstance(run_id, str)
    assert len(run_id) > 0
    # DB 中 finished_at 应为 NULL(running 状态)
    row = await conn.fetchrow("SELECT finished_at, mock_enabled FROM eval_runs WHERE run_id = $1", run_id)
    assert row is not None
    assert row["finished_at"] is None
    assert row["mock_enabled"] is False


async def test_eval_run_repo_complete_run(conn: asyncpg.Connection):
    """complete_run 设置 finished_at(状态变 completed)。"""
    repo = EvalRunRepo(conn)
    run_id = await repo.create_run(
        skill_name="office", skill_version="1.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    await repo.complete_run(run_id)
    row = await conn.fetchrow("SELECT finished_at FROM eval_runs WHERE run_id = $1", run_id)
    assert row is not None
    assert row["finished_at"] is not None


async def test_eval_run_repo_fail_run_sets_error(conn: asyncpg.Connection):
    """fail_run 设置 finished_at + metrics 含 error 键。"""
    repo = EvalRunRepo(conn)
    run_id = await repo.create_run(
        skill_name="office", skill_version="1.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    await repo.fail_run(run_id, "boom")
    got = await repo.get_run(run_id)
    assert got is not None
    assert got["finished_at"] is not None
    assert got["metrics"] is not None
    assert got["metrics"]["error"] == "boom"


async def test_eval_run_repo_update_run_metrics(conn: asyncpg.Connection):
    """update_run_metrics 写入 metrics + sample_results。"""
    repo = EvalRunRepo(conn)
    run_id = await repo.create_run(
        skill_name="office", skill_version="1.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    await repo.update_run_metrics(
        run_id,
        metrics={"task_completion": {"completion_rate": 0.8}},
        sample_results=[{"sample_id": "s1", "score": 0.8}],
    )
    got = await repo.get_run(run_id)
    assert got is not None
    assert got["metrics"]["task_completion"]["completion_rate"] == 0.8
    assert got["sample_results"][0]["sample_id"] == "s1"


async def test_eval_run_repo_list_runs_status_running(conn: asyncpg.Connection):
    """list_runs(status='running') 返回 finished_at IS NULL 的 run。"""
    repo = EvalRunRepo(conn)
    rid1 = await repo.create_run(
        skill_name="office", skill_version="1.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    rid2 = await repo.create_run(
        skill_name="office", skill_version="1.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    await repo.complete_run(rid2)

    running = await repo.list_runs(status="running")
    running_ids = {r["run_id"] for r in running}
    assert rid1 in running_ids
    assert rid2 not in running_ids


async def test_eval_run_repo_list_runs_status_completed(conn: asyncpg.Connection):
    """list_runs(status='completed') 返回 finished_at IS NOT NULL 且无 error 的 run。"""
    repo = EvalRunRepo(conn)
    rid1 = await repo.create_run(
        skill_name="office", skill_version="1.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    rid2 = await repo.create_run(
        skill_name="office", skill_version="1.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    await repo.complete_run(rid1)
    await repo.fail_run(rid2, "boom")

    completed = await repo.list_runs(status="completed")
    completed_ids = {r["run_id"] for r in completed}
    assert rid1 in completed_ids
    assert rid2 not in completed_ids


async def test_eval_run_repo_list_runs_status_failed(conn: asyncpg.Connection):
    """list_runs(status='failed') 返回 finished_at IS NOT NULL 且 metrics ? 'error' 的 run。"""
    repo = EvalRunRepo(conn)
    rid1 = await repo.create_run(
        skill_name="office", skill_version="1.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    rid2 = await repo.create_run(
        skill_name="office", skill_version="1.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    await repo.complete_run(rid1)
    await repo.fail_run(rid2, "boom")

    failed = await repo.list_runs(status="failed")
    failed_ids = {r["run_id"] for r in failed}
    assert rid2 in failed_ids
    assert rid1 not in failed_ids


async def test_eval_run_repo_list_runs_filter_skill_version(conn: asyncpg.Connection):
    """list_runs 按 skill_version 过滤。"""
    repo = EvalRunRepo(conn)
    rid1 = await repo.create_run(
        skill_name="office", skill_version="1.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    await repo.create_run(
        skill_name="office", skill_version="2.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    result = await repo.list_runs(skill_version="1.0.0")
    assert len(result) == 1
    assert result[0]["run_id"] == rid1


async def test_eval_run_repo_get_low_score_samples(conn: asyncpg.Connection):
    """get_low_score_samples 返回 completion_rate < threshold 的样本(§8.16 复用)。"""
    repo = EvalRunRepo(conn)
    run_id = await repo.create_run(
        skill_name="office", skill_version="1.0.0", model_id="m",
        dataset_version="v1", eval_mode="offline", mock_enabled=False,
    )
    await repo.update_run_metrics(
        run_id,
        metrics={"task_completion": {"completion_rate": 0.4}},
        sample_results=[
            {"sample_id": "low1", "metrics": {"task_completion": {"completion_rate": 0.3}}},
            {"sample_id": "high1", "metrics": {"task_completion": {"completion_rate": 0.9}}},
        ],
    )
    result = await repo.get_low_score_samples(threshold=0.6)
    assert len(result) == 1
    assert result[0]["sample_id"] == "low1"


async def test_eval_run_repo_get_run_missing(conn: asyncpg.Connection):
    """get_run 未命中返回 None。"""
    repo = EvalRunRepo(conn)
    got = await repo.get_run("00000000-0000-0000-0000-000000000000")
    assert got is None


# ── VersionSnapshotRepo ─────────────────────────────────────────────────


async def test_version_snapshot_repo_save_get_roundtrip(conn: asyncpg.Connection):
    """AC-6: save + get 读写一致。"""
    repo = VersionSnapshotRepo(conn)
    payload = {"system_prompt": "you are an assistant", "version": "1.0.0"}
    await repo.save(scope="prompt", version="v1", payload=payload)
    got = await repo.get(scope="prompt", version="v1")
    assert got is not None
    assert got == payload


async def test_version_snapshot_repo_save_upsert(conn: asyncpg.Connection):
    """save 同 scope+version 重复保存为 upsert(ON CONFLICT 更新 payload)。"""
    repo = VersionSnapshotRepo(conn)
    await repo.save(scope="prompt", version="v1", payload={"k": "old"})
    await repo.save(scope="prompt", version="v1", payload={"k": "new"})
    got = await repo.get(scope="prompt", version="v1")
    assert got == {"k": "new"}
    # 表中应只有 1 行
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM version_snapshots WHERE scope=$1 AND version=$2",
        "prompt", "v1",
    )
    assert count == 1


async def test_version_snapshot_repo_get_missing(conn: asyncpg.Connection):
    """get 未命中返回 None。"""
    repo = VersionSnapshotRepo(conn)
    got = await repo.get(scope="prompt", version="nope")
    assert got is None


async def test_version_snapshot_repo_list_by_scope(conn: asyncpg.Connection):
    """list_by_scope 按 scope 过滤,按 created_at DESC 排序。"""
    repo = VersionSnapshotRepo(conn)
    await repo.save(scope="prompt", version="v1", payload={"i": 1})
    await repo.save(scope="prompt", version="v2", payload={"i": 2})
    await repo.save(scope="skill", version="s1", payload={"i": 3})

    result = await repo.list_by_scope(scope="prompt")
    assert len(result) == 2
    versions = [r["version"] for r in result]
    # 最新创建的在前(v2 后保存)
    assert versions[0] == "v2"
    assert versions[1] == "v1"


async def test_version_snapshot_repo_get_latest(conn: asyncpg.Connection):
    """get_latest 返回 scope 下最新的一条(按 created_at DESC)。"""
    repo = VersionSnapshotRepo(conn)
    await repo.save(scope="prompt", version="v1", payload={"i": 1})
    await repo.save(scope="prompt", version="v2", payload={"i": 2})

    latest = await repo.get_latest(scope="prompt")
    assert latest is not None
    assert latest["version"] == "v2"


async def test_version_snapshot_repo_get_latest_empty(conn: asyncpg.Connection):
    """get_latest scope 无记录返回 None。"""
    repo = VersionSnapshotRepo(conn)
    latest = await repo.get_latest(scope="nope")
    assert latest is None
