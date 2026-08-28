"""0.6.0 D-2: MissionSupervisor —— 监督轮(失败触发的单次 LLM 纠偏判定)。

设计文档: docs/next-phase-plan-2026-08-28-wuya-long-task-orchestration.md §4.4
实施裁剪(v4 设计为"一次性 ReactLoop"; 实施确认**单次 chat 足够**):
- 监督判定 = 合成摘要(charter+journal 尾部+plan 状态+失败详情) → 单次模型调用
  → JSON 裁决 {action, reason} —— 无工具循环需求(纠偏动作由 MissionRunner
  确定性执行, 监督者无副作用能力, 天然满足"不可逆动作永不自动执行");
- 相比 ReactLoop: 省多轮工具循环 token、无 frozen hash 参与、无权限面;
- 定时触发: runner 已 2s 轮询 mission state(纯等待不调模型, §4.4.1 一致),
  自适应间隔服务于"运行中观察节奏", 模型监督只在失败事件触发(事件驱动)。

判定三问(§4.3 第三道防线, 写入监督 system prompt):
①当前动作与 goal 是否一致? ②改道次数/预算消耗? ③是否该 escalate?
裁决枚举: redelegate(方向正确, 执行失败 → 换法重派) / wait_user(方向存疑或
超能力范围 → 保持 escalated 等用户)。

JSON 解析容错: 模型输出含 ```json 包裹/前后噪声时提取首个 {} 块; 解析失败
保守回退 wait_user(不误自动纠偏)。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from private_agent.observability.logging import setup_logger

__all__ = [
    "SUPERVISOR_SYSTEM_PROMPT",
    "build_supervision_user_prompt",
    "parse_supervision_decision",
    "supervise_failure",
]

logger = setup_logger("private_agent.mission_supervisor")

SUPERVISOR_SYSTEM_PROMPT = (
    "你是长任务监督者。任务执行中某个里程碑失败, 你基于任务宪章与执行台账"
    "判断下一步处置。只回答一个 JSON 对象, 不输出其他内容:\n"
    '{"action": "redelegate" | "wait_user", "reason": "<≤200字判定依据>"}\n'
    "判定规则:\n"
    "1. [方向一致] 失败原因属执行层(网络/环境/工具临时故障), 且当前里程碑"
    "仍是达成目标的正确路径 → action=redelegate(换可行方式重试同一里程碑);\n"
    "2. [方向存疑] 失败暴露里程碑本身偏离任务目标 / 里程碑与目标无交集 / "
    "需要的资源(权限/依赖/数据)超出子代理能力 → action=wait_user(等用户裁决);\n"
    "3. 判断依据优先级: 任务目标 > 完成标准 > 硬约束 > 台账历史。"
)


def build_supervision_user_prompt(
    charter: dict,
    plan: list,
    journal: list,
    failed_ms: dict,
    detail: str,
) -> str:
    """合成监督输入(限长: journal 尾 10 条, 各字段截断)。"""
    lines = [
        "[任务宪章]",
        f"目标: {str(charter.get('goal', '-'))[:200]}",
    ]
    if charter.get("dod"):
        lines.append("完成标准: " + "; ".join(str(d) for d in charter["dod"])[:300])
    if charter.get("constraints"):
        lines.append("硬约束: " + "; ".join(str(c) for c in charter["constraints"])[:300])
    lines.append("\n[里程碑计划与状态]")
    for ms in plan:
        if isinstance(ms, dict):
            lines.append(
                f"- [{ms.get('id')}] {str(ms.get('milestone', '-'))[:80]} "
                f"({ms.get('executor_type')}, status={ms.get('status', 'pending')})"
            )
    lines.append("\n[执行台账尾部]")
    for e in journal[-10:]:
        if isinstance(e, dict):
            lines.append(f"- [{e.get('kind')}] {str(e.get('detail', ''))[:120]}")
    lines.append("\n[本次失败]")
    lines.append(f"里程碑: [{failed_ms.get('id')}] {failed_ms.get('milestone', '-')}")
    lines.append(f"失败详情: {detail[:400]}")
    lines.append("\n请给出 JSON 裁决。")
    return "\n".join(lines)


def parse_supervision_decision(raw: str) -> dict:
    """解析监督裁决(容错: 提取首个 JSON 对象; 非法 → wait_user 保守回退)。"""
    fallback = {"action": "wait_user", "reason": "监督裁决解析失败, 保守等用户"}
    if not raw:
        return fallback
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return fallback
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return fallback
    action = data.get("action")
    if action not in ("redelegate", "wait_user"):
        return fallback
    return {"action": action, "reason": str(data.get("reason", ""))[:300]}


async def supervise_failure(
    adapter: Any,
    charter: dict,
    plan: list,
    journal: list,
    failed_ms: dict,
    detail: str,
    max_tokens: int = 600,
) -> dict:
    """失败监督判定入口(单次 chat; 异常/超时 → wait_user 保守回退)。

    Args:
        adapter: 模型适配器(adapter_factory 产物; chat(messages) -> ChatResult)。
        其余参数同 build_supervision_user_prompt。

    Returns:
        {"action": "redelegate"|"wait_user", "reason": str}
    """
    messages = [
        {"role": "system", "content": SUPERVISOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": build_supervision_user_prompt(
                charter, plan, journal, failed_ms, detail
            ),
        },
    ]
    try:
        result = await adapter.chat(messages, max_tokens=max_tokens)
        decision = parse_supervision_decision(getattr(result, "content", "") or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("supervision chat failed (%s) -> wait_user", type(e).__name__)
        decision = {"action": "wait_user", "reason": f"监督调用异常: {e}"}
    logger.info(
        "supervision decision: action=%s reason=%s",
        decision["action"], decision["reason"][:80],
    )
    return decision
