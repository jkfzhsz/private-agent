"""M1 Phase 1 step 5 - GET /admin/disk-status HTTP 端点。
M2 §4.2 - POST /admin/sessions/{session_id}/extract_memory 手动触发记忆提取。

Source: plan/m1-react-loop step 5 (蓝图 §2.10 第 6 条 + §9.4 AC-4)

GET /admin/disk-status 返回 {"level", "message", "size_bytes"}。
从 db.get_pool() 获取 conn,调用 get_disk_status。
异常时返回 503 + {"error": "disk_status_unavailable"}。

POST /admin/sessions/{session_id}/extract_memory 手动触发记忆提取(蓝图 §4.2)。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from private_agent.config import loader
from private_agent.knowledge.kb_repo import KnowledgeBaseRepo
from private_agent.memory.manager import MemoryManager
from private_agent.memory.memories_repo import MemoriesRepo
from private_agent.storage import db
from private_agent.storage.disk_alert import get_disk_status

router = APIRouter(prefix="/admin", tags=["admin"])


def _build_compress_adapter(cfg):
    """构造压缩模型适配器(蓝图 §4.2,spec AC-7),测试可 monkeypatch。"""
    from private_agent.models.registry import build_compress_adapter
    return build_compress_adapter(cfg)


@router.get("/disk-status", response_model=None)
async def disk_status():
    """返回磁盘占用分级状态(蓝图 §2.10 第 6 条)。

    Returns:
        200: {"level": "none|yellow|orange|red", "message": "...", "size_bytes": N}
        503: {"error": "disk_status_unavailable"}
    """
    try:
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            return await get_disk_status(conn)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "disk_status_unavailable"},
        )


@router.post("/sessions/{session_id}/extract_memory", response_model=None)
async def extract_memory(session_id: int):
    """手动触发记忆提取(蓝图 §4.2 缺口补充)。

    Args:
        session_id: 会话 ID。

    Returns:
        200: {"count": int, "types": [str, ...]}
        404: {"error": "session_not_found"}
        500: {"error": "extract_failed"}
    """
    try:
        conn = await db.connect()
        try:
            # 验证会话存在
            row = await conn.fetchrow(
                "SELECT id FROM sessions WHERE id = $1", session_id,
            )
            if row is None:
                raise HTTPException(
                    status_code=404, detail="session_not_found"
                )
            cfg = loader.load_config()
            repo = MemoriesRepo(conn)
            mgr = MemoryManager(
                memories_repo=repo,
                compress_adapter=_build_compress_adapter(cfg),
                extract_interval_turns=cfg.get("memory", {}).get(
                    "extract_interval_turns", 8
                ),
                eviction_max_active=cfg.get("memory", {}).get(
                    "eviction", {}
                ).get("max_active_count", 200),
                eviction_min_importance=cfg.get("memory", {}).get(
                    "eviction", {}
                ).get("min_importance_threshold", 0.3),
                eviction_expire_days=cfg.get("memory", {}).get(
                    "eviction", {}
                ).get("expire_days", 30),
            )
            # 获取会话最后轮次
            last_turn = await conn.fetchval(
                "SELECT COALESCE(MAX(turn), 0) FROM messages WHERE session_id = $1",
                session_id,
            )
            memories = await mgr.manual_extract(
                session_id=session_id, current_turn=last_turn,
            )
            types = [m.type for m in memories]
            return {"count": len(memories), "types": types}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "extract_failed"},
        )


# ══════════════════════════════════════════════════════════════════════════
# 知识库管理(蓝图 §4.16)
# ══════════════════════════════════════════════════════════════════════════


@router.get("/knowledge/stats", response_model=None)
async def knowledge_stats():
    """获取知识库统计信息(蓝图 §4.16 快照)。

    Returns:
        200: {"total_documents": int, "total_chunks": int, "scenarios": {...}}
        503: {"error": "stats_unavailable"}
    """
    try:
        conn = await db.connect()
        try:
            repo = KnowledgeBaseRepo(conn)
            stats = await repo.get_stats()
            return stats
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "stats_unavailable"},
        )


@router.post("/knowledge/upload", response_model=None)
async def knowledge_upload(
    session_id: int,
    content: str,
    filename: str,
    scenario: str | None = None,
):
    """上传文档到知识库(蓝图 §4.6 文档处理流水线)。

    Args:
        session_id: 会话 ID(用于关联)。
        content: 文档原始文本。
        filename: 文件名。
        scenario: 场景(可选)。

    Returns:
        200: {"doc_id": int, "chunks": int}
        500: {"error": "upload_failed"}
    """
    try:
        conn = await db.connect()
        try:
            from private_agent.knowledge.kb_service import KnowledgeBaseService
            from private_agent.knowledge.document_processor import DocumentProcessor

            repo = KnowledgeBaseRepo(conn)
            svc = KnowledgeBaseService(
                kb_repo=repo,
                processor=DocumentProcessor(),
            )
            doc_id, chunks = await svc.process_document(
                content=content,
                filename=filename,
                scenario=scenario,
            )
            return {"doc_id": doc_id, "chunks": len(chunks)}
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "upload_failed"},
        )