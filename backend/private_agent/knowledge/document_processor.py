"""蓝图 §4.7/§4.8/§4.9 文档类型识别与分块策略。

职责:
1. 按扩展名+内容嗅探识别文档类型(markdown/pdf/code/plain)。
2. 按类型分发不同 chunking 策略(标题层级/段落/函数边界/固定长度)。
3. 支持 config 驱动 chunk_size/overlap 参数。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from private_agent.knowledge.models import Chunk

# 蓝图 §4.7 类型识别规则: (扩展名列表, 内容嗅探正则, 文档类型)
TYPE_RULES: list[tuple[list[str], str | None, str]] = [
    ([".md", ".markdown"], None, "markdown"),
    ([".pdf"], None, "pdf"),
    (
        [".py", ".js", ".ts", ".java", ".go", ".rs", ".cpp", ".c", ".h"],
        None,
        "code",
    ),
    # 兜底:内容嗅探
    ([], r"^#{1,6}\s+", "markdown"),  # 开头是 Markdown 标题
    ([], None, "plain"),  # 默认纯文本
]

# 蓝图 §4.9 默认 chunk 参数
DEFAULT_CHUNK_PARAMS: dict[str, dict[str, int]] = {
    "markdown": {"chunk_size": 512, "chunk_overlap": 64},
    "pdf": {"chunk_size": 512, "chunk_overlap": 64},
    "code": {"chunk_size": 256, "chunk_overlap": 32},
    "plain": {"chunk_size": 400, "chunk_overlap": 50},
}

# 蓝图 §4.9 可解析的文档类型
VALID_DOC_TYPES = frozenset({"markdown", "pdf", "code", "plain"})


class DocumentProcessor:
    """文档类型识别与分块处理器(蓝图 §4.7/§4.8)。

    Args:
        chunk_params: 可选覆盖各文档类型的 chunk_size/chunk_overlap。
                      默认使用 DEFAULT_CHUNK_PARAMS。
    """

    def __init__(
        self,
        chunk_params: dict[str, dict[str, int]] | None = None,
    ) -> None:
        self._chunk_params = chunk_params or DEFAULT_CHUNK_PARAMS

    # ── 类型识别 ────────────────────────────────────────────────────────

    def detect_type(self, filename: str, content: str = "") -> str:
        """按扩展名+内容嗅探识别文档类型(蓝图 §4.7)。

        Args:
            filename: 文件名(含扩展名)。
            content: 文档内容(前 200 字符用于嗅探)。

        Returns:
            文档类型: markdown/pdf/code/plain。
        """
        ext = Path(filename).suffix.lower()
        for extensions, pattern, doc_type in TYPE_RULES:
            if ext in extensions:
                return doc_type
            if pattern and re.search(pattern, content[:200], re.MULTILINE):
                return doc_type
        return "plain"

    # ── 主入口 ──────────────────────────────────────────────────────────

    def process(
        self,
        content: str,
        filename: str,
        scenario: str | None = None,
    ) -> list[Chunk]:
        """完整处理流水线:类型识别 → chunking(蓝图 §4.6)。

        Args:
            content: 文档原始文本。
            filename: 文件名(用于类型识别和 source 填充)。
            scenario: 场景(office/data_analysis/frontend_design)。

        Returns:
            Chunk 列表。
        """
        doc_type = self.detect_type(filename, content)
        params = self._chunk_params.get(doc_type, DEFAULT_CHUNK_PARAMS["plain"])
        source = Path(filename).name

        if doc_type == "markdown":
            raw_chunks = chunk_markdown(
                content, params["chunk_size"], params["chunk_overlap"]
            )
        elif doc_type == "pdf":
            raw_chunks = chunk_pdf(
                content, params["chunk_size"], params["chunk_overlap"]
            )
        elif doc_type == "code":
            raw_chunks = chunk_code(
                content, filename, params["chunk_size"], params["chunk_overlap"]
            )
        else:
            texts = _split_by_paragraph(
                content, params["chunk_size"], params["chunk_overlap"]
            )
            raw_chunks = [(t, {"type": "plain"}) for t in texts]

        return [
            Chunk(
                text=text,
                metadata=meta,
                doc_type=doc_type,
                scenario=scenario,
                source=source,
            )
            for text, meta in raw_chunks
        ]

    @staticmethod
    def compute_hash(content: str) -> str:
        """计算文档内容 SHA-256 hash(蓝图 §4.6 增量更新判断)。"""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════════════
# Chunking 策略实现(蓝图 §4.8)
# ══════════════════════════════════════════════════════════════════════════


def chunk_markdown(
    content: str,
    chunk_size: int = 512,
    overlap: int = 64,
) -> list[tuple[str, dict[str, Any]]]:
    """Markdown chunking:按标题层级切割(蓝图 §4.8)。

    Returns:
        [(text, metadata), ...] 列表,metadata 含 title/level。
    """
    # 按 ## / ### 标题切割
    sections = re.split(r"(?=^#{1,6}\s+)", content, flags=re.MULTILINE)
    chunks: list[tuple[str, dict[str, Any]]] = []
    for section in sections:
        if not section.strip():
            continue
        # 提取标题作为 metadata
        title_match = re.match(r"^(#{1,6})\s+(.+)", section)
        title = title_match.group(2) if title_match else ""
        level = len(title_match.group(1)) if title_match else 0
        meta: dict[str, Any] = {"title": title, "level": level}
        # 单节超过 chunk_size,进一步按段落切割
        if len(section) > chunk_size * 3:
            sub_chunks = _split_by_paragraph(section, chunk_size, overlap)
            for sc in sub_chunks:
                chunks.append((sc, {**meta, "type": "markdown"}))
        else:
            chunks.append((section, {**meta, "type": "markdown"}))
    return chunks


def chunk_pdf(
    content: str,
    chunk_size: int = 512,
    overlap: int = 64,
) -> list[tuple[str, dict[str, Any]]]:
    """PDF chunking:按段落切割(蓝图 §4.8)。

    MVP 简化:将 content 视为纯文本段落序列,不依赖页信息。
    V2 增加页码 metadata 支持。

    Returns:
        [(text, metadata), ...] 列表,metadata 含 type。
    """
    paragraphs = re.split(r"\n\s*\n", content)
    chunks: list[tuple[str, dict[str, Any]]] = []
    buffer = ""
    for para in paragraphs:
        if not para.strip():
            continue
        if len(buffer) + len(para) > chunk_size * 3 and buffer:
            chunks.append((buffer, {"type": "pdf"}))
            buffer = buffer[-overlap * 3 :] + "\n\n" + para if overlap else para
        else:
            buffer = buffer + "\n\n" + para if buffer else para
    if buffer:
        chunks.append((buffer, {"type": "pdf"}))
    return chunks


def chunk_code(
    content: str,
    filename: str,
    chunk_size: int = 256,
    overlap: int = 32,
) -> list[tuple[str, dict[str, Any]]]:
    """Code chunking:按函数/类边界切割(蓝图 §4.8)。

    Returns:
        [(text, metadata), ...] 列表,metadata 含 file/symbol。
    """
    # 按函数/类定义切割
    pattern = (
        r"(?=^(?:async\s+)?(?:def|class|function|func|public|private|protected)\s+)"
    )
    blocks = re.split(pattern, content, flags=re.MULTILINE)
    chunks: list[tuple[str, dict[str, Any]]] = []
    for block in blocks:
        if not block.strip():
            continue
        # 提取符号名
        symbol_match = re.match(
            r"(?:async\s+)?(?:def|class|function|func)\s+(\w+)",
            block,
        )
        symbol = symbol_match.group(1) if symbol_match else ""
        meta: dict[str, Any] = {"file": Path(filename).name, "symbol": symbol}
        # 超长块进一步按行切割
        if len(block) > chunk_size * 3:
            sub_blocks = _split_by_lines(block, chunk_size, overlap)
            for sb in sub_blocks:
                chunks.append((sb, {**meta, "type": "code"}))
        else:
            chunks.append((block, {**meta, "type": "code"}))
    return chunks


# ── 辅助函数 ────────────────────────────────────────────────────────────


def _split_by_paragraph(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    """按段落切割,超过 chunk_size 时在段落边界截断,保留 overlap(蓝图 §4.8)。

    Args:
        text: 输入文本。
        chunk_size: 每块目标字符数。
        overlap: 前后块重叠字符数。

    Returns:
        分块后的文本列表。
    """
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    buffer = ""
    for para in paragraphs:
        if not para.strip():
            continue
        if len(buffer) + len(para) > chunk_size * 3 and buffer:
            chunks.append(buffer)
            buffer = buffer[-overlap * 3 :] + "\n\n" + para if overlap else para
        else:
            buffer = buffer + "\n\n" + para if buffer else para
    if buffer:
        chunks.append(buffer)
    return chunks


def _split_by_lines(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    """按行数估算切割,保留 overlap(蓝图 §4.8 code 兜底)。

    Args:
        text: 输入文本。
        chunk_size: 每块目标字符数。
        overlap: 前后块重叠字符数。

    Returns:
        分块后的文本列表。
    """
    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if current_len + len(line) > chunk_size * 3 and current:
            chunks.append("".join(current))
            # overlap:保留末尾几行
            overlap_chars = 0
            overlap_lines: list[str] = []
            for l in reversed(current):
                if overlap_chars + len(l) > overlap * 3:
                    break
                overlap_lines.insert(0, l)
                overlap_chars += len(l)
            current = list(overlap_lines) if overlap else []
            current_len = sum(len(l) for l in current)
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks