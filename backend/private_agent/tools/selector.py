"""动态工具选择器 —— 对话流畅度优化(方向一, 蓝图 §2.4 延伸)。

背景: 此前每轮把全部工具(内置 + 231 MCP)全量注入模型请求, 工具 schema
占用 20k-35k token/轮, 拖慢首 token 延迟。本选择器每轮从工具池
动态挑选 top-N 相关工具注入, 执行侧(_find_tool)仍遍历全池作安全网。

设计:
- 确定性评分(同输入同输出, 测试友好): 关键词重叠 + 历史使用衰减 + 锚点
- 每轮 turn 开始时求值一次, 迭代间固定(KV Cache 前缀友好)
- 池内工具 ≤ min_pool_size 时全量注入(小池无需裁剪)
"""

from __future__ import annotations

import re
from collections import Counter

from private_agent.tools.defs import ToolDef


def _tokenize(text: str) -> set[str]:
    """粗分词: 英文小写单词 + 中文 2-gram。用于关键词重叠打分。"""
    tokens: set[str] = set()
    for w in re.findall(r"[a-z][a-z0-9_]{1,}", text.lower()):
        tokens.add(w)
    # 中文 2-gram(避免逐字噪声)
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for seg in cjk:
        for i in range(len(seg) - 1):
            tokens.add(seg[i : i + 2])
    return tokens


class ToolSelector:
    """每轮从工具池挑选 top-N 注入模型请求。"""

    def __init__(self, cfg: dict | None = None):
        sel = (cfg or {}).get("tools", {}).get("tool_selection", {}) or {}
        self.enabled = bool(sel.get("enabled", True))
        self.top_n = max(1, int(sel.get("top_n", 15)))
        self.min_pool_size = max(1, int(sel.get("min_pool_size", 8)))
        always = sel.get("always_include", [])
        self.always_include = {str(a) for a in always} if always else set()
        # 会话内历史使用计数(评分权重: 用过的工具倾向保留, 衰减避免僵化)
        self._usage: Counter[str] = Counter()

    def record_usage(self, tool_name: str) -> None:
        """记录工具被实际调用(评分加权)。"""
        self._usage[tool_name] += 1

    def _score(self, td: ToolDef, query_tokens: set[str]) -> float:
        """确定性评分: 关键词重叠(0.6) + 历史使用衰减(0.3) + 描述长度归一(0.1)。"""
        doc_tokens = _tokenize(f"{td.name} {td.description or ''}")
        overlap = len(doc_tokens & query_tokens)
        kw_score = min(1.0, overlap / 3.0)  # 重叠≥3 词即满分
        usage = self._usage.get(td.name, 0)
        usage_score = min(1.0, usage / 5.0)  # 用过 5 次即满分
        desc_score = min(1.0, len(td.description or "") / 200.0)
        return 0.6 * kw_score + 0.3 * usage_score + 0.1 * desc_score

    def select(self, tools: list[ToolDef], user_message: str) -> list[ToolDef]:
        """从工具池挑选本轮注入子集(保持工具池原始顺序)。

        阶段三批次3(T3.3, 调研 round2 §4.3.1): is_kernel=True 的内核工具
        作为隐含锚点始终注入(高频基础能力), 非内核工具(search_knowledge/
        read_artifact 下沉)靠关键词/历史评分竞争 top-N —— 实现"非场景
        工具不主动注入"的下沉效果。
        """
        if not self.enabled or not tools:
            return tools
        if len(tools) <= self.min_pool_size:
            return tools
        query_tokens = _tokenize(user_message)
        # 内核工具 + always_include 配置 → 锚点集合
        anchors = [
            t for t in tools
            if t.name in self.always_include or getattr(t, "is_kernel", True)
        ]
        rest = [t for t in tools if t.name not in self.always_include
                and not getattr(t, "is_kernel", True)]
        ranked = sorted(rest, key=lambda t: self._score(t, query_tokens), reverse=True)
        # top-N 减去锚点数后从 rest 取(防超限)
        remaining = max(1, self.top_n - len(anchors))
        chosen = ranked[:remaining]
        seen: set[str] = {t.name for t in anchors}
        result = list(anchors)
        for t in chosen:
            if t.name not in seen:
                result.append(t)
                seen.add(t.name)
        return result
