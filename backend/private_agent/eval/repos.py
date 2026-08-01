"""M4 §8.11 + §7.3 eval/repos.py - 三个仓储层(蓝图 §8.4/§8.11/§7.3)。

Source: plan/m4-eval-foundation step 4-6 (AC-3, AC-4, AC-5, AC-6)
- EvalDatasetRepo: eval_datasets 表 CRUD,insert 入库前调 validate_expected_trace
- EvalRunRepo: eval_runs 表 CRUD,status 三态(running/completed/failed)
  用 finished_at + metrics.error 联合判断(避免 schema 变更)
- VersionSnapshotRepo: version_snapshots 表 CRUD,scope+version 唯一约束 upsert

mock_mode 字段保留但不用(spec Out of scope),统一用 mock_enabled。
"""
from __future__ import annotations

import json
from typing import Any

import asyncpg

from private_agent.eval.models import (
    EvalSample,
    ExpectedTrace,
    InvalidSampleFormatError,
    validate_expected_trace,
)

__all__ = ["EvalDatasetRepo", "EvalRunRepo", "VersionSnapshotRepo"]


def _parse_jsonb(value: Any) -> Any:
    """asyncpg 默认返回 JSONB 为 str,需 json.loads 反序列化。

    若已为 dict/list(注册了 codec)则原样返回;None 保持 None。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


# ── EvalDatasetRepo ─────────────────────────────────────────────────────


class EvalDatasetRepo:
    """eval_datasets 表 CRUD(蓝图 §8.4,AC-3/AC-4)。"""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(self, sample: EvalSample) -> int:
        """插入单条样本,入库前调 validate_expected_trace(AC-3)。

        Args:
            sample: EvalSample 实例。

        Returns:
            新记录的 id。

        Raises:
            InvalidSampleFormatError: expected_react_trace 结构非法时抛出。
        """
        # 入库前校验(AC-3):validate_expected_trace 失败则不入库
        validate_expected_trace(sample.expected_react_trace.model_dump())
        return await self._conn.fetchval(
            """
            INSERT INTO eval_datasets
                (sample_id, scenario, skill_name, skill_version, case_type,
                 difficulty, input, expected_react_trace, expected_output, split)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
            RETURNING id
            """,
            sample.sample_id,
            sample.scenario,
            sample.skill_name,
            sample.skill_version,
            sample.case_type,
            sample.difficulty,
            sample.input,
            sample.expected_react_trace.model_dump_json(),
            sample.expected_output,
            sample.split,
        )

    async def load_test_set(
        self, scenario: str, skill_version: str
    ) -> list[EvalSample]:
        """加载 scenario+skill_version 下 split='test' 的样本(AC-4)。

        Args:
            scenario: 场景名(office/data_analysis/frontend_design)。
            skill_version: Skill 版本。

        Returns:
            EvalSample 列表(空表返回空列表)。
        """
        return await self._load(
            "SELECT sample_id, scenario, skill_name, skill_version, case_type, "
            "difficulty, split, input, expected_react_trace, expected_output "
            "FROM eval_datasets "
            "WHERE scenario=$1 AND skill_version=$2 AND split='test'",
            scenario,
            skill_version,
        )

    async def load_by_split(
        self, scenario: str, split: str
    ) -> list[EvalSample]:
        """加载 scenario+split 的全部样本。"""
        return await self._load(
            "SELECT sample_id, scenario, skill_name, skill_version, case_type, "
            "difficulty, split, input, expected_react_trace, expected_output "
            "FROM eval_datasets "
            "WHERE scenario=$1 AND split=$2",
            scenario,
            split,
        )

    async def get_by_sample_id(self, sample_id: str) -> EvalSample | None:
        """按 sample_id 查询,未命中返回 None。"""
        rows = await self._load(
            "SELECT sample_id, scenario, skill_name, skill_version, case_type, "
            "difficulty, split, input, expected_react_trace, expected_output "
            "FROM eval_datasets WHERE sample_id=$1",
            sample_id,
        )
        return rows[0] if rows else None

    async def _load(self, sql: str, *args: Any) -> list[EvalSample]:
        rows = await self._conn.fetch(sql, *args)
        return [self._row_to_sample(r) for r in rows]

    @staticmethod
    def _row_to_sample(row: asyncpg.Record) -> EvalSample:
        """将 asyncpg 行(含 JSONB expected_react_trace)转换为 EvalSample。"""
        trace_data = _parse_jsonb(row["expected_react_trace"])
        return EvalSample(
            sample_id=row["sample_id"],
            scenario=row["scenario"],
            skill_name=row["skill_name"],
            skill_version=row["skill_version"],
            case_type=row["case_type"],
            difficulty=row["difficulty"],
            split=row["split"],
            input=row["input"],
            expected_react_trace=ExpectedTrace.model_validate(trace_data),
            expected_output=row["expected_output"],
        )


# ── EvalRunRepo ─────────────────────────────────────────────────────────


class EvalRunRepo:
    """eval_runs 表 CRUD(蓝图 §8.11,AC-5)。

    status 三态用 finished_at + metrics.error 联合判断(无 status 列):
    - running: finished_at IS NULL
    - completed: finished_at IS NOT NULL AND metrics ? 'error' IS FALSE
    - failed: finished_at IS NOT NULL AND metrics ? 'error' IS TRUE

    mock_mode 字段保留但不用(spec Out of scope),统一用 mock_enabled。
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create_run(
        self,
        *,
        skill_name: str,
        skill_version: str,
        model_id: str,
        dataset_version: str,
        eval_mode: str,
        mock_enabled: bool,
    ) -> str:
        """创建评估运行,返回 run_id(str)(AC-5)。

        新记录 finished_at IS NULL(隐含 running 状态)。
        """
        return await self._conn.fetchval(
            """
            INSERT INTO eval_runs
                (skill_name, skill_version, model_id, dataset_version,
                 eval_mode, mock_enabled)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING run_id::text
            """,
            skill_name,
            skill_version,
            model_id,
            dataset_version,
            eval_mode,
            mock_enabled,
        )

    async def update_run_metrics(
        self,
        run_id: str,
        metrics: dict,
        sample_results: list[dict],
    ) -> None:
        """更新 run 的 metrics + sample_results(JSONB)。"""
        await self._conn.execute(
            """
            UPDATE eval_runs
            SET metrics = $2::jsonb,
                sample_results = $3::jsonb
            WHERE run_id = $1::uuid
            """,
            run_id,
            json.dumps(metrics),
            json.dumps(sample_results),
        )

    async def complete_run(self, run_id: str) -> None:
        """标记 run 为 completed(finished_at = now())。"""
        await self._conn.execute(
            "UPDATE eval_runs SET finished_at = now() WHERE run_id = $1::uuid",
            run_id,
        )

    async def fail_run(self, run_id: str, error: str) -> None:
        """标记 run 为 failed(finished_at = now() + metrics.error = error)。"""
        await self._conn.execute(
            """
            UPDATE eval_runs
            SET finished_at = now(),
                metrics = COALESCE(metrics, '{}'::jsonb) || jsonb_build_object('error', $2::text)
            WHERE run_id = $1::uuid
            """,
            run_id,
            error,
        )

    async def list_runs(
        self,
        *,
        skill_version: str | None = None,
        model_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """列出 run,支持 skill_version/model_id/status 过滤。

        Args:
            skill_version: 按版本过滤(None 不过滤)。
            model_id: 按模型过滤(None 不过滤)。
            status: running/completed/failed 三态过滤(None 不过滤)。
                running → finished_at IS NULL
                completed → finished_at IS NOT NULL AND NOT metrics ? 'error'
                failed → finished_at IS NOT NULL AND metrics ? 'error'
            limit: 返回条数(默认 20)。
        """
        where_parts: list[str] = []
        args: list[Any] = []
        idx = 1
        if skill_version is not None:
            where_parts.append(f"skill_version = ${idx}")
            args.append(skill_version)
            idx += 1
        if model_id is not None:
            where_parts.append(f"model_id = ${idx}")
            args.append(model_id)
            idx += 1
        if status == "running":
            where_parts.append("finished_at IS NULL")
        elif status == "completed":
            # metrics 可能为 NULL(complete_run 不写 metrics)→ 用 IS NULL OR 兜底
            where_parts.append(
                "finished_at IS NOT NULL AND (metrics IS NULL OR NOT (metrics ? 'error'))"
            )
        elif status == "failed":
            where_parts.append("finished_at IS NOT NULL AND (metrics ? 'error')")

        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
        args.append(limit)
        sql = (
            "SELECT run_id::text, skill_name, skill_version, model_id, "
            "dataset_version, eval_mode, mock_enabled, metrics, sample_results, "
            "started_at, finished_at "
            f"FROM eval_runs{where_sql} "
            f"ORDER BY started_at DESC LIMIT ${idx}"
        )
        rows = await self._conn.fetch(sql, *args)
        return [self._row_to_run_dict(r) for r in rows]

    async def get_run(self, run_id: str) -> dict | None:
        """按 run_id 查询,未命中返回 None。"""
        row = await self._conn.fetchrow(
            "SELECT run_id::text, skill_name, skill_version, model_id, "
            "dataset_version, eval_mode, mock_enabled, metrics, sample_results, "
            "started_at, finished_at "
            "FROM eval_runs WHERE run_id = $1::uuid",
            run_id,
        )
        return self._row_to_run_dict(row) if row else None

    @staticmethod
    def _row_to_run_dict(row: asyncpg.Record) -> dict:
        """将 asyncpg 行转换为 dict,JSONB 字段(metrics/sample_results)反序列化。"""
        return {
            "run_id": row["run_id"],
            "skill_name": row["skill_name"],
            "skill_version": row["skill_version"],
            "model_id": row["model_id"],
            "dataset_version": row["dataset_version"],
            "eval_mode": row["eval_mode"],
            "mock_enabled": row["mock_enabled"],
            "metrics": _parse_jsonb(row["metrics"]),
            "sample_results": _parse_jsonb(row["sample_results"]),
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    async def get_low_score_samples(
        self, threshold: float = 0.6, limit: int = 50
    ) -> list[dict]:
        """查询 sample_results 中 completion_rate < threshold 的样本(§8.16 复用)。

        sample_results JSONB 结构假设(与 m4-eval-runner-replay spec 锁定):
            [{"sample_id": str, "metrics": {"task_completion": {"completion_rate": float}}}, ...]
        """
        rows = await self._conn.fetch(
            """
            SELECT elem->>'sample_id' AS sample_id,
                   (elem->'metrics'->'task_completion'->>'completion_rate')::float AS completion_rate
            FROM eval_runs,
                 jsonb_array_elements(sample_results) AS elem
            WHERE (elem->'metrics'->'task_completion'->>'completion_rate')::float < $1
            LIMIT $2
            """,
            threshold,
            limit,
        )
        return [dict(r) for r in rows]


# ── VersionSnapshotRepo ─────────────────────────────────────────────────


class VersionSnapshotRepo:
    """version_snapshots 表 CRUD(蓝图 §7.3,AC-6)。

    scope 枚举:prompt/skill/harness/config/kb(与 schema.sql CHECK 约束一致)。
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def save(self, *, scope: str, version: str, payload: dict) -> int:
        """upsert scope+version 的 payload(AC-6)。

        Returns:
            记录 id。
        """
        return await self._conn.fetchval(
            """
            INSERT INTO version_snapshots (scope, version, payload)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (scope, version) DO UPDATE
                SET payload = EXCLUDED.payload
            RETURNING id
            """,
            scope,
            version,
            json.dumps(payload),
        )

    async def get(self, *, scope: str, version: str) -> dict | None:
        """按 scope+version 查询 payload,未命中返回 None。"""
        row = await self._conn.fetchrow(
            "SELECT payload FROM version_snapshots WHERE scope=$1 AND version=$2",
            scope,
            version,
        )
        if row is None:
            return None
        return dict(_parse_jsonb(row["payload"]))

    async def list_by_scope(self, scope: str, limit: int = 20) -> list[dict]:
        """按 scope 列出最近记录(按 created_at DESC)。"""
        rows = await self._conn.fetch(
            """
            SELECT id, scope, version, payload, created_at
            FROM version_snapshots
            WHERE scope = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            scope,
            limit,
        )
        return [
            {
                "id": r["id"],
                "scope": r["scope"],
                "version": r["version"],
                "payload": dict(_parse_jsonb(r["payload"])),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    async def get_latest(self, scope: str) -> dict | None:
        """返回 scope 下最新一条记录(按 created_at DESC LIMIT 1)。

        Args:
            scope: prompt/skill/harness/config/kb。
        """
        row = await self._conn.fetchrow(
            """
            SELECT id, scope, version, payload, created_at
            FROM version_snapshots
            WHERE scope = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            scope,
        )
        if row is None:
            return None
        return {
            "id": row["id"],
            "scope": row["scope"],
            "version": row["version"],
            "payload": dict(_parse_jsonb(row["payload"])),
            "created_at": row["created_at"],
        }
