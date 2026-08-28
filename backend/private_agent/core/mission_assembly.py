"""0.6.0 F1-6: mission 子代理工具白名单装配 + 进程级 mission 并发注册表。

设计文档: docs/next-phase-plan-2026-08-28-wuya-long-task-orchestration.md §4.5
一、工具白名单: 子代理工具集从"继承父全集"收紧为按任务类型装配 ——
    防漂移(search 类乱改文件) + 防误用(analysis 类乱上网) + 防越权
    (任何子代理不得触碰编排/监控/进化/评估工具)。
    规则 = 显式排除(全类型 + 按类型) + MCP 前缀保守允许清单(未列前缀默认禁止)。
二、MissionRegistry: 进程级 running mission 并发计数(acquire/release) ——
    与 F1-2 mission_create 的 DB 级检查互为防线(§八裁决 3: 默认 2);
    模式同 SubagentTypeRegistry(2026-08-13, 已验收)。
"""
from __future__ import annotations

import asyncio

__all__ = [
    "MISSION_EXCLUDED_ALL",
    "MISSION_EXCLUDED_BY_TYPE",
    "MISSION_MCP_ALLOWED_PREFIXES",
    "filter_tools_for_task_type",
    "mission_registry",
    "MissionRegistry",
]

# ── 一、工具白名单 ──────────────────────────────────────────────────────

# 全类型显式排除: 编排(防嵌套委派/越权创建任务) + 监控 + 进化 + 评估 + 记忆写入
# (记忆写入仅限主对话; 压缩工具子代理无意义)
MISSION_EXCLUDED_ALL: frozenset[str] = frozenset({
    # 编排(嵌套深度恒 1 的硬保障)
    "delegate_subtask", "mission_create", "mission_status", "mission_control",
    # 监控/系统(monitor 会话专属)
    "system_metrics_query", "system_status", "optim_plan", "apply_optim",
    "subagent_status", "session_events",
    # 自进化/评估/技能管理(主对话专属)
    "compress_now", "evolution_tools", "skill_manager", "eval_runner",
    "search_lessons",
    # 记忆写入(防子代理污染用户记忆; SubagentRunner 已置 memory_manager=None 双保险)
    "memory_save",
})

# 按类型显式排除(§4.5 表; http_request 与 web_search 同为出网面, 随 web_search 同步排除)
MISSION_EXCLUDED_BY_TYPE: dict[str, frozenset[str]] = {
    "search": frozenset({"file_write", "code_execution"}),
    "analysis": frozenset({"file_write", "web_search", "http_request"}),
    "code": frozenset({"web_search", "http_request"}),
}

# MCP 前缀保守允许清单(未列前缀默认禁止 —— mempalace 等默认不进子代理;
# 需要扩列时改此处 + 单测, 不做静默放行)
MISSION_MCP_ALLOWED_PREFIXES: dict[str, tuple[str, ...]] = {
    "search": ("mcp__Searchpin__",),
    "analysis": ("mcp__hexin-", "mcp__qcc"),  # iFind 全家(金融/宏观/企业数据)
    "code": (),
}


def filter_tools_for_task_type(tools: list, task_type: str) -> list:
    """按任务类型过滤子代理工具集(subagents.task_type: search/analysis/code/other)。

    - other: 仅排除全类型禁区(保守不过滤功能面);
    - 命中 MISSION_EXCLUDED_ALL / MISSION_EXCLUDED_BY_TYPE[type] → 剔除;
    - MCP 工具(mcp__ 前缀): 仅允许 MISSION_MCP_ALLOWED_PREFIXES[type] 内前缀;
    - 返回原 ToolDef 对象列表(不过滤入参列表本身)。
    """
    excluded = set(MISSION_EXCLUDED_ALL) | set(
        MISSION_EXCLUDED_BY_TYPE.get(task_type, frozenset())
    )
    allowed_mcp = MISSION_MCP_ALLOWED_PREFIXES.get(task_type, ())
    out: list = []
    for t in tools:
        name = getattr(t, "name", None) or (
            t.get("name") if isinstance(t, dict) else None
        )
        if not name or name in excluded:
            continue
        if name.startswith("mcp__"):
            if not any(name.startswith(p) for p in allowed_mcp):
                continue
        out.append(t)
    return out


# ── 二、进程级 mission 并发注册表 ────────────────────────────────────────

class MissionRegistry:
    """进程级 running mission 计数(跨会话/跨轮)。

    语义(同 SubagentTypeRegistry):
    - D-1 MissionRunner.spawn 时 acquire(超限等待, 默认 30s 超时);
    - 终态(done/failed/cancelled/escalated)时 release;
    - 进程重启计数归零(重启恢复路径由 startup 扫描重建, D-1)。
    """

    def __init__(self, max_running: int = 2):
        self._max = max_running
        self._count = 0
        self._cond = asyncio.Condition()

    async def acquire(self, timeout_sec: float = 30.0) -> bool:
        """获取一个 mission 并发名额; 超时返回 False(调用方拒绝创建)。

        轮询实现(0.2s): release 必须可在 task done_callback(同步上下文)中
        调用 —— Condition.wait/notify 需要 async 上下文, 在"task 未被调度即被
        取消"场景(协程体未执行, finally 不跑)无法保证释放; done_callback 是
        task 终结的唯一可靠释放点(并发上限 2, 轮询成本可忽略)。
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_sec
        while self._count >= self._max:
            if loop.time() > deadline:
                return False
            await asyncio.sleep(0.2)
        self._count += 1
        return True

    def release(self) -> None:
        """释放名额(mission 终态; 同步安全 —— 供 task done_callback 调用;
        幂等: count 不为负)。"""
        self._count = max(0, self._count - 1)

    def running(self) -> int:
        return self._count

    def set_max(self, max_running: int) -> None:
        self._max = max_running


mission_registry = MissionRegistry(max_running=2)
