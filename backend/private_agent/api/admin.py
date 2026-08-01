"""Admin 控制面 HTTP 端点(蓝图 §2.10 / §4.2 / §7.4)。

- M1: GET /admin/disk-status 磁盘占用分级
- M2: POST /admin/sessions/{id}/extract_memory 手动记忆提取
- M2: GET /admin/knowledge/stats + POST /admin/knowledge/upload 知识库管理
- M3: POST /admin/sessions/{id}/activate Skill 激活与锁定 (plan step 19)
- M3: GET /admin/skills 列表 + GET /admin/skills/{name} 详情 (plan step 17-18)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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


def _build_skill_manager(cfg):
    """构造 SkillManager(M3 §7.4),测试可 monkeypatch。"""
    from private_agent.skills.example_loader import ExampleLoader
    from private_agent.skills.loader import SkillLoader
    from private_agent.skills.manager import SkillManager
    from private_agent.tools.builtins import register_all_builtins
    from private_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_all_builtins(registry)
    return SkillManager(
        loader=SkillLoader.from_cfg(cfg),
        example_loader=ExampleLoader.from_cfg(cfg),
        tool_registry=registry,
    )


def _build_skill_loader(cfg):
    """构造 SkillLoader(plan step 17-18),测试可 monkeypatch。"""
    from private_agent.skills.loader import SkillLoader
    return SkillLoader.from_cfg(cfg)


class ActivateSkillRequest(BaseModel):
    """POST /admin/sessions/{id}/activate 请求体(plan step 19)。"""
    skill_name: str


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


# ══════════════════════════════════════════════════════════════════════════
# Skills 激活(蓝图 §7.4,plan step 19)
# ══════════════════════════════════════════════════════════════════════════


@router.post("/sessions/{session_id}/activate", response_model=None)
async def activate_skill(session_id: int, body: ActivateSkillRequest):
    """激活 Skill 并锁定到会话(plan step 19,spec AC-1/4/5)。

    Args:
        session_id: 会话 ID。
        body: {"skill_name": "office"}。

    Returns:
        200: {"locked_version": str, "frozen_hash": str}
        404: {"detail": "session_not_found" | "skill_not_found"}
        409: {"detail": "skill_switch_not_allowed"}
        400: {"detail": "skill_validation_failed"}
    """
    from private_agent.skills.errors import (
        SkillNotFoundError,
        SkillSwitchNotAllowedError,
        SkillValidationError,
    )

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
            mgr = _build_skill_manager(cfg)
            result = await mgr.activate_skill(
                skill_name=body.skill_name,
                session_id=session_id,
                conn=conn,
            )
            return {
                "locked_version": result["locked_version"],
                "frozen_hash": result["frozen_hash"],
            }
        finally:
            await conn.close()
    except HTTPException:
        raise
    except SkillSwitchNotAllowedError:
        raise HTTPException(status_code=409, detail="skill_switch_not_allowed")
    except SkillValidationError:
        raise HTTPException(status_code=400, detail="skill_validation_failed")
    except SkillNotFoundError:
        raise HTTPException(status_code=404, detail="skill_not_found")
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "activate_failed"},
        )


@router.get("/skills", response_model=None)
async def list_skills():
    """列出所有 enabled Skill(plan step 17)。

    Returns:
        200: [{name, version, description, enabled}]
        500: {"error": "skills_list_failed"}
    """
    try:
        conn = await db.connect()
        try:
            cfg = loader.load_config()
            loader_ = _build_skill_loader(cfg)
            skills = await loader_.list_all(conn)
            return [
                {
                    "name": s.manifest.name,
                    "version": s.manifest.version,
                    "description": s.manifest.description,
                    "enabled": s.manifest.enabled,
                }
                for s in skills
            ]
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "skills_list_failed"},
        )


@router.get("/skills/{skill_name}", response_model=None)
async def get_skill_detail(skill_name: str):
    """获取 Skill 详情(plan step 18)。

    Returns:
        200: {name, version, description, enabled, system_prompt_preview(≤500), tools}
        404: {"detail": "skill_not_found"}
        500: {"error": "skill_detail_failed"}
    """
    from private_agent.skills.errors import SkillNotFoundError

    try:
        conn = await db.connect()
        try:
            cfg = loader.load_config()
            loader_ = _build_skill_loader(cfg)
            skill = await loader_.load(skill_name, conn)
            return {
                "name": skill.manifest.name,
                "version": skill.manifest.version,
                "description": skill.manifest.description,
                "enabled": skill.manifest.enabled,
                "system_prompt_preview": skill.system_prompt[:500],
                "tools": [
                    {
                        "name": t.name,
                        "safety_level_override": t.safety_level_override,
                        "enabled": t.enabled,
                    }
                    for t in skill.manifest.dependencies.tools
                ],
            }
        finally:
            await conn.close()
    except SkillNotFoundError:
        raise HTTPException(status_code=404, detail="skill_not_found")
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "skill_detail_failed"},
        )