"""在线失败案例采集器 - 打通在线对话与离线评估闭环。

对应参考文档 EvoSkill Executor Agent：
"拿当前 Skill 库去跑任务，把失败案例完整记录下来"。

将在线对话中的失败案例（工具失败/迭代用尽/用户纠正/模型调用失败）
自动写入 M4 评估闭环的 ReviewQueueRepo，供人工审核后扩充评估数据集。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from private_agent.observability.logging import setup_logger

logger = setup_logger(__name__)


class FailureType(str, Enum):
    """失败类型分类。"""

    TOOL_ERROR = "tool_error"  # 工具执行失败
    ITERATION_EXHAUSTED = "iteration_exhausted"  # 迭代用尽
    USER_CORRECTION = "user_correction"  # 用户纠正
    PROVIDER_ERROR = "provider_error"  # 模型调用失败
    CONTEXT_OVERFLOW = "context_overflow"  # 上下文超限


@dataclass
class _DedupKey:
    """去重键：同一会话+同一失败类型 5 分钟内去重。"""

    session_id: int
    failure_type: FailureType

    def __hash__(self) -> int:
        return hash((self.session_id, self.failure_type))


class OnlineFailureCollector:
    """在线失败案例采集器。

    进程内维护 _recent 去重表：同一 session_id + failure_type 在
    _DEDUP_WINDOW_SEC 窗口内只采集一次，避免重复失败刷屏审核队列。
    """

    _DEDUP_WINDOW_SEC = 300  # 5 分钟去重窗口

    def __init__(self, review_queue_repo: Any) -> None:
        self._review_queue = review_queue_repo
        self._recent: dict[_DedupKey, float] = {}  # key -> last_collect_ts

    async def collect(
        self,
        session_id: int,
        scope: str | None,
        user_message: str,
        failure_type: FailureType,
        failure_detail: str,
        react_events: list[dict[str, Any]],
        final_output: str,
    ) -> int | None:
        """采集一个失败案例，写入审核队列。

        Returns:
            写入成功返回 item_id；去重跳过或写入异常返回 None
            (采集失败不阻塞对话主流程)。
        """
        key = _DedupKey(session_id=session_id, failure_type=failure_type)
        now = time.time()
        last_ts = self._recent.get(key)
        if last_ts is not None and (now - last_ts) < self._DEDUP_WINDOW_SEC:
            logger.debug(
                "failure_deduped session=%s type=%s", session_id, failure_type
            )
            return None
        self._recent[key] = now

        item = {
            "source_run_id": None,  # 在线案例无 eval_run_id
            "source_session_id": session_id,
            "scope": scope,
            "sample_input": user_message[:1000],
            "actual_output": final_output[:1000],
            "actual_events": self._summarize_events(react_events),
            "failure_reason": f"[{failure_type.value}] {failure_detail}",
            "failure_type": failure_type.value,
            "suggested_as": "boundary",
            "status": "pending",
        }

        try:
            item_id = await self._review_queue.add(item)
            logger.info(
                "failure_collected session=%s type=%s item_id=%s",
                session_id, failure_type, item_id,
            )
            return item_id
        except Exception as e:  # noqa: BLE001
            logger.warning("failure_collect_failed error=%s", e)
            return None

    @staticmethod
    def _summarize_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """摘要 react_events（避免全量存储，每条取关键字段）。

        生产事件 payload 键: tool_call → payload.tool_name(2026-08-11
        Phase 1 P0 修复后统一键名, 勿回退到 "tool")。
        """
        summary: list[dict[str, Any]] = []
        for ev in events[:20]:  # 最多存 20 条
            summary.append({
                "event_type": ev.get("event_type", ""),
                "turn": ev.get("turn", 0),
                "tool_name": ev.get("payload", {}).get("tool_name", ""),
            })
        return summary
