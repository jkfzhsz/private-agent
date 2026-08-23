"""蓝图 §4.6-4.16 知识库模块。

公开 API:
- DocumentProcessor: 文档类型识别 + chunking
- KnowledgeBaseRepo: kb_documents/kb_chunks CRUD + 检索
- KnowledgeBaseService: 知识库服务编排层
- EmbeddingService: Embedding 服务(Worker + 降级 + LRU 缓存)
- RerankerService: Reranker 重排服务
- Chunk: 分块数据类
- Document: 文档元数据类
- rrf_fusion: RRF 融合策略
"""
from __future__ import annotations

from private_agent.knowledge.document_processor import DocumentProcessor
from private_agent.knowledge.embedding_service import EmbeddingService
from private_agent.knowledge.kb_repo import (
    KnowledgeBaseRepo,
    rrf_fusion,
)
from private_agent.knowledge.kb_service import KnowledgeBaseService
from private_agent.knowledge.models import Chunk, Document
from private_agent.knowledge.reranker_service import RerankerService

__all__ = [
    "DocumentProcessor",
    "EmbeddingService",
    "KnowledgeBaseRepo",
    "KnowledgeBaseService",
    "RerankerService",
    "Chunk",
    "Document",
    "rrf_fusion",
]