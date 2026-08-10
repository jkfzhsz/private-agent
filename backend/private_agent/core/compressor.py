"""蓝图 §3.9/§3.10 上下文压缩 — 三类策略(滑动窗口/摘要/Stable Zone 合并)。

B4 P0-1: 检查触发条件(token 超限/轮次超限),执行压缩,写入 compress 事件。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from private_agent.core.token_estimator import TokenEstimator

if TYPE_CHECKING:
    import asyncpg


class Compressor:
    """上下文压缩器(蓝图 §3.9/§3.10)。

    三类策略:
    1. 滑动窗口: 保留最近 keep_turns 轮,旧消息标记 compressed=True
    2. 摘要: 调 compress_adapter 生成摘要消息
    3. Stable Zone 合并: 每 5 轮或 >20 条时合并(留 V2)
    """

    def __init__(self) -> None:
        self._estimator = TokenEstimator()

    def maybe_compress(
        self,
        messages: list[dict],
        *,
        active_turns: int,
        context_window: int,
        compress_adapter: Any = None,
    ) -> bool:
        if active_turns > 10:
            return True
        tokens = self._estimator.estimate_messages(messages)
        if tokens > context_window * 0.8:
            return True
        return False

    # ── §3.10.3 [MVP] Stable Zone 合并压缩 ──────────────────────────────

    @staticmethod
    def should_merge_stable(
        turn: int,
        kb_count: int,
        merge_interval: int = 5,
        kb_threshold: int = 20,
    ) -> bool:
        """Stable Zone 合并触发判断(蓝图 §3.10.3)。

        条件 1: 每 N 轮(默认 N=5)触发一次, 且当前有 KB 片段;
        条件 2: KB 片段数超过阈值(默认 20 条)。
        """
        if kb_count <= 0:
            return False
        if turn > 0 and turn % merge_interval == 0:
            return True
        if kb_count > kb_threshold:
            return True
        return False

    @staticmethod
    def build_merge_prompt(stable_msgs: list[dict]) -> str:
        """构造 Stable Zone 合并摘要 prompt(蓝图 §3.10.3 _build_merge_prompt)。

        将多个 KB 检索片段合并为单一"知识摘要", 保留关键事实与来源。
        """
        lines = [
            "请将以下多次知识库检索片段合并为一份精炼的知识摘要。",
            "保留关键事实、数据与结论, 去除重复内容; 若片段间有矛盾请标注。",
            "",
        ]
        for i, m in enumerate(stable_msgs, 1):
            content = (m.get("content") or "").replace("[KB Context]", "").strip()
            lines.append(f"[片段{i}]\n{content[:1500]}")
        lines.append("")
        lines.append("输出: 以 '[Merged KB Context]' 开头的合并摘要。")
        return "\n".join(lines)

    def plan_compression(
        self,
        messages: list[dict],
        keep_turns: int = 6,
    ) -> dict:
        """滑动窗口压缩规划(AI-Agents-in-Depth §2.7.4 第 4 层: 归档式摘要前置)。

        用 _sliding_window 标记旧轮次消息 compressed=True(消息需携带内部
        turn 字段, 来自 context_manager 内存消息), 返回:
        {
            "kept": [未压缩消息, 仍进 API],
            "compressed": [被标记压缩的消息, 摘要的来源],
        }

        Args:
            messages: 含内部 metadata(turn) 的消息列表(get_messages_with_meta)。
            keep_turns: 保留最近轮次数(默认 6, 与 M2 测试基线一致)。
        """
        marked = self._sliding_window(messages, keep_turns=keep_turns)
        kept = [m for m in marked if not m.get("compressed")]
        compressed = [m for m in marked if m.get("compressed")]
        return {"kept": kept, "compressed": compressed}

    @staticmethod
    def _is_factual(content: str) -> bool:
        """0.5.1(2026-08-10 蒋先生要求"压缩不丢事实"): 事实型消息检测。

        满足任一即视为事实型(压缩时原文保留, 不摘要化):
        - 数字密集: ≥3 个数值/百分比(持仓、账户、指标)
        - 表格: 含 | 分隔的多列行
        - 路径: 盘符/反斜杠路径/常见扩展名
        - 代码块: ``` 包裹
        - 结构化: JSON/键值对
        """
        if not content:
            return False
        # 代码块
        if "```" in content:
            return True
        # 表格(至少 2 行含 | 分隔)
        if sum(1 for ln in content.splitlines() if "|" in ln) >= 2:
            return True
        # 数字密集(≥2 个独立数值/百分比 —— 2026-08-10 调优: 3→2,
        # 持仓/记录类消息含市值+盈亏+比例即可命中)
        import re

        num_count = len(re.findall(r"\d+(?:[.,]\d+)?%?", content))
        if num_count >= 2:
            return True
        # 用户显式记忆指令("记录/记住/保存/写入/更新" + 具体对象)
        if re.search(r"(记录|记住|保存|写入|更新|记下)[\u4e00-\u9fa5A-Za-z0-9，,、\s]{0,20}(持仓|资产|数据|内容|结果|信息|配置)", content):
            return True
        # 路径(盘符 / 反斜杠 / 常见文件扩展名)
        if re.search(r"[A-Za-z]:[\\/]|\.(?:md|py|json|yaml|txt|png|jpg|zip)\b", content):
            return True
        # 结构化键值(如 "名称: 值" 出现 ≥2 次)
        if len(re.findall(r"[\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9_]*[:：]", content)) >= 2:
            return True
        return False

    async def execute(
        self,
        messages: list[dict],
        *,
        keep_turns: int = 6,
        compress_adapter: Any = None,
    ) -> dict:
        """执行一次完整压缩(AI-Agents-in-Depth §2.7.4): 滑动窗口 + 可选摘要。

        有 compress_adapter 时对压缩掉的消息生成摘要消息(summary 进 API);
        无 compress_adapter 时仅滑动窗口(低价值旧消息直接删除, 不做摘要)。
        摘要失败(LLM 调用异常)时降级为纯滑动窗口, 不中断, 但返回
        summary_error=True 供上层熔断计数(避免在反复失败的会话上持续烧钱,
        §2.7.4 第 5 层)。

        Returns:
            {
                "messages": 压缩后的消息列表(含摘要, 供 context_manager 回写),
                "summary": 摘要消息 dict 或 None,
                "compressed_msgs": 被压缩的原始消息列表,
                "summary_error": 摘要 LLM 调用是否失败(True 时已降级滑动窗口),
            }
        """
        plan = self.plan_compression(messages, keep_turns=keep_turns)
        compressed = plan["compressed"]
        if not compressed:
            return {
                "messages": messages,
                "summary": None,
                "compressed_msgs": [],
                "summary_error": False,
            }

        result_messages = list(plan["kept"])
        summary: dict | None = None
        summary_error = False
        # 0.5.1(2026-08-10 事实型压缩保护): 压缩消息按事实型分级 ——
        # 事实型(数字/表格/路径/代码)原文保留为"事实快照", 不摘要化;
        # 仅非事实型(闲聊/寒暄/中间推理)走 LLM 摘要。
        factual_msgs = [
            m for m in compressed
            if self._is_factual(str(m.get("content", "") or ""))
        ]
        summarizable = [
            m for m in compressed if m not in factual_msgs
        ]
        factual_snapshot: dict | None = None
        if factual_msgs:
            # 事实快照: 原文合并保留(超长截断 + 标注可追溯)
            factual_text = "\n\n".join(
                f"[{m.get('role', 'unknown')}]: {m.get('content', '')}"
                for m in factual_msgs
            )
            max_factual_chars = 20000  # ≈ 5K token 事实快照预算
            if len(factual_text) > max_factual_chars:
                factual_text = (
                    factual_text[:max_factual_chars]
                    + "\n…(事实快照已按预算截断, 完整内容见压缩存档)"
                )
            factual_snapshot = {
                "role": "user",
                "content": f"[事实快照(原文保留)]\n{factual_text}",
                "compressed_from_factual": True,
            }
            result_messages.insert(0, factual_snapshot)
        if summarizable and compress_adapter is not None:
            try:
                summary = await self._summarize(compress_adapter, summarizable)
                result_messages.insert(0, summary)
            except Exception:
                # 摘要失败: 降级为纯滑动窗口(不中断对话), 标记供熔断
                summary = None
                summary_error = True
                result_messages = list(plan["kept"])
        return {
            "messages": result_messages,
            "summary": summary,
            "factual_snapshot": factual_snapshot,
            "compressed_msgs": compressed,
            "summary_error": summary_error,
        }

    def _sliding_window(
        self, messages: list[dict], keep_turns: int = 6
    ) -> list[dict]:
        if not messages:
            return messages
        max_turn = max(m.get("turn", 0) for m in messages)
        keep_from = max(1, max_turn - keep_turns + 1)

        # 收集 tool_call_id 映射,确保配对不被拆分
        tool_call_turns: dict[str, int] = {}
        for m in messages:
            for tc in m.get("tool_calls", []):
                cid = tc.get("id", "")
                if cid:
                    tool_call_turns[cid] = m.get("turn", 0)

        result = []
        for m in messages:
            msg = dict(m)
            # C-1(P1-7) 防御: 显式 zone != active 的消息(理论上调用方已过滤)
            # 永不标记 compressed —— system prompt/记忆/KB 常驻
            if msg.get("zone") is not None and msg.get("zone") != "active":
                result.append(msg)
                continue
            turn = msg.get("turn", 0)
            if turn < keep_from:
                # 检查是否有 tool_result 配对在 keep_from 之后
                tid = msg.get("tool_call_id", "")
                if tid and tid in tool_call_turns:
                    call_turn = tool_call_turns[tid]
                    if call_turn >= keep_from:
                        msg["compressed"] = False
                        result.append(msg)
                        continue
                msg["compressed"] = True
            result.append(msg)
        return result

    async def _summarize(
        self, compress_adapter: Any, compressed_msgs: list[dict]
    ) -> dict:
        summary_prompt = (
            "Summarize the following conversation concisely, preserving key facts, "
            "decisions, and action items.\n"
            "IMPORTANT: 必须逐字保留所有数字、金额、比例、路径、文件名、"
            "表格、代码和结构化数据(不得概括、不得省略具体数值)。\n\n"
        )
        for m in compressed_msgs:
            role = m.get("role", "unknown")
            content = m.get("content", "") or ""
            summary_prompt += f"[{role}]: {content[:500]}\n"

        result = await compress_adapter.chat(
            [{"role": "user", "content": summary_prompt}], tools=[]
        )
        return {
            "role": "assistant",
            "content": f"[Previous Context Summary]\n{result.content}",
            "compressed_from": [id(m) for m in compressed_msgs],
        }

    async def _emit_compress_event(
        self,
        conn: "asyncpg.Connection",
        *,
        session_id: int,
        turn: int,
        trigger: str,
    ) -> None:
        from private_agent.storage.react_events import insert_react_event

        await insert_react_event(
            conn,
            session_id=session_id,
            turn=turn,
            event_type="compress",
            payload={"trigger": trigger, "turn": turn},
        )