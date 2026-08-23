"""M4 §8.11 + §7.3 + §8.16 eval/repos.py - 四个仓储层(蓝图 §8.4/§8.11/§7.3/§8.16)。

Source: plan/m4-eval-foundation step 4-6 (AC-3, AC-4, AC-5, AC-6)
Source: spec/m4-continuous-evolution §B (AC-2..AC-6)
- EvalDatasetRepo: eval_datasets 表 CRUD,insert 入库前调 validate_expected_trace
- EvalRunRepo: eval_runs 表 CRUD,status 三态(running/completed/failed)
  用 finished_at + metrics.error 联合判断(避免 schema 变更)
- VersionSnapshotRepo: version_snapshots 表 CRUD,scope+version 唯一约束 upsert
- ReviewQueueRepo: 低分案例人工审核队列(JSON 文件存储,MVP 避免新增 DB 表)

mock_mode 字段保留但不用(spec Out of scope),统一用 mock_enabled。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any

import asyncpg

from private_agent.eval.models import (
    EvalSample,
    ExpectedTrace,
    InvalidSampleFormatError,
    validate_expected_trace,
)
from private_agent.observability.logging import setup_logger

__all__ = [
    "EvalDatasetRepo",
    "EvalRunRepo",
    "VersionSnapshotRepo",
    "ReviewQueueRepo",
]


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


# ── ReviewQueueRepo ─────────────────────────────────────────────────────


_VALID_DECISIONS = {"model_limitation_drop", "prompt_defect_edit"}
_INSERT_DECISIONS = {"prompt_defect_edit"}
_INSERT_STATUSES = {"approved", "edited"}


class ReviewQueueRepo:
    """低分案例人工审核队列存储(spec m4-continuous-evolution §B,AC-2..AC-6)。

    MVP 用 JSON 文件存储(避免新增 DB 表),路径: {workspace_root}/.eval_review_queue.json
    V2 可迁移到 DB 表。

    JSON 文件格式:
        {
          "items": [
            {"id": 1, "source_run_id": ..., "sample_input": ..., "status": "pending",
             "created_at": "...", "decided_at": null, "decision": null, ...},
            ...
          ],
          "next_id": 2
        }

    原子写入:写临时文件 + os.replace(防止写一半崩溃导致数据损坏)。

    Args:
        queue_file: JSON 文件路径。
        dataset_repo: EvalDatasetRepo 实例(AC-4 入库需要);None 时无法入库。
    """

    def __init__(
        self,
        *,
        queue_file: str,
        dataset_repo: "EvalDatasetRepo | None" = None,
    ) -> None:
        self._queue_file = queue_file
        self._dataset_repo = dataset_repo
        self._logger = setup_logger("private_agent.eval.review_queue_repo")

    async def add(self, item: dict) -> int:
        """AC-2: 添加审核项,返回 item_id,status='pending'。

        Args:
            item: 审核项字段(source_run_id / sample_input / actual_output /
                actual_events / failure_reason / suggested_as 等)。

        Returns:
            新分配的 item_id(int,从 1 起递增,持久化到文件)。
        """
        data = self._load()
        item_id = data["next_id"]
        new_item = {
            "id": item_id,
            "status": "pending",
            "created_at": _utc_now_iso(),
            "decided_at": None,
            "decision": None,
        }
        # 合并调用方传入的字段(不覆盖 id/status/created_at)
        for k, v in item.items():
            if k not in new_item:
                new_item[k] = v
        data["items"].append(new_item)
        data["next_id"] = item_id + 1
        self._atomic_write(data)
        return item_id

    async def list_pending(self, limit: int = 20) -> list[dict]:
        """AC-3: 列出 status='pending' 的审核项(按 id 升序)。"""
        return self._list(status="pending", limit=limit)

    async def list_all(
        self, status: str | None = None, limit: int = 50
    ) -> list[dict]:
        """列出所有审核项(可按 status 过滤,按 id 升序)。"""
        return self._list(status=status, limit=limit)

    async def update_status(
        self,
        item_id: int,
        *,
        status: str,
        decision: str,
        edited_sample: EvalSample | None = None,
    ) -> None:
        """AC-4/AC-5/AC-6: 更新审核状态。

        Args:
            item_id: 审核项 id。
            status: "approved" | "rejected" | "edited"。
                approved/edited + prompt_defect_edit → 入库
                rejected + model_limitation_drop → 丢弃
            decision: "model_limitation_drop" | "prompt_defect_edit"。
            edited_sample: decision='prompt_defect_edit' 时必填,入库前调
                validate_expected_trace 校验。

        Raises:
            KeyError: item_id 不存在。
            ValueError: decision 非法,或 prompt_defect_edit 缺 edited_sample。
            InvalidSampleFormatError: edited_sample 校验失败(AC-6)。
        """
        if decision not in _VALID_DECISIONS:
            raise ValueError(
                f"非法 decision: {decision},应为 {sorted(_VALID_DECISIONS)}"
            )
        if decision in _INSERT_DECISIONS and edited_sample is None:
            raise ValueError(
                f"decision={decision} 必须提供 edited_sample"
            )
        if status not in _INSERT_STATUSES and status != "rejected":
            raise ValueError(
                f"非法 status: {status},应为 approved/rejected/edited"
            )

        data = self._load()
        target = next((i for i in data["items"] if i["id"] == item_id), None)
        if target is None:
            raise KeyError(f"审核项不存在: item_id={item_id}")

        # AC-6: 入库前校验(在修改队列状态前校验,失败则状态保持 pending)
        if decision in _INSERT_DECISIONS and status in _INSERT_STATUSES:
            assert edited_sample is not None  # 上面已校验
            # 强制 case_type=boundary, split=test(spec §D)
            forced_sample = edited_sample.model_copy(update={
                "case_type": "boundary",
                "split": "test",
            })
            # AC-6: 入库前调 validate_expected_trace
            validate_expected_trace(
                forced_sample.expected_react_trace.model_dump()
            )
            if self._dataset_repo is None:
                raise RuntimeError(
                    "ReviewQueueRepo.dataset_repo 未配置,无法入库 edited_sample"
                )
            await self._dataset_repo.insert(forced_sample)

        # 入库成功(或无需入库)后更新队列状态
        target["status"] = status
        target["decision"] = decision
        target["decided_at"] = _utc_now_iso()
        self._atomic_write(data)

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def _list(self, *, status: str | None, limit: int) -> list[dict]:
        data = self._load()
        items = data["items"]
        if status is not None:
            items = [i for i in items if i["status"] == status]
        # 按 id 升序
        items = sorted(items, key=lambda i: i["id"])
        return list(items[:limit])

    def _load(self) -> dict:
        """加载 JSON 文件,不存在则返回空结构。"""
        if not os.path.exists(self._queue_file):
            return {"items": [], "next_id": 1}
        try:
            with open(self._queue_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                return {"items": [], "next_id": 1}
            data = json.loads(content)
            # 兼容旧文件缺失 next_id 的情况
            if "next_id" not in data:
                data["next_id"] = (
                    max((i["id"] for i in data.get("items", [])), default=0) + 1
                )
            return data
        except (json.JSONDecodeError, OSError) as e:
            self._logger.warning(
                "审核队列文件读取失败,重置为空: %s (%s)", self._queue_file, e
            )
            return {"items": [], "next_id": 1}

    def _atomic_write(self, data: dict) -> None:
        """原子写入:先写临时文件,再 os.replace 覆盖(POSIX 原子,Windows Best-effort)。"""
        parent = os.path.dirname(self._queue_file) or "."
        os.makedirs(parent, exist_ok=True)
        # tempfile 在同目录下生成,确保 replace 在同一文件系统
        fd, tmp_path = tempfile.mkstemp(
            prefix=".eval_review_queue.", suffix=".tmp", dir=parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._queue_file)
        except Exception:
            # 失败时清理临时文件
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise


def _utc_now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串(带时区)。"""
    return datetime.now(timezone.utc).isoformat()
