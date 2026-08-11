"""反思总结模块 - 任务完成后自动提炼经验（双轨进化）。

Source: plan/2026-08-11-dialogue-self-evolution-improvement Task 1.2
对应参考文档：
- EvoSkill Proposer Agent（反思者）：根因分析，决定新建还是修改 Skill
- CoEvoSkills Skill Generator：从执行轨迹提炼候选 Skill
- 论文 §06 关键洞察："总结者是被严重低估的关键模块"

双轨反思（2026-08-11）：
- 领域智能体（scope=office/data_analysis/frontend_design）→ DOMAIN_REFLECTION_PROMPT
  反思专业技巧：成功模式/失败教训/用户纠正，沉淀 lesson_category='domain_skill'
- 无涯（scope=monitor）→ PROJECT_EVOLUTION_REFLECTION_PROMPT
  反思项目进化：代码重构模式/架构优化/Bug 修复套路，沉淀 lesson_category='project_evolution'

反思触发条件（满足任一）：
1. 使用了工具（非纯对话）
2. 发生错误（had_error=True）
3. 多轮迭代（turn >= 2）

跳过条件：寒暄/闲聊（无工具调用 + 单轮 + 无错误）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from private_agent.models.base import ChatResult, ModelAdapter
from private_agent.observability.logging import setup_logger

logger = setup_logger(__name__)

# 寒暄/闲聊关键词（跳过反思）
_TRIVIAL_KEYWORDS = {"你好", "谢谢", "再见", "hi", "hello", "thanks", "bye"}

# scope → lesson_category 映射（与 EvolutionRepo._SCOPE_CATEGORY_MAP 一致）
_SCOPE_TO_CATEGORY = {
    "monitor": "project_evolution",
    "office": "domain_skill",
    "data_analysis": "domain_skill",
    "frontend_design": "domain_skill",
    "global": "cross_domain",
}


@dataclass
class ReflectionResult:
    """反思产物（双轨）。"""
    scope: str
    task_summary: str
    lesson_type: str  # success / failure / correction
    lesson_content: str
    lesson_category: str = "domain_skill"  # domain_skill / project_evolution / cross_domain
    tool_chain: list[str] = field(default_factory=list)
    importance: float = 0.5


# 领域智能体反思模板（子瞻/白圭/清和用）
DOMAIN_REFLECTION_PROMPT_TEMPLATE = """你是一个领域经验总结者。请分析以下专业任务执行轨迹，提炼一条可复用的领域技巧经验。

【场景】{scope}
【用户请求】{user_message}
【执行轨迹摘要】{trace_summary}
【最终输出】{final_output}
【是否出错】{had_error}

请输出 JSON（仅 JSON，无其他文字）：
{{
  "lesson_type": "success" | "failure" | "correction",
  "task_summary": "一句话任务摘要",
  "lesson_content": "经验内容：成功模式/失败教训/纠正点。要具体可操作，不要泛泛而谈",
  "importance": 0.0-1.0 之间的浮点数（成功=0.3-0.7，失败=0.7-1.0，纠正=0.6-0.9）
}}

规则：
- success：任务成功完成，提炼可复用的工作流/工具链/专业技巧
- failure：任务出错或未完成，提炼失败原因与避免方法
- correction：用户纠正了模型行为，提炼行为修正点
- 经验内容要具体（如"用 pd.to_datetime(errors='coerce') 处理日期列"），不要泛泛（如"注意数据类型"）
- 聚焦领域技巧（如 pandas 套路、估值模型、设计原则），不涉及代码重构
"""

# 无涯项目进化反思模板（主智能体用）
PROJECT_EVOLUTION_REFLECTION_PROMPT_TEMPLATE = """你是一个项目进化经验总结者。请分析以下项目改进任务执行轨迹，提炼一条可复用的项目进化经验。

【场景】无涯·项目进化（monitor）
【改进任务】{user_message}
【执行轨迹摘要】{trace_summary}
【最终输出】{final_output}
【是否出错】{had_error}

请输出 JSON（仅 JSON，无其他文字）：
{{
  "lesson_type": "success" | "failure" | "correction",
  "task_summary": "一句话改进任务摘要",
  "lesson_content": "经验内容：代码重构模式/架构优化技巧/Bug 修复套路/性能改进方法。要具体可操作，包含模式名称与适用场景",
  "importance": 0.0-1.0 之间的浮点数（成功=0.4-0.7，失败=0.7-1.0，纠正=0.6-0.9）
}}

规则：
- success：项目改进成功完成，提炼可复用的进化模式（如"提取重复代码为工具函数"+"先备份再 file_write"+"用 pytest 验证无回归"）
- failure：改进失败（如引入回归/破坏现有功能），提炼失败原因与避免方法
- correction：用户纠正了改进方向，提炼行为修正点（如"应优先 YAGNI 而非抽象"）
- 经验内容要具体（如"重复模式 >=3 处时提取函数，用 SearchReplace 工具改"），不要泛泛（如"注意代码质量"）
- 聚焦项目级进化（代码/架构/Skill prompt/性能），不涉及领域专业技巧
"""


class ReflectionEngine:
    """反思引擎：任务完成后生成经验教训（双轨：领域技巧 or 项目进化）。"""

    def __init__(self, adapter: ModelAdapter) -> None:
        self._adapter = adapter

    async def reflect(
        self,
        scope: str,
        user_message: str,
        react_events: list[dict[str, Any]],
        final_output: str,
        had_error: bool,
    ) -> ReflectionResult | None:
        """对一轮任务执行反思（根据 scope 选择双轨模板）。

        Returns:
            ReflectionResult 或 None（寒暄/闲聊跳过时返回 None）
        """
        # 提取工具链
        tool_chain = self._extract_tool_chain(react_events)

        # 跳过条件：寒暄/闲聊（无工具 + 无错误 + 单轮）
        if not tool_chain and not had_error and self._is_trivial(user_message):
            logger.debug("reflection_skipped reason=trivial_conversation scope=%s", scope)
            return None

        # 双轨模板选择
        lesson_category = _SCOPE_TO_CATEGORY.get(scope, "domain_skill")
        if scope == "monitor":
            prompt_template = PROJECT_EVOLUTION_REFLECTION_PROMPT_TEMPLATE
        else:
            prompt_template = DOMAIN_REFLECTION_PROMPT_TEMPLATE

        # 构建轨迹摘要（避免全量注入，只取关键步骤）
        trace_summary = self._build_trace_summary(react_events)

        prompt = prompt_template.format(
            scope=scope,
            user_message=user_message[:500],
            trace_summary=trace_summary,
            final_output=final_output[:500],
            had_error=had_error,
        )

        try:
            result: ChatResult = await self._adapter.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
            )
            parsed = self._parse_reflection_response(
                result.content, scope, lesson_category
            )
            if parsed is not None:
                parsed.tool_chain = tool_chain
                logger.info(
                    "reflection_done scope=%s category=%s type=%s importance=%.2f",
                    scope, parsed.lesson_category, parsed.lesson_type, parsed.importance,
                )
            return parsed
        except Exception as e:
            logger.warning("reflection_failed scope=%s error=%s", scope, e)
            return None

    @staticmethod
    def _extract_tool_chain(events: list[dict[str, Any]]) -> list[str]:
        """从 react_events 提取工具调用序列（去重保序）。"""
        chain: list[str] = []
        seen: set[str] = set()
        for ev in events:
            if ev.get("event_type") == "tool_call":
                tool = ev.get("payload", {}).get("tool", "")
                if tool and tool not in seen:
                    chain.append(tool)
                    seen.add(tool)
        return chain

    @staticmethod
    def _is_trivial(user_message: str) -> bool:
        """判断是否为寒暄/闲聊。"""
        msg_lower = user_message.strip().lower()
        if len(msg_lower) > 20:
            return False
        return any(kw in msg_lower for kw in _TRIVIAL_KEYWORDS)

    @staticmethod
    def _build_trace_summary(events: list[dict[str, Any]]) -> str:
        """构建轨迹摘要（每步一行，避免全量注入）。"""
        lines: list[str] = []
        for ev in events:
            etype = ev.get("event_type", "")
            turn = ev.get("turn", 0)
            payload = ev.get("payload", {})
            if etype == "thinking":
                lines.append(f"[turn{turn} 思考] {str(payload.get('content', ''))[:100]}")
            elif etype == "tool_call":
                lines.append(f"[turn{turn} 调用工具] {payload.get('tool', '')}")
            elif etype == "tool_result":
                result_str = str(payload.get("result", ""))[:100]
                lines.append(f"[turn{turn} 工具结果] {result_str}")
            elif etype == "error":
                lines.append(f"[turn{turn} 错误] {str(payload.get('error', ''))[:100]}")
        return "\n".join(lines) if lines else "(无工具调用)"

    @staticmethod
    def _parse_reflection_response(
        content: str, scope: str, lesson_category: str
    ) -> ReflectionResult | None:
        """解析模型返回的 JSON（双轨：注入 lesson_category）。"""
        if not content or not content.strip():
            return None
        try:
            # 容错：提取 JSON 部分（模型可能包裹 markdown）
            text = content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text)
            return ReflectionResult(
                scope=scope,
                task_summary=data.get("task_summary", ""),
                lesson_type=data.get("lesson_type", "success"),
                lesson_content=data.get("lesson_content", ""),
                lesson_category=lesson_category,
                importance=float(data.get("importance", 0.5)),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning("reflection_parse_failed scope=%s error=%s content=%s", scope, e, content[:200])
            return None
