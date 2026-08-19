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
        task_phase: bool = False,
        model_suggested: bool = False,
        keep_ratio: float = 0.1,
    ) -> str | None:
        """多信号压缩触发判断(B-1, 设计文档 §3.2-B1)。

        触发决策优先级: token_limit > model_suggested > task_phase > turn_limit。
        返回触发信号名(供 compress 事件记录 trigger), 未触发返回 None。

        Args:
            messages: 当前上下文消息(用于 token 估算)。
            active_turns: 当前轮次。
            context_window: 上下文窗口(token)。
            compress_adapter: 压缩适配器(预留, 未消费)。
            task_phase: 任务阶段切换信号(默认 False = 关闭, 零回归)。
            model_suggested: 模型自主压缩信号(默认 False = 关闭)。
            keep_ratio: 保留最近原始消息 token 比例(B-2, 默认 0.1)。
        """
        tokens = self._estimator.estimate_messages(messages)
        if tokens > context_window * 0.8:
            return "token_limit"
        if model_suggested:
            return "model_suggested"
        if task_phase:
            return "task_phase"
        if active_turns > 10:
            return "turn_limit"
        return None

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
        keep_ratio: float = 0.1,
    ) -> dict:
        """滑动窗口压缩规划(AI-Agents-in-Depth §2.7.4 第 4 层: 归档式摘要前置)。

        用 _sliding_window 标记旧轮次消息 compressed=True(消息需携带内部
        turn 字段, 来自 context_manager 内存消息), 返回:
        {
            "kept": [未压缩消息, 仍进 API],
            "compressed": [被标记压缩的消息, 摘要的来源],
        }

        B-2(2026-08-15, 设计文档 §3.2-B2): keep_ratio 保留最近 ~ratio token
        的原始消息 —— 与 keep_turns 轮次保留取 token 更优者(保留更多消息),
        防止"轮次多但单轮 token 大"时把大轮次整轮压缩掉。

        Args:
            messages: 含内部 metadata(turn) 的消息列表(get_messages_with_meta)。
            keep_turns: 保留最近轮次数(默认 6, 与 M2 测试基线一致)。
            keep_ratio: 保留最近原始消息 token 比例(默认 0.1 = 最近 10%)。
        """
        # 轮次窗口
        turn_kept = self._sliding_window(messages, keep_turns=keep_turns)
        # token 窗口(按消息顺序累积至 total × keep_ratio)
        ratio_kept = self._sliding_window_by_ratio(messages, keep_ratio=keep_ratio)
        # 取"保留更多"者(B-2: 两者取 token 更优者 —— 保留更多原始消息)
        if self._kept_tokens(ratio_kept) > self._kept_tokens(turn_kept):
            marked = ratio_kept
        else:
            marked = turn_kept
        kept = [m for m in marked if not m.get("compressed")]
        compressed = [m for m in marked if m.get("compressed")]
        return {"kept": kept, "compressed": compressed}

    @staticmethod
    def _kept_tokens(marked: list[dict]) -> int:
        """估算保留消息(未压缩)的 token 总量(比较用, 精度要求不高)。"""
        est = TokenEstimator()
        kept = [m for m in marked if not m.get("compressed")]
        return est.estimate_messages(kept)

    def _sliding_window_by_ratio(
        self, messages: list[dict], keep_ratio: float = 0.1
    ) -> list[dict]:
        """按 token 比例保留最近消息(替代按轮次, B-2)。

        从消息末尾向前累积 token, 保留最近 ~keep_ratio 总量的原始消息;
        保留工具配对完整性(与 _sliding_window 同语义: tool_result 配对
        在保留区间内的 tool_calls 消息不被压缩)。
        """
        if not messages:
            return messages
        total = self._estimator.estimate_messages(messages)
        budget = int(total * max(0.0, min(1.0, keep_ratio)))
        if budget <= 0:
            # ratio=0: 不保留任何 active 消息(全压缩; frozen/stable 仍跳过)
            keep_from_idx = len(messages)
        else:
            # 从末尾向前累加, 找到保留起点
            keep_from_idx = 0
            acc = 0
            for i in range(len(messages) - 1, -1, -1):
                m = messages[i]
                acc += self._estimator.estimate_messages([m])
                if acc >= budget:
                    keep_from_idx = i
                    break
                keep_from_idx = i

        # 收集 tool_call_id 映射(配对保护)
        tool_call_turns: dict[str, int] = {}
        for m in messages:
            for tc in m.get("tool_calls", []):
                cid = tc.get("id", "")
                if cid:
                    tool_call_turns[cid] = m.get("turn", 0)

        result = []
        for idx, m in enumerate(messages):
            msg = dict(m)
            if msg.get("zone") is not None and msg.get("zone") != "active":
                result.append(msg)
                continue
            if idx >= keep_from_idx:
                result.append(msg)
                continue
            # 保留区间之前: 检查工具配对
            tid = msg.get("tool_call_id", "")
            if tid and tid in tool_call_turns:
                if tool_call_turns[tid] >= 0:  # 配对在保留区内(简化: 全保留)
                    msg["compressed"] = False
                    result.append(msg)
                    continue
            msg["compressed"] = True
            result.append(msg)
        return result

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
        keep_ratio: float = 0.1,
        compress_adapter: Any = None,
        workspace: str = "",
    ) -> dict:
        """执行一次完整压缩(AI-Agents-in-Depth §2.7.4): 滑动窗口 + 可选摘要。

        有 compress_adapter 时对压缩掉的消息生成摘要消息(summary 进 API);
        无 compress_adapter 时仅滑动窗口(低价值旧消息直接删除, 不做摘要)。
        摘要失败(LLM 调用异常)时降级为纯滑动窗口, 不中断, 但返回
        summary_error=True 供上层熔断计数(避免在反复失败的会话上持续烧钱,
        §2.7.4 第 5 层)。

        B-2(2026-08-15): keep_ratio 保留最近 ~10% token 原始消息(与
        keep_turns 取 token 更优者, 见 plan_compression)。

        C-3(2026-08-15 压缩转存联动, 设计文档 §3.3-C3): workspace 非空时
        事实型消息写入 {workspace}/archive/ctx-{turn}.md(文件化转存, 替代
        20000 字符内联截断), 摘要含 "[事实快照见 ws:archive/ctx-{turn}.md]"
        路径引用; workspace 为空 → 维持现有 factual_snapshot 内联截断
        (零回归)。

        Returns:
            {
                "messages": 压缩后的消息列表(含摘要, 供 context_manager 回写),
                "summary": 摘要消息 dict 或 None,
                "compressed_msgs": 被压缩的原始消息列表,
                "summary_error": 摘要 LLM 调用是否失败(True 时已降级滑动窗口),
                "archive_path": 事实转存文件相对路径或 None(C-3),
            }
        """
        plan = self.plan_compression(
            messages, keep_turns=keep_turns, keep_ratio=keep_ratio
        )
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
        archive_path: str | None = None
        if factual_msgs:
            # 事实快照: 原文合并保留(超长截断 + 标注可追溯)
            factual_text = "\n\n".join(
                f"[{m.get('role', 'unknown')}]: {m.get('content', '')}"
                for m in factual_msgs
            )
            max_factual_chars = 20000  # ≈ 5K token 事实快照预算
            # C-3(2026-08-15 压缩转存联动): 有 workspace 时事实型消息写入
            # {workspace}/archive/ctx-{turn}.md 文件化转存(替代内联截断),
            # 摘要保留路径引用; 无 workspace → 维持现有内联截断(零回归)。
            archive_rel = self._write_factual_archive(
                factual_text, workspace=workspace
            )
            if archive_rel:
                archive_path = archive_rel
                factual_snapshot = {
                    "role": "user",
                    "content": (
                        f"[事实快照见 ws:{archive_rel}]"
                        f"\n(完整事实已转存工作区 archive/ 文件, 可随时用 "
                        f"ws_read 读取; 以下为关键开头预览)\n{factual_text[:max_factual_chars]}"
                    ),
                    "compressed_from_factual": True,
                }
            else:
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
            "archive_path": archive_path,
        }

    @staticmethod
    def _write_factual_archive(factual_text: str, workspace: str) -> str | None:
        """C-3: 事实快照转存工作区 archive/ 文件, 返回相对路径。

        写入 {workspace}/archive/ctx-{hash8}.md(按内容 hash 命名, 天然去重,
        幂等)。workspace 为空/写入失败 → 返回 None(调用方回退内联截断)。

        Returns:
            archive/ctx-{hash8}.md 相对路径(供 ws_read 引用)或 None。
        """
        if not workspace:
            return None
        try:
            import hashlib
            import os

            ws_root = os.path.abspath(
                os.path.expanduser(os.path.expandvars(workspace))
            )
            archive_dir = os.path.join(ws_root, "archive")
            os.makedirs(archive_dir, exist_ok=True)
            digest = hashlib.sha256(factual_text.encode("utf-8")).hexdigest()[:8]
            filename = f"ctx-{digest}.md"
            filepath = os.path.join(archive_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(factual_text)
            return f"archive/{filename}"
        except Exception:  # noqa: BLE001 - 转存失败回退内联截断
            return None

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
        compressed_msgs: list[dict] | None = None,
        summary: dict | None = None,
    ) -> None:
        """写入 compress 事件(2026-08-18: 附带估算 token, 供消耗统计)。

        压缩消耗为本地估算(无真实 usage): input_tokens_est = 被压缩消息
        估算 token, output_tokens_est = 摘要文本估算 token。标记 estimated=true,
        与 token_usage 事件的精确用量区分, 不混入 /usage 精确统计。
        """
        from private_agent.storage.react_events import insert_react_event

        payload: dict = {"trigger": trigger, "turn": turn, "estimated": True}
        if compressed_msgs:
            payload["input_tokens_est"] = self._estimator.estimate_messages(
                compressed_msgs
            )
        if summary:
            payload["output_tokens_est"] = self._estimator.estimate(
                str(summary.get("content") or "")
            )
        await insert_react_event(
            conn,
            session_id=session_id,
            turn=turn,
            event_type="compress",
            payload=payload,
        )