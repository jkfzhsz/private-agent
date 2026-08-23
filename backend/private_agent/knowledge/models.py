"""蓝图 §4.8/§4.12 知识库数据类。

定义:
- Chunk: 知识库分块数据类
- Document: 文档元数据类
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Chunk:
    """知识库分块数据类(蓝图 §4.8/§4.12)。

    Attributes:
        text: 分块文本内容。
        metadata: 扩展 metadata(标题/页码/符号名等)。
        doc_type: 文档类型(markdown/pdf/code/plain)。
        doc_id: 所属文档 ID。
        chunk_id: 分块 ID(DB 返回时填充)。
        scenario: 场景(office/data_analysis/frontend_design)。
        source: 来源(文件名/URL)。
        embedding: 向量(DB 返回时填充,仅用于检索结果)。
        score: 相似度分数(检索结果时填充)。
        is_active: 软删除标记。
    """

    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    doc_type: str = "plain"
    doc_id: int | None = None
    chunk_id: int | None = None
    scenario: str | None = None
    source: str | None = None
    embedding: bytes | None = None
    score: float = 0.0
    is_active: bool = True


@dataclass
class Document:
    """文档元数据类(蓝图 §4.12 kb_documents 表)。

    Attributes:
        id: 文档 ID。
        source: 文件名/URL/手动输入。
        content: 原始内容。
        scenario: 场景。
        metadata: 扩展 metadata(JSONB)。
        hash: SHA-256 内容 hash。
        is_active: 软删除标记。
        created_at: 创建时间。
        updated_at: 更新时间。
    """

    id: int | None = None
    source: str = ""
    content: str | None = None
    scenario: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    hash: str | None = None
    is_active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None