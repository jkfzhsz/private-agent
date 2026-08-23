"""AI-Agents-in-Depth §2.6 Agent 状态栏 — 纯代码维护的动态元信息注入。

理论依据:
- 上下文学习更像检索而非推理: 模型擅长查找, 不擅长从原始轨迹中归纳统计
  (打了几次电话/是否超约束)。状态栏提前把"算好的结论"注入上下文末尾,
  模型"瞥一眼"即可, 省思考 token 且准确率更高。
- 与 system prompt 的区别: 系统提示词是静态员工手册, 状态栏是贴在屏幕
  边缘的实时仪表盘, 随任务推进不断更新。

三条经验(§2.6.1, 本实现遵循):
1. 状态栏必须用代码维护, 绝不拿 LLM 批量统计(20 行代码 > 大模型逐条总结)。
2. 状态栏是键值对格式(如 "工具调用: web_search x3"), 不是散文——
   散文需要模型先解析, 等于又回到"扫描"。
3. 状态栏信息必须来自代码可观测的真实运行状态, 绝不来自可被外部污染的
   数据源(否则成为"假权威"把模型带偏)。

注入位置: 上下文末尾的 user-role meta 消息(不持久化, 仅内存注入)。
追加到末尾不破坏 KV Cache 前缀(因果注意力: 新 token 只依赖其前 token)。
"""
from __future__ import annotations

import time
from collections import Counter
from typing import Any


class AgentStatusBar:
    """Agent 状态栏: 工具调用计数 + 时间戳 + 当前状态, 渲染为键值对。

    用法:
        bar = AgentStatusBar()
        bar.record_tool_call("web_search")
        bar.record_tool_result("web_search", error=None)
        text = bar.render(state="acting", iteration=2, max_iterations=10)
    """

    def __init__(self) -> None:
        # 工具名 → 调用次数(按调用记录, 非结果, 反映真实执行次数)
        self._tool_counts: Counter[str] = Counter()
        # 工具名 → 失败次数(约束/权限/未知工具等非正常完成)
        self._tool_errors: Counter[str] = Counter()
        self._started_at: float = time.time()

    def record_tool_call(self, tool_name: str) -> None:
        """记录一次工具调用(Phase A 解析后即计数, 含未知工具)。"""
        self._tool_counts[tool_name] += 1

    def record_tool_result(self, tool_name: str, *, error: str | None = None) -> None:
        """记录工具结果; error 非空时计入失败计数。"""
        if error:
            self._tool_errors[tool_name] += 1

    def counts(self) -> dict[str, int]:
        """当前工具调用计数快照(按调用次数降序)。"""
        return dict(
            sorted(self._tool_counts.items(), key=lambda kv: -kv[1])
        )

    def reset(self) -> None:
        """重置状态(新 turn 开始时调用, 避免跨轮累积污染)。"""
        self._tool_counts.clear()
        self._tool_errors.clear()
        self._started_at = time.time()

    def render(
        self,
        *,
        state: str = "idle",
        turn: int = 0,
        iteration: int = 0,
        max_iterations: int = 10,
        workspace: str = "",
        platform: str = "",
    ) -> str:
        """渲染状态栏文本(键值对格式, 追加到上下文末尾的 user 消息)。

        Args:
            state: 当前 ReAct 状态(thinking/acting/observing/idle/error)。
            turn: 当前对话轮次。
            iteration: 当前工具迭代次数。
            max_iterations: 迭代上限。
            workspace: 工作目录(opencode 借鉴: 运行时环境注入, 帮助模型
                理解当前项目上下文)。
            platform: 平台/OS 信息(如 Windows)。

        Returns:
            <agent_status> 包裹的键值对文本。
        """
        lines: list[str] = []
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        lines.append(f"当前时间: {now_str}")
        lines.append(f"对话轮次: 第 {turn} 轮")
        lines.append(f"工具迭代: {iteration}/{max_iterations}")
        lines.append(f"当前状态: {state}")
        if workspace:
            lines.append(f"工作目录: {workspace}")
        if platform:
            lines.append(f"运行平台: {platform}")

        if self._tool_counts:
            parts = []
            for name, cnt in self._tool_counts.items():
                label = name.replace("mcp__", "").replace("_", " ")
                parts.append(f"{label} x{cnt}")
            lines.append(f"工具调用: {', '.join(parts)}")
        else:
            lines.append("工具调用: 无")

        if self._tool_errors:
            err_parts = []
            for name, cnt in self._tool_errors.items():
                label = name.replace("mcp__", "").replace("_", " ")
                err_parts.append(f"{label} x{cnt}")
            lines.append(f"工具失败: {', '.join(err_parts)}")
        else:
            lines.append("工具失败: 无")

        return "<agent_status>\n" + "\n".join(lines) + "\n</agent_status>"


def build_status_bar_message(bar: AgentStatusBar, **kwargs: Any) -> dict:
    """构造状态栏 user 消息(注入上下文末尾, 不进 DB)。

    Returns:
        {"role": "user", "content": <agent_status>...</agent_status>}
    """
    return {"role": "user", "content": bar.render(**kwargs)}
