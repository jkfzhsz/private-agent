"""Admin 控制面 HTTP 端点(蓝图 §2.10 / §4.2 / §7.4)。

- M1: GET /admin/disk-status 磁盘占用分级
- M2: POST /admin/sessions/{id}/extract_memory 手动记忆提取
- M2: GET /admin/knowledge/stats + POST /admin/knowledge/upload 知识库管理
- M3: POST /admin/sessions/{id}/activate Skill 激活与锁定 (plan step 19)
- M3: GET /admin/skills 列表 + GET /admin/skills/{name} 详情 (plan step 17-18)
"""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import os
import yaml

from private_agent.config import loader
from private_agent.knowledge.kb_repo import KnowledgeBaseRepo
from private_agent.memory.manager import MemoryManager
from private_agent.memory.memories_repo import MemoriesRepo
from private_agent.storage import db
from private_agent.storage.disk_alert import get_disk_status

from private_agent.api.files import _get_outputs_dir

router = APIRouter(prefix="/admin", tags=["admin"])


async def _load_cfg() -> dict:
    """加载 config.yaml 并合并 config_runtime 运行时覆盖(runtime > yaml)。

    设置页修改 provider/MCP 后, 此处读到的即为最新生效配置。
    """
    conn = await db.connect()
    try:
        return await loader.load_config_with_overrides(conn)
    finally:
        await conn.close()


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


def _build_skill_version_listener(cfg):
    """构造 SkillVersionListener(plan m4-version-compare-rollback step 4),测试可 monkeypatch。"""
    from private_agent.eval.hybrid_eval import HybridEvaluator
    from private_agent.eval.runner import EvalRunner
    from private_agent.eval.repos import EvalDatasetRepo, EvalRunRepo, VersionSnapshotRepo
    from private_agent.eval.version_listener import SkillVersionListener
    from private_agent.models.registry import build_default_adapter
    from private_agent.skills.loader import SkillLoader

    # listener 持有 EvalRunner(实际触发时再连接 DB)
    class _LazyRunner:
        """延迟构造 EvalRunner,避免在 import 时连接 DB。"""
        def __init__(self, cfg):
            self._cfg = cfg

        async def run_evaluation(self, *, skill_name, skill_version, model_id,
                                  eval_mode, mock_enabled, sample_subset, conn):
            runner = EvalRunner(
                dataset_repo=EvalDatasetRepo(conn),
                eval_repo=EvalRunRepo(conn),
                snapshot_repo=VersionSnapshotRepo(conn),
                skill_loader=SkillLoader.from_cfg(self._cfg),
                model_adapter=build_default_adapter(self._cfg),
                hybrid_evaluator=HybridEvaluator.from_cfg(self._cfg),
                cfg=self._cfg,
            )
            return await runner.run_evaluation(
                skill_name=skill_name,
                skill_version=skill_version,
                model_id=model_id,
                eval_mode=eval_mode,
                mock_enabled=mock_enabled,
                sample_subset=sample_subset,
                conn=conn,
            )

    return SkillVersionListener(_LazyRunner(cfg), cfg)


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
            # V2 补齐(§4.4 [MVP]): 注入 react_events_insert, 手动提取路径
            # 同样记录 memory_extracted/memory_evicted 事件
            from private_agent.storage.react_events import insert_react_event

            mgr = MemoryManager(
                memories_repo=repo,
                compress_adapter=_build_compress_adapter(cfg),
                react_events_insert=insert_react_event,
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


class CorrectionExtractRequest(BaseModel):
    """阶段三批次3(T3.4, 调研 round2 §4.4.1) - 纠正沉淀请求体。"""

    original: str
    corrected: str


@router.post("/sessions/{session_id}/extract_correction", response_model=None)
async def extract_correction(session_id: int, body: CorrectionExtractRequest):
    """阶段三批次3(T3.4): 用户纠正 → correction 记忆沉淀。

    前端在检测到"用户编辑消息后重发"时调用; 提取走 compress_adapter
    (LLM 定向提取, 无适配器时启发式降级), 落 user_memories(correction 类型)。

    Returns:
        200: {"count": int, "type": "correction"}
        500: {"error": "extract_failed"}
    """
    try:
        conn = await db.connect()
        try:
            repo = MemoriesRepo(conn)
            mgr = MemoryManager(
                memories_repo=repo,
                compress_adapter=_build_compress_adapter(await _load_cfg()),
            )
            memories = await mgr.maybe_extract_from_correction(
                original=body.original, corrected=body.corrected,
            )
            return {
                "count": len(memories),
                "type": "correction",
                "content": memories[0].content if memories else "",
            }
        finally:
            await conn.close()
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
                processor=_build_kb_processor(cfg),
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
# V1.2-6.4 基础 RAG: 知识库列表/删除/文档/文件上传(库 = scenario 分组)
# ══════════════════════════════════════════════════════════════════════════

@router.get("/knowledge", response_model=None)
async def list_knowledge_bases():
    """知识库列表(V1.2-6.4): 按 scenario 分组统计 + 文档清单。"""
    try:
        conn = await db.connect()
        try:
            repo = KnowledgeBaseRepo(conn)
            stats = await repo.get_stats()
            docs = await repo.list_documents(limit=500)
            from collections import defaultdict

            groups: dict[str, dict] = defaultdict(
                lambda: {"scenario": None, "documents": [], "chunks": 0}
            )
            for d in docs:
                sc = d.scenario or "未分类"
                g = groups[sc]
                g["scenario"] = sc
                g["documents"].append({
                    "doc_id": d.id,
                    "source": d.source,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                })
            scenarios = []
            for sc, g in groups.items():
                sc_stats = (stats.get("scenarios") or {}).get(sc, {}) if sc != "未分类" else {}
                g["chunks"] = int(sc_stats.get("chunks", 0))
                scenarios.append(g)
            scenarios.sort(key=lambda g: -len(g["documents"]))
            return {
                "total_documents": int(stats.get("total_documents", 0)),
                "total_chunks": int(stats.get("total_chunks", 0)),
                "bases": scenarios,
            }
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(status_code=503, content={"error": "kb_list_failed"})


@router.delete("/knowledge/{scenario}", response_model=None)
async def delete_knowledge_base(scenario: str):
    """删除知识库(V1.2-6.4): 软删该 scenario 全部文档(soft-delete, 可追溯)。"""
    try:
        conn = await db.connect()
        try:
            repo = KnowledgeBaseRepo(conn)
            docs = await repo.list_documents(scenario=scenario, limit=1000)
            for d in docs:
                if d.id:
                    await repo.deactivate_document(d.id)
            return {"ok": True, "scenario": scenario, "deleted_documents": len(docs)}
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(status_code=503, content={"error": "kb_delete_failed"})


@router.get("/knowledge/{scenario}/documents", response_model=None)
async def list_kb_documents(scenario: str, limit: int = 100):
    """库内文档列表(V1.2-6.4)。"""
    try:
        conn = await db.connect()
        try:
            repo = KnowledgeBaseRepo(conn)
            docs = await repo.list_documents(
                scenario=scenario, limit=min(max(int(limit), 1), 500)
            )
            return [
                {
                    "doc_id": d.id,
                    "source": d.source,
                    "created_at": d.created_at.isoformat() if d.created_at else None,
                }
                for d in docs
            ]
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(status_code=503, content={"error": "kb_documents_failed"})


# ══════════════════════════════════════════════════════════════════════════
# V1.3-7.3 知识库专业升级: 切片配置 / 批量重索引 / 检索测试
# ══════════════════════════════════════════════════════════════════════════


def _build_kb_processor(cfg: dict):
    """按 config_runtime 覆盖构造 DocumentProcessor(切片参数全局生效)。"""
    from private_agent.knowledge.document_processor import (
        DEFAULT_CHUNK_PARAMS,
        DocumentProcessor,
    )

    chunking = (cfg.get("knowledge") or {}).get("chunking") or {}
    params = dict(DEFAULT_CHUNK_PARAMS)
    for doc_type, overrides in chunking.items():
        if not isinstance(overrides, dict):
            continue
        base = dict(params.get(doc_type, {}))
        for k in ("chunk_size", "chunk_overlap"):
            v = overrides.get(k)
            if isinstance(v, (int, float)) and v > 0:
                base[k] = int(v)
        params[doc_type] = base
    return DocumentProcessor(chunk_params=params)


class KnowledgeConfigRequest(BaseModel):
    """PUT /admin/knowledge/config 请求体(V1.3-7.3): 切片参数。

    chunking: {"markdown": {"chunk_size": 512, "chunk_overlap": 64}, ...}
    仅更新传入的类型/键; 不传 = 不更新。
    """

    chunking: dict | None = None


@router.get("/knowledge/config", response_model=None)
async def get_knowledge_config():
    """读取切片配置(yaml + config_runtime 合并, V1.3-7.3)。"""
    cfg = await _load_cfg()
    from private_agent.knowledge.document_processor import DEFAULT_CHUNK_PARAMS

    chunking = dict(DEFAULT_CHUNK_PARAMS)
    merged = (cfg.get("knowledge") or {}).get("chunking") or {}
    for doc_type, overrides in merged.items():
        if isinstance(overrides, dict):
            chunking[doc_type] = {**chunking.get(doc_type, {}), **overrides}
    return {"chunking": chunking}


@router.put("/knowledge/config", response_model=None)
async def update_knowledge_config(body: KnowledgeConfigRequest):
    """修改切片参数(写入 config_runtime, 下次上传/重索引生效, V1.3-7.3)。"""
    if not body.chunking:
        return {"status": "ok", "unchanged": True}
    # 合并写: 逐类型逐键写点分 key
    conn = await db.connect()
    try:
        for doc_type, overrides in body.chunking.items():
            if not isinstance(overrides, dict):
                continue
            for k, v in overrides.items():
                if k not in ("chunk_size", "chunk_overlap"):
                    continue
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    continue
                if iv <= 0:
                    continue
                await _set_runtime(
                    conn, f"knowledge.chunking.{doc_type}.{k}", iv
                )
    finally:
        await conn.close()
    return {"status": "ok"}


class KnowledgeReindexRequest(BaseModel):
    """POST /admin/knowledge/reindex 请求体(V1.3-7.3): 批量重向量化。

    scenario: 目标库(必填)。chunk_size/overlap 可选覆盖(一次性, 不落配置)。
    """

    scenario: str
    chunk_size: int | None = None
    chunk_overlap: int | None = None


@router.post("/knowledge/reindex", response_model=None)
async def reindex_knowledge(body: KnowledgeReindexRequest):
    """批量重索引(V1.3-7.3): 删除该库全部 chunk 后按当前切片配置重切重向量化。

    Returns:
        200: {"ok": true, "documents": n, "chunks": m}
        404: {"error": "kb_not_found"}
        500: {"error": "reindex_failed"}
    """
    scenario = (body.scenario or "").strip()
    if not scenario:
        return JSONResponse(status_code=400, content={"error": "scenario_required"})
    try:
        conn = await db.connect()
        try:
            from private_agent.knowledge.kb_service import KnowledgeBaseService

            cfg = await _load_cfg()
            processor = _build_kb_processor(cfg)
            # 一次性覆盖(不落配置)
            if body.chunk_size or body.chunk_overlap:
                from private_agent.knowledge.document_processor import (
                    DEFAULT_CHUNK_PARAMS,
                )

                params = dict(processor._chunk_params)
                for dt, base in DEFAULT_CHUNK_PARAMS.items():
                    cur = dict(params.get(dt, base))
                    if body.chunk_size:
                        cur["chunk_size"] = body.chunk_size
                    if body.chunk_overlap:
                        cur["chunk_overlap"] = body.chunk_overlap
                    params[dt] = cur
                processor = DocumentProcessor(chunk_params=params)

            repo = KnowledgeBaseRepo(conn)
            docs = await repo.list_documents(
                scenario=scenario, limit=5000
            )
            if not docs:
                return JSONResponse(
                    status_code=404, content={"error": "kb_not_found"}
                )
            # 清空旧 chunk(重向量化)
            await conn.execute(
                "DELETE FROM kb_chunks WHERE scenario = $1", scenario
            )
            svc = KnowledgeBaseService(
                kb_repo=repo, processor=processor,
            )
            total_chunks = 0
            for d in docs:
                if not d.content:
                    continue
                _, chunks = await svc.process_document(
                    content=d.content,
                    filename=d.source or "doc.txt",
                    scenario=scenario,
                    skip_dedup=True,
                )
                total_chunks += len(chunks)
            return {"ok": True, "documents": len(docs), "chunks": total_chunks}
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(
            status_code=500, content={"error": "reindex_failed"}
        )


class KnowledgeSearchTestRequest(BaseModel):
    """POST /admin/knowledge/search_test 请求体(V1.3-7.3): 检索测试。"""

    query: str
    scenario: str | None = None
    top_k: int = 5


@router.post("/knowledge/search_test", response_model=None)
async def knowledge_search_test(body: KnowledgeSearchTestRequest):
    """检索测试面板(V1.3-7.3): 复用生产检索流水线(search_with_rerank)。

    Returns:
        200: {"results": [{chunk_id, text, score, source, doc_type}]}
        400: {"error": "query_required"}
        503: {"error": "search_failed"}
    """
    query = (body.query or "").strip()
    if not query:
        return JSONResponse(status_code=400, content={"error": "query_required"})
    try:
        conn = await db.connect()
        try:
            from private_agent.knowledge.kb_service import KnowledgeBaseService

            cfg = await _load_cfg()
            repo = KnowledgeBaseRepo(conn)
            svc = KnowledgeBaseService(
                kb_repo=repo,
                processor=_build_kb_processor(cfg),
                config=cfg.get("knowledge", {}),
            )
            chunks = await svc.search_with_rerank(
                query=query,
                scenario=body.scenario or None,
                top_k=min(max(int(body.top_k), 1), 20),
            )
            return {
                "results": [
                    {
                        "chunk_id": c.chunk_id,
                        "text": c.text,
                        "score": round(float(c.score or 0), 4),
                        "source": c.source,
                        "doc_type": c.doc_type,
                    }
                    for c in chunks
                ]
            }
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(
            status_code=503, content={"error": "search_failed"}
        )


class KnowledgeFileUploadRequest(BaseModel):
    """POST /admin/knowledge/upload-file 请求体(V1.2-6.4): 文件上传入库。

    filename: 文件名(扩展名决定文档类型)。
    content_base64: 文件内容(base64, 文本文件 utf-8/gbk)。
    scenario: 目标知识库(缺省按文件名/类型归类)。
    """

    filename: str
    content_base64: str
    scenario: str | None = None


@router.post("/knowledge/upload-file", response_model=None)
async def knowledge_upload_file(body: KnowledgeFileUploadRequest):
    """文件上传入库(V1.2-6.4): base64 → 文本 → 切片向量化。

    支持文本类文件(md/txt/csv/json/代码等, utf-8/gbk); 二进制/PDF 请
    先转文本再上传(个人应用, 不内置文档解析器)。
    """
    import base64 as _b64
    import re as _re
    from pathlib import Path

    safe_name = _re.sub(r'[\\/:*?"<>|]', "_", Path(body.filename).name or "upload.txt")
    try:
        decoded = _b64.b64decode(body.content_base64, validate=False)
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": "invalid_file"})
    if len(decoded) > 10 * 1024 * 1024:
        return JSONResponse(status_code=400, content={"error": "file_too_large"})
    # 文本解码(utf-8 优先, gbk 兜底)
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            text = decoded.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return JSONResponse(
            status_code=400,
            content={"error": "unsupported_binary_file"},
        )
    try:
        conn = await db.connect()
        try:
            from private_agent.knowledge.kb_service import KnowledgeBaseService

            cfg = await _load_cfg()
            repo = KnowledgeBaseRepo(conn)
            svc = KnowledgeBaseService(
                kb_repo=repo,
                processor=_build_kb_processor(cfg),
                config=cfg.get("knowledge", {}),
            )
            doc_id, chunks = await svc.process_document(
                content=text,
                filename=safe_name,
                scenario=body.scenario,
            )
            return {"doc_id": doc_id, "chunks": len(chunks), "filename": safe_name}
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(status_code=500, content={"error": "upload_failed"})


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
        404: {"detail": "skill_not_found"}
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
            # 会话懒创建(与 WS user_message 一致):前端随机/首次 session_id
            # 激活时 sessions 无该行则插入,避免 session_not_found
            row = await conn.fetchrow(
                "SELECT id FROM sessions WHERE id = $1", session_id,
            )
            if row is None:
                await conn.execute(
                    "INSERT INTO sessions (id, title) VALUES ($1, $2)",
                    session_id, f"session-{session_id}",
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

    阶段三批次3(T3.2, 调研 round2 §4.3.2): 返回 permissions 摘要
    (安装/激活 UI 展示 Required Permissions)。

    Returns:
        200: [{name, version, description, enabled, permissions}]
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
                    # V1.1-3.6: getattr 防御(兼容 mock/旧 manifest 无新字段)
                    "avatar": getattr(s.manifest, "avatar", ""),
                    "tags": list(getattr(s.manifest, "tags", None) or []),
                    "permissions": _skill_permissions_summary(s.manifest),
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


def _skill_permissions_summary(manifest) -> dict:
    """汇总 Skill 权限声明(阶段三批次3 T3.2)。

    Returns:
        {allow_file_write, allow_network, sandbox_enabled,
         rules: [{tool, paths, domains}]}
    """
    perms = manifest.permissions
    rules = []
    for r in getattr(perms, "rules", []) or []:
        rules.append(
            {
                "tool": r.tool,
                "paths": list(r.paths or []),
                "domains": list(r.domains or []),
            }
        )
    return {
        "allow_file_write": bool(getattr(perms, "allow_file_write", False)),
        "allow_network": bool(getattr(perms, "allow_network", False)),
        "sandbox_enabled": bool(getattr(perms, "sandbox_enabled", False)),
        "max_file_size_mb": int(getattr(perms, "max_file_size_mb", 50)),
        "rules": rules,
    }


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
                # V1.1-3.6: getattr 防御(兼容 mock/旧 manifest 无新字段)
                "avatar": getattr(skill.manifest, "avatar", ""),
                "tags": list(getattr(skill.manifest, "tags", None) or []),
                "model_params": dict(getattr(skill.manifest, "model_params", None) or {}),
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


def _skill_dev_dir() -> Path:
    """skill 开发目录(config skills.storage.dev_dir, 默认 ./skills)。"""
    from pathlib import Path

    cfg = loader.load_config()
    dev_dir = Path(cfg.get("skills", {}).get("storage", {}).get("dev_dir", "./skills"))
    if not dev_dir.is_absolute():
        dev_dir = Path.cwd() / dev_dir
    return dev_dir


class SkillMetaRequest(BaseModel):
    """PUT /admin/skills/{name}/meta 请求体(V1.1-3.6): 智能体元数据。

    description/avatar/enabled/model_params 传 None 不更新; tags 传 None 不更新,
    传 [] 清空。model_params 支持 {temperature, top_p, max_tokens}(仅存元数据,
    运行时 max_tokens 注入, temperature/top_p 视 provider 能力)。
    """

    description: str | None = None
    avatar: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None
    model_params: dict | None = None


@router.put("/skills/{skill_name}/meta", response_model=None)
async def update_skill_meta(skill_name: str, body: SkillMetaRequest):
    """更新智能体基础信息(V1.1-3.6): 写 skill.yaml + 同步 PG skills 表。"""
    import shutil as _shutil
    from pathlib import Path

    try:
        cfg = loader.load_config()
        loader_ = _build_skill_loader(cfg)
        conn = await db.connect()
        try:
            skill = await loader_.load(skill_name, conn)
        finally:
            await conn.close()
    except Exception:
        raise HTTPException(status_code=404, detail="skill_not_found")

    # 1. 更新 skill.yaml
    skill_dir = _skill_dev_dir() / skill_name
    yaml_path = skill_dir / "skill.yaml"
    if not yaml_path.exists():
        raise HTTPException(status_code=404, detail="skill_yaml_not_found")
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if body.description is not None:
            data["description"] = body.description.strip()
        if body.avatar is not None:
            data["avatar"] = body.avatar.strip()
        if body.tags is not None:
            data["tags"] = [str(t).strip() for t in body.tags if str(t).strip()]
        if body.model_params is not None:
            mp = {}
            for k in ("temperature", "top_p", "max_tokens"):
                if body.model_params.get(k) is not None:
                    mp[k] = body.model_params[k]
            data["model_params"] = mp
        if body.enabled is not None:
            data["enabled"] = bool(body.enabled)
        yaml_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except Exception:
        return JSONResponse(status_code=500, content={"error": "skill_meta_save_failed"})

    # 2. 同步 PG skills 表 manifest(存在才更新)
    try:
        conn = await db.connect()
        try:
            await conn.execute(
                """
                UPDATE skills SET manifest = $1, description = $2,
                    is_enabled = $3, updated_at = now()
                WHERE name = $4
                """,
                __import__("json").dumps(data, ensure_ascii=False),
                data.get("description"),
                data.get("enabled", True),
                skill_name,
            )
        finally:
            await conn.close()
    except Exception:
        pass  # PG 同步失败不影响文件系统源
    return {"ok": True, "name": skill_name}


@router.post("/skills/{skill_name}/clone", response_model=None)
async def clone_skill(skill_name: str):
    """克隆智能体(V1.1-3.6): 复制 skill 目录为 {name}-copy + 同步 PG 行。"""
    import re as _re
    import shutil as _shutil
    from pathlib import Path

    src_dir = _skill_dev_dir() / skill_name
    if not src_dir.exists():
        raise HTTPException(status_code=404, detail="skill_not_found")
    new_name = f"{skill_name}-copy"
    if _re.fullmatch(r"[a-z0-9_-]+", new_name) is None:
        new_name = f"{skill_name}-{int(__import__('time').time())}"
    dst_dir = _skill_dev_dir() / new_name
    if dst_dir.exists():
        # 已存在同名副本 → 追加序号
        i = 2
        while dst_dir.exists():
            dst_dir = _skill_dev_dir() / f"{new_name}{i}"
            i += 1
        new_name = dst_dir.name
    try:
        _shutil.copytree(src_dir, dst_dir)
        # 改 yaml name
        yaml_path = dst_dir / "skill.yaml"
        if yaml_path.exists():
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            data["name"] = new_name
            yaml_path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        # 同步 PG 行: 源有 PG 行则复制; 无则从文件系统组装(保证 db_first 可读)
        conn = await db.connect()
        try:
            existing = await conn.fetchrow(
                "SELECT manifest, system_prompt, tools FROM skills WHERE name = $1",
                skill_name,
            )
            if existing is not None:
                import json as _json

                manifest = _json.loads(existing["manifest"]) if existing["manifest"] else {}
                manifest["name"] = new_name
                new_manifest = _json.dumps(manifest, ensure_ascii=False)
                new_prompt = existing["system_prompt"]
                new_tools = existing["tools"] if existing["tools"] else "[]"
                new_version = str((manifest.get("version") or "1.0.0"))
                new_desc = manifest.get("description")
            else:
                import json as _json

                yaml_text = (
                    (dst_dir / "skill.yaml").read_text(encoding="utf-8")
                    if (dst_dir / "skill.yaml").exists() else "{}"
                )
                parsed = yaml.safe_load(yaml_text) or {}
                parsed["name"] = new_name
                new_manifest = _json.dumps(parsed, ensure_ascii=False)
                new_prompt = (
                    (dst_dir / "system_prompt.md").read_text(encoding="utf-8")
                    if (dst_dir / "system_prompt.md").exists() else ""
                )
                tools_file = dst_dir / "tools.yaml"
                tools_parsed = (
                    yaml.safe_load(tools_file.read_text(encoding="utf-8"))
                    if tools_file.exists() else []
                )
                new_tools = _json.dumps(tools_parsed or [], ensure_ascii=False)
                new_version = str(parsed.get("version") or "1.0.0")
                new_desc = parsed.get("description")
            await conn.execute(
                """
                INSERT INTO skills (name, version, description, manifest, system_prompt, tools, is_enabled)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb, TRUE)
                """,
                new_name,
                new_version,
                new_desc,
                new_manifest,
                new_prompt,
                new_tools,
            )
        finally:
            await conn.close()
        return {"ok": True, "name": new_name, "path": str(dst_dir)}
    except Exception:
        return JSONResponse(status_code=500, content={"error": "skill_clone_failed"})


@router.delete("/skills/{skill_name}", response_model=None)
async def delete_skill(skill_name: str):
    """删除技能(V1.1 技能库): 删 skill 目录 + PG 行。

    安全约束: 若存在激活了该技能的活跃会话 → 400 拒绝(防历史会话续聊失败)。
    """
    import shutil as _shutil

    try:
        conn = await db.connect()
        try:
            # 被活跃会话锁定的技能不允许删
            locked = await conn.fetchval(
                """
                SELECT COUNT(*) FROM sessions
                WHERE locked_skill_name = $1 AND status IN ('active', 'interrupted')
                """,
                skill_name,
            )
            if (locked or 0) > 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"skill_in_use: {locked} 个会话正在使用该技能",
                )
            await conn.execute("DELETE FROM skills WHERE name = $1", skill_name)
        finally:
            await conn.close()

        skill_dir = _skill_dev_dir() / skill_name
        if skill_dir.exists():
            _shutil.rmtree(skill_dir)
        return {"ok": True, "name": skill_name, "deleted": True}
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "skill_delete_failed"},
        )


# ══════════════════════════════════════════════════════════════════════════
# V1.2-6.1 技能配置编辑器: 系统提示词读/写 + 自动快照 + token 估算
# ══════════════════════════════════════════════════════════════════════════

class SkillPromptRequest(BaseModel):
    """PUT /admin/skills/{name}/prompt 请求体(V1.2-6.1): 系统提示词全文。"""

    system_prompt: str


@router.get("/skills/{skill_name}/prompt", response_model=None)
async def get_skill_prompt(skill_name: str):
    """读取技能系统提示词(V1.2-6.1): 内容 + token 估算 + 当前版本。"""
    try:
        cfg = loader.load_config()
        loader_ = _build_skill_loader(cfg)
        conn = await db.connect()
        try:
            skill = await loader_.load(skill_name, conn)
        finally:
            await conn.close()
    except Exception:
        raise HTTPException(status_code=404, detail="skill_not_found")
    from private_agent.core.token_estimator import TokenEstimator

    try:
        tokens = TokenEstimator().estimate(skill.system_prompt)
    except Exception:  # noqa: BLE001
        tokens = None
    return {
        "name": skill_name,
        "system_prompt": skill.system_prompt,
        "token_count": tokens,
        "version": skill.manifest.version,
    }


@router.put("/skills/{skill_name}/prompt", response_model=None)
async def update_skill_prompt(skill_name: str, body: SkillPromptRequest):
    """写系统提示词(V1.2-6.1): 落盘 system_prompt.md + 自动快照 + 同步 PG。

    注: 提示词变化会改变 frozen_hash → 旧会话续聊自动重建 Frozen Zone(已有机制)。
    """
    import time as _time

    prompt = body.system_prompt
    skill_dir = _skill_dev_dir() / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail="skill_not_found")
    try:
        conn = await db.connect()
        try:
            from private_agent.eval.repos import VersionSnapshotRepo

            # 1. 自动快照(scope=prompt, 版本=时间戳; 失败不阻塞保存)
            try:
                repo = VersionSnapshotRepo(conn)
                await repo.save(
                    scope="prompt",
                    version=_time.strftime("%Y%m%d%H%M%S"),
                    payload={"skill_name": skill_name, "system_prompt": prompt},
                )
            except Exception:  # noqa: BLE001
                pass
            # 2. 同步 PG skills.system_prompt(存在才更新, db_first 运行时生效)
            await conn.execute(
                "UPDATE skills SET system_prompt = $1, updated_at = now() WHERE name = $2",
                prompt,
                skill_name,
            )
        finally:
            await conn.close()
        # 3. 落盘(skill.yaml 同目录)
        (skill_dir / "system_prompt.md").write_text(prompt, encoding="utf-8")
        from private_agent.core.token_estimator import TokenEstimator

        try:
            tokens = TokenEstimator().estimate(prompt)
        except Exception:  # noqa: BLE001
            tokens = None
        return {"ok": True, "name": skill_name, "token_count": tokens}
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(status_code=500, content={"error": "skill_prompt_save_failed"})


# ══════════════════════════════════════════════════════════════════════════
# 记忆/设置查询端点(V1.5 Phase 1 Task 12, 供前端记忆页/设置页)
# ══════════════════════════════════════════════════════════════════════════


@router.get("/memories", response_model=None)
async def list_memories(type: str | None = None, limit: int = 100, q: str | None = None):
    """查询活跃用户记忆列表(蓝图 §4.3)。

    Args:
        type: 记忆类型过滤(preference/fact/todo/decision,可选)。
        limit: 返回条数上限(默认 100)。
        q: 内容关键字检索(V1.3-7.1, ILIKE 匹配, 可选)。

    Returns:
        200: [{id, type, content, importance, source_session_id, created_at,
               last_accessed_at, access_count}]
        503: {"error": "memories_list_failed"}
    """
    try:
        conn = await db.connect()
        try:
            rows = await conn.fetch(
                """
                SELECT id, type, content, importance, source_session_id,
                       created_at, last_accessed_at, access_count
                FROM user_memories
                WHERE is_active = TRUE
                  AND ($1::text IS NULL OR type = $1)
                  AND ($2::text IS NULL OR content ILIKE '%' || $2 || '%')
                ORDER BY importance DESC, created_at DESC
                LIMIT $3
                """,
                type,
                (q or "").strip() or None,
                min(max(int(limit), 1), 500),
            )
            return [
                {
                    "id": r["id"],
                    "type": r["type"],
                    "content": r["content"],
                    "importance": r["importance"],
                    "source_session_id": r["source_session_id"],
                    "created_at": (
                        r["created_at"].isoformat() if r["created_at"] else None
                    ),
                    "last_accessed_at": (
                        r["last_accessed_at"].isoformat()
                        if r["last_accessed_at"] else None
                    ),
                    "access_count": r["access_count"],
                }
                for r in rows
            ]
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "memories_list_failed"},
        )


class MemoryCreateRequest(BaseModel):
    """POST /memories 请求体(V1.3-7.1): 手动新增记忆。

    content: 记忆内容(必填非空)。
    type: 记忆类型(preference/fact/todo/decision/correction, 默认 fact)。
    importance: 重要性 0~1(默认 0.5)。
    """

    content: str
    type: str = "fact"
    importance: float = 0.5


@router.post("/memories", response_model=None)
async def create_memory(body: MemoryCreateRequest):
    """手动新增用户记忆(V1.3-7.1)。

    Returns:
        200: {"ok": true, "id": int}
        400: {"error": "memory_invalid_content"}
        503: {"error": "memory_create_failed"}
    """
    content = (body.content or "").strip()
    if not content:
        return JSONResponse(
            status_code=400, content={"error": "memory_invalid_content"}
        )
    mtype = body.type if body.type in (
        "preference", "fact", "todo", "decision", "correction"
    ) else "fact"
    importance = min(max(float(body.importance), 0.0), 1.0)
    try:
        conn = await db.connect()
        try:
            mid = await conn.fetchval(
                """
                INSERT INTO user_memories (user_id, type, content, importance)
                VALUES (1, $1, $2, $3)
                RETURNING id
                """,
                mtype, content, importance,
            )
        finally:
            await conn.close()
        return {"ok": True, "id": mid}
    except Exception:
        return JSONResponse(
            status_code=503, content={"error": "memory_create_failed"}
        )


@router.delete("/memories/{memory_id}", response_model=None)
async def delete_memory(memory_id: int):
    """软删除记忆(V1.3-7.1): is_active=FALSE, 保留历史供审计。

    Returns:
        200: {"ok": true}
        404: {"error": "memory_not_found"}
    """
    try:
        conn = await db.connect()
        try:
            row = await conn.fetchrow(
                """
                UPDATE user_memories
                SET is_active = FALSE
                WHERE id = $1 AND is_active = TRUE
                RETURNING id
                """,
                memory_id,
            )
        finally:
            await conn.close()
        if row is None:
            return JSONResponse(
                status_code=404, content={"error": "memory_not_found"}
            )
        return {"ok": True}
    except Exception:
        return JSONResponse(
            status_code=503, content={"error": "memory_delete_failed"}
        )


@router.get("/settings/providers", response_model=None)
async def get_providers():
    """返回模型 provider 配置状态(蓝图 §2.7, API key 只返回是否已配置)。

    Returns:
        200: {
            "providers": [{name, enabled, model_name, base_url, api_key_configured}],
            "fallback_chain": [str, ...],
        }
    """
    import os

    from private_agent.config.loader import resolve_provider_limits

    cfg = await _load_cfg()
    providers = cfg.get("models", {}).get("providers", {})
    result = []
    for name, prov in providers.items():
        if prov.get("deleted"):
            continue  # 已删除的 provider 不展示
        env_var = f"PA_{name.upper()}_API_KEY"
        key_val = os.environ.get(env_var, "")
        result.append({
            "name": name,
            "enabled": prov.get("enabled", True),
            "model_name": prov.get("model_name"),
            "base_url": prov.get("base_url"),
            # V1.4-8.2: 分组元数据(前端分组展示)
            "group": prov.get("group"),
            "sort_order": prov.get("sort_order", 0),
            "kind": prov.get("kind", "cloud"),
            # 不返回 key 明文,仅返回是否已配置(非空且非 test-key 占位)
            "api_key_configured": bool(key_val) and key_val != "test-key",
            # per-provider 对话参数上限(已解析: provider 级 > 全局默认)
            "limits": resolve_provider_limits(cfg, name),
        })
    return {
        "providers": result,
        "fallback_chain": cfg.get("models", {}).get("router", {}).get(
            "fallback_chain", []
        ),
    }


# ══════════════════════════════════════════════════════════════════════════
# 设置页编辑: provider 配置更新 / 连通性测试 (Phase 1 补足)
# ══════════════════════════════════════════════════════════════════════════


async def _set_runtime(conn, key: str, value) -> None:
    """upsert 一条 config_runtime 记录(点分 key + JSONB value)。"""
    import json as _json

    await conn.execute(
        """
        INSERT INTO config_runtime (key, value)
        VALUES ($1, $2::jsonb)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        key,
        _json.dumps(value),
    )


def _ensure_master_key() -> bytes:
    """确保 PA_MASTER_KEY 可用: env > user_env > backend/.env > 生成。

    2026-08-06 打包版修复: 原实现只写 backend/.env, 打包后 resourcesPath
    只读 → 写失败 → 每次启动生成新 key → 已加密的 provider API key 解密
    失败(设置页"key 全清")。现改为优先写 Electron 用户配置
    %APPDATA%/Private Agent/backend.env(打包版与 dev 均在此读写)。
    """
    import os

    from private_agent.config import secrets

    hex_key = os.environ.get("PA_MASTER_KEY", "")
    if not hex_key:
        hex_key = _read_env_map(_user_env_path()).get("PA_MASTER_KEY", "")
    if not hex_key:
        # 继承 dev 历史 backend/.env(保持 master key 一致 → 旧 provider key 可解密)
        try:
            workspace = os.path.expandvars(
                loader.load_config().get("system", {}).get("workspace_root", ".")
            )
            hex_key = _read_env_map(os.path.join(workspace, ".env")).get(
                "PA_MASTER_KEY", ""
            )
        except Exception:  # noqa: BLE001
            pass
    if not hex_key:
        import secrets as _secrets

        hex_key = _secrets.token_hex(32)  # 64 hex chars = 32 bytes
        _write_env_updates(_user_env_path(), {"PA_MASTER_KEY": hex_key})
    os.environ["PA_MASTER_KEY"] = hex_key
    return bytes.fromhex(hex_key)


def _user_env_path() -> str:
    """Electron sidecar 的用户可写配置位置(打包版与 dev 统一):
    Windows: %APPDATA%\\Private Agent\\backend.env; 其他平台: XDG 配置目录。"""
    import os

    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Private Agent", "backend.env")
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(xdg, "Private Agent", "backend.env")


def _read_env_map(path: str) -> dict:
    """读取 .env 文件为 {KEY: VALUE}(跳过注释/空行/非法行)。"""
    out: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                k, v = stripped.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _write_env_updates(path: str, updates: dict) -> None:
    """更新/新增 .env 的 KEY=VALUE: 保留原文件注释与其他 key, 重复 key 覆盖。"""
    import os

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    lines: list[str] = []
    seen: set[str] = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if (
                    stripped
                    and not stripped.startswith("#")
                    and "=" in stripped
                    and stripped.split("=", 1)[0].strip() in updates
                ):
                    k = stripped.split("=", 1)[0].strip()
                    lines.append(f"{k}={updates[k]}\n")
                    seen.add(k)
                    continue
                lines.append(line)
    for k, v in updates.items():
        if k not in seen:
            lines.append(f"{k}={v}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _ensure_admin_token_for_user_env() -> str:
    """确保 PA_ADMIN_TOKEN 稳定(打包版 backend/.env 只读时写入 user_env)。"""
    import os
    import secrets as _secrets

    token = os.environ.get("PA_ADMIN_TOKEN")
    if token:
        return token
    # 现有 backend/.env 的 token 优先继承(与 dev 一致)
    token = _read_env_map(_user_env_path()).get("PA_ADMIN_TOKEN", "")
    if not token:
        try:
            workspace = os.path.expandvars(
                loader.load_config().get("system", {}).get("workspace_root", ".")
            )
            token = _read_env_map(os.path.join(workspace, ".env")).get(
                "PA_ADMIN_TOKEN", ""
            )
        except Exception:  # noqa: BLE001
            pass
    if not token:
        token = _secrets.token_hex(32)
        _write_env_updates(_user_env_path(), {"PA_ADMIN_TOKEN": token})
    os.environ["PA_ADMIN_TOKEN"] = token
    return token


class DatabaseSettingsRequest(BaseModel):
    """PUT /settings/database 请求体: 数据库连接配置(密码仅本地 .env 存储)。

    master_key(可选): 旧的 PA_MASTER_KEY(64 hex)。提供则写入 user_env,
    保证与历史环境一致的 AES 密钥 → 已加密的 provider API key 可解密
    (否则每次环境重建生成新 key, 设置页表现为"key 全清")。
    """

    host: str | None = None
    port: int | None = None
    name: str | None = None
    user: str | None = None
    password: str | None = None
    master_key: str | None = None


@router.get("/settings/database", response_model=None)
async def get_database_settings():
    """返回数据库连接配置状态(密码不回显; master key 明文供备份/迁移)。

    2026-08-06: 不依赖 DB 可用 —— 首次配置(DB 未连接)时也返回 200,
    展示 yaml 默认值 + env(PA_DB_*) 覆盖; 不再 _load_cfg()(需连 DB)。

    Returns:
        200: {
            host, port, name, user,          # env(PA_DB_*) > config.yaml
            password_configured,             # PA_DB_PASSWORD 是否可用
            master_key_configured,           # PA_MASTER_KEY 是否稳定
            master_key,                      # 明文(本机回环+admin token 保护),
                                             # 供用户查看/备份
            env_file,                        # 用户配置写入位置
        }
    """
    env_path = _user_env_path()
    # yaml 默认值(load_config 不连 DB) + env 覆盖(PA_DB_*)
    try:
        db_cfg = loader.load_config().get("database", {}) or {}
    except Exception:  # noqa: BLE001
        db_cfg = {}
    host = os.environ.get("PA_DB_HOST") or db_cfg.get("host", "")
    port = os.environ.get("PA_DB_PORT") or str(db_cfg.get("port", ""))
    name = os.environ.get("PA_DB_NAME") or db_cfg.get("name", "")
    user = os.environ.get("PA_DB_USER") or db_cfg.get("user", "")
    mk = os.environ.get("PA_MASTER_KEY") or _read_env_map(env_path).get(
        "PA_MASTER_KEY", ""
    )
    if not mk:
        # 兜底: 未持久化时生成并写入(正常已由启动链路完成)
        try:
            mk = _ensure_master_key()
            mk = mk.hex() if not isinstance(mk, str) else mk
        except Exception:  # noqa: BLE001
            mk = ""
    return {
        "host": host,
        "port": port,
        "name": name,
        "user": user,
        "password_configured": bool(
            os.environ.get("PA_DB_PASSWORD")
            or _read_env_map(env_path).get("PA_DB_PASSWORD")
        ),
        "master_key_configured": bool(mk),
        "master_key": mk,
        "env_file": env_path,
        # 2026-08-06: 数据库可达性(配置后重启生效; 首次未配置时不可达是正常的)
        "db_reachable": await _probe_db_reachable(),
    }


async def _probe_db_reachable() -> bool:
    """探测数据库是否可连接(快速失败, 不抛异常)。"""
    try:
        conn = await db.connect()
        try:
            await conn.fetchval("SELECT 1")
            return True
        finally:
            await conn.close()
    except Exception:  # noqa: BLE001
        return False


@router.put("/settings/database", response_model=None)
async def update_database_settings(body: DatabaseSettingsRequest):
    """保存数据库连接配置(2026-08-06 打包版首启能力, 不依赖 DB 可用)。

    - host/port/name/user/password → 写 Electron 用户配置
      %APPDATA%/Private Agent/backend.env(PA_DB_HOST/PORT/NAME/USER/
      PASSWORD) —— build_dsn 优先读 env, 首次配置时 DB 连不上也能保存
      (修复"401/500 鸡生蛋": 旧实现写 config_runtime 需 DB, 密码未设时
      DB 连不上 → 500)
    - **password 可选(2026-08-06)**: 已配置时留空 = 不修改(改 host/name
      无需重输密码); 首次未配置且为空 → 400
    - 同步确保 PA_MASTER_KEY / PA_ADMIN_TOKEN 稳定(user_env)
    - 若 DB 可用, 连接参数也写 config_runtime(运行中覆盖, 容错跳过)
    - 生效: 需重启应用(sidecar DB 连接池启动时创建)

    Returns:
        200: {"saved": true, "env_file": str, "need_restart": true,
              "message": "已保存, 重启应用后生效"}
        400: 首次配置 password 缺失 / master_key 非法
    """
    env_path = _user_env_path()
    password_configured = bool(
        os.environ.get("PA_DB_PASSWORD") or _read_env_map(env_path).get("PA_DB_PASSWORD")
    )
    if not body.password and not password_configured:
        return JSONResponse(
            status_code=400,
            content={"error": "database password is required(首次配置必填)"},
        )
    # 密钥稳定化(写入 user_env; master key 继承 dev 历史值以解密旧 provider key)
    if body.master_key:
        mk = body.master_key.strip()
        if len(mk) != 64:
            return JSONResponse(
                status_code=400,
                content={"error": "master_key 必须为 64 位 hex(PA_MASTER_KEY)"},
            )
        os.environ["PA_MASTER_KEY"] = mk
        _write_env_updates(_user_env_path(), {"PA_MASTER_KEY": mk})
    _ensure_master_key()
    _ensure_admin_token_for_user_env()
    # DB 连接参数全量写 user_env(build_dsn env 优先, 重启即生效);
    # 密码已配置且本次留空 → 不覆盖(保留原密码)
    env_updates: dict = {}
    if body.password:
        env_updates["PA_DB_PASSWORD"] = body.password
    if body.host:
        env_updates["PA_DB_HOST"] = body.host.strip()
    if body.port:
        env_updates["PA_DB_PORT"] = str(int(body.port))
    if body.name:
        env_updates["PA_DB_NAME"] = body.name.strip()
    if body.user:
        env_updates["PA_DB_USER"] = body.user.strip()
    _write_env_updates(_user_env_path(), env_updates)
    # 可选: DB 可用时同步写 config_runtime(运行中覆盖; 首次配置 DB 不可用
    # 则跳过 —— env 已覆盖, 不影响重启生效)
    try:
        conn = await db.connect()
        try:
            if body.host:
                await _set_runtime(conn, "database.host", body.host)
            if body.port:
                await _set_runtime(conn, "database.port", int(body.port))
            if body.name:
                await _set_runtime(conn, "database.name", body.name)
            if body.user:
                await _set_runtime(conn, "database.user", body.user)
        finally:
            await conn.close()
    except Exception:  # noqa: BLE001
        pass  # DB 不可用(首次配置)时跳过 config_runtime, env 已生效
    return {
        "saved": True,
        "env_file": _user_env_path(),
        "need_restart": True,
        "message": "数据库配置已保存, 重启应用后生效",
    }


class ProviderUpdateRequest(BaseModel):
    """PUT /settings/providers/{name} 请求体(至少一项)。

    含 per-provider 对话参数上限(覆盖全局 models.limits 默认)。
    V1.4-8.2: group/sort_order/kind 分组元数据(展示用, 不影响运行)。
    """

    base_url: str | None = None
    model_name: str | None = None
    enabled: bool | None = None
    api_key: str | None = None  # 提供才更新; 明文仅走 HTTPS/本机回环, 加密后存库
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_turns: int | None = None
    group: str | None = None
    sort_order: int | None = None
    kind: str | None = None  # cloud | local


@router.put("/settings/providers/{name}", response_model=None)
async def update_provider(name: str, body: ProviderUpdateRequest):
    """更新模型 provider 配置(运行时覆盖, config_runtime 优先级 > yaml)。

    - base_url/model_name/enabled → config_runtime(下次加载配置即生效)
    - api_key 提供时 → AES-256-GCM 加密存 config_runtime + 同步设置环境变量(热生效)
    """
    import os

    # 校验 provider 存在
    cfg = await _load_cfg()
    providers = cfg.get("models", {}).get("providers", {})
    if name not in providers:
        raise HTTPException(status_code=404, detail=f"provider '{name}' not found")
    _validate_provider_name(name)

    conn = await db.connect()
    try:
        prefix = f"models.providers.{name}"
        if body.base_url is not None:
            await _set_runtime(conn, f"{prefix}.base_url", body.base_url)
        if body.model_name is not None:
            await _set_runtime(conn, f"{prefix}.model_name", body.model_name)
        if body.enabled is not None:
            await _set_runtime(conn, f"{prefix}.enabled", bool(body.enabled))

        # V1.4-8.2: 分组元数据(group 空串=清除, sort_order/kind 直接落)
        if body.group is not None:
            g = (body.group or "").strip()
            if g:
                await _set_runtime(conn, f"{prefix}.group", g)
            else:
                await conn.execute(
                    "DELETE FROM config_runtime WHERE key = $1",
                    f"{prefix}.group",
                )
        if body.sort_order is not None:
            await _set_runtime(conn, f"{prefix}.sort_order", int(body.sort_order))
        if body.kind is not None:
            k = (body.kind or "").strip()
            if k in ("cloud", "local"):
                await _set_runtime(conn, f"{prefix}.kind", k)

        # per-provider 对话参数上限(0 表示删除覆盖回退全局默认, 空表示不更新)
        for key, field in (
            ("max_input_tokens", body.max_input_tokens),
            ("max_output_tokens", body.max_output_tokens),
            ("max_turns", body.max_turns),
        ):
            if field is None:
                continue
            if field <= 0:
                await conn.execute(
                    "DELETE FROM config_runtime WHERE key = $1",
                    f"{prefix}.{key}",
                )
                continue
            min_val = (
                256 if key == "max_input_tokens"
                else (64 if key == "max_output_tokens" else 1)
            )
            await _set_runtime(
                conn, f"{prefix}.{key}", max(min_val, int(field))
            )

        if body.api_key is not None and body.api_key.strip():
            master = _ensure_master_key()
            from private_agent.config import secrets

            encrypted = secrets.encrypt_api_key(body.api_key.strip(), master)
            await _set_runtime(conn, f"{prefix}.api_key_encrypted", encrypted)
            # 热生效: 本进程适配器直接读环境变量
            os.environ[f"PA_{name.upper()}_API_KEY"] = body.api_key.strip()
    finally:
        await conn.close()
    return {"ok": True, "name": name}


def _validate_provider_name(name: str) -> None:
    """校验 provider 名称为合法标识符(字母/数字/下划线/连字符)。

    provider 名会映射为环境变量 PA_{NAME}_API_KEY 与 config_runtime 点分 key,
    含空格/中文等非法字符会导致 API key 无法生效、配置错乱。
    """
    import re

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        raise HTTPException(
            status_code=400,
            detail=(
                f"模型名称 '{name}' 不合法: 只能包含字母/数字/下划线/连字符, "
                "且不能以数字或符号开头(如 deepseek-flash, glm-4)"
            ),
        )


class ProviderCreateRequest(BaseModel):
    """POST /settings/providers 请求体: 新增模型 provider。"""

    name: str
    base_url: str
    model_name: str
    enabled: bool = True
    api_key: str | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_turns: int | None = None


@router.post("/settings/providers", response_model=None)
async def create_provider(body: ProviderCreateRequest):
    """新增模型 provider(任意 OpenAI 兼容服务, 动态注册)。

    - 配置写 config_runtime(models.providers.{name}.*), 与 yaml provider 同等对待
    - enabled 时自动加入 fallback_chain 尾部(整体列表写 runtime)
    - api_key 可选, 提供则 AES 加密存储 + 热生效
    """
    import os

    name = body.name.strip()
    if not name or not body.base_url.strip():
        raise HTTPException(status_code=400, detail="name 与 base_url 必填")
    _validate_provider_name(name)
    import json as _json

    conn = await db.connect()
    try:
        # 已存在且未删除 → 拒绝(避免误覆盖)
        existing = await conn.fetchval(
            "SELECT value FROM config_runtime WHERE key = $1",
            f"models.providers.{name}.deleted",
        )
        if existing is not None and existing is False:
            raise HTTPException(status_code=409, detail=f"provider '{name}' 已存在")
        if existing is True:
            # 重新启用: 清除删除标记
            await conn.execute(
                "DELETE FROM config_runtime WHERE key = $1",
                f"models.providers.{name}.deleted",
            )
            await _set_runtime(conn, f"models.providers.{name}.enabled", True)

        prefix = f"models.providers.{name}"
        await _set_runtime(conn, f"{prefix}.base_url", body.base_url.strip())
        await _set_runtime(conn, f"{prefix}.model_name", body.model_name.strip())
        await _set_runtime(conn, f"{prefix}.enabled", bool(body.enabled))
        if body.api_key and body.api_key.strip():
            master = _ensure_master_key()
            from private_agent.config import secrets

            encrypted = secrets.encrypt_api_key(body.api_key.strip(), master)
            await _set_runtime(conn, f"{prefix}.api_key_encrypted", encrypted)
            os.environ[f"PA_{name.upper()}_API_KEY"] = body.api_key.strip()
        for key, field in (
            ("max_input_tokens", body.max_input_tokens),
            ("max_output_tokens", body.max_output_tokens),
            ("max_turns", body.max_turns),
        ):
            if field and field > 0:
                await _set_runtime(conn, f"{prefix}.{key}", int(field))

        # 加入 fallback_chain(整体列表存 runtime, 避免写 yaml)
        if body.enabled:
            row = await conn.fetchval(
                "SELECT value FROM config_runtime WHERE key = 'models.router.fallback_chain'"
            )
            if row:
                chain = _json.loads(row) if isinstance(row, str) else row
            else:
                cfg = await _load_cfg()
                chain = list(
                    cfg.get("models", {}).get("router", {}).get("fallback_chain", [])
                )
            if name not in chain:
                chain.append(name)
                await _set_runtime(conn, "models.router.fallback_chain", chain)
    finally:
        await conn.close()
    return {"ok": True, "name": name}


@router.delete("/settings/providers/{name}", response_model=None)
async def delete_provider(name: str):
    """删除模型 provider(软删: deleted 标记 + 禁用 + 移出 fallback_chain)。

    config.yaml 的静态 provider 不可物理删除, 用 runtime 标记屏蔽;
    已删除的可通过 POST /settings/providers 同名重新创建。
    """
    import json as _json

    conn = await db.connect()
    try:
        await _set_runtime(conn, f"models.providers.{name}.deleted", True)
        await _set_runtime(conn, f"models.providers.{name}.enabled", False)
        # 清除加密 key(重新创建时必须重新录入, 避免误用旧凭据)
        await conn.execute(
            "DELETE FROM config_runtime WHERE key = $1",
            f"models.providers.{name}.api_key_encrypted",
        )
        # 移出 fallback_chain
        row = await conn.fetchval(
            "SELECT value FROM config_runtime WHERE key = 'models.router.fallback_chain'"
        )
        if row:
            chain = _json.loads(row) if isinstance(row, str) else row
        else:
            cfg = await _load_cfg()
            chain = list(
                cfg.get("models", {}).get("router", {}).get("fallback_chain", [])
            )
        if name in chain:
            chain = [c for c in chain if c != name]
            await _set_runtime(conn, "models.router.fallback_chain", chain)
    finally:
        await conn.close()
    return {"ok": True, "name": name}


@router.post("/settings/providers/{name}/test", response_model=None)
async def test_provider(name: str):
    """连通性测试: 用当前配置(含 key)实际调用一次模型。

    Returns:
        200: {"ok": true, "provider", "sample"} | {"ok": false, "provider", "error"}
    """
    import os

    cfg = await _load_cfg()
    env_var = f"PA_{name.upper()}_API_KEY"
    key_val = os.environ.get(env_var, "")
    if not key_val or key_val == "test-key":
        return {"ok": False, "provider": name, "error": "未配置 API Key(可在设置中录入)"}
    try:
        from private_agent.models.registry import get_adapter

        adapter = get_adapter(name, cfg)
        result = await adapter.chat(
            [{"role": "user", "content": "hi"}],
            tools=None,
        )
        sample = (result.content or result.reasoning_content or "")[:80]
        return {"ok": True, "provider": name, "sample": sample}
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "provider": name,
            "error": f"{type(e).__name__}: {e}",
        }


# ══════════════════════════════════════════════════════════════════════════
# §6.14 [MVP] 沙箱配置管理 UI(蓝图 §6.14: GET/PUT config + POST test)
# ══════════════════════════════════════════════════════════════════════════


class SandboxConfigUpdateRequest(BaseModel):
    """PUT /admin/settings/sandbox 请求体(至少一项, 空表示不更新)。"""

    enabled: bool | None = None
    cpu_timeout_sec: int | None = None
    memory_limit_mb: int | None = None
    disk_limit_mb: int | None = None
    network_enabled: bool | None = None
    code_scan_enabled: bool | None = None
    env_sanitization_enabled: bool | None = None
    retention_days: int | None = None


class SandboxTestRequest(BaseModel):
    """POST /admin/settings/sandbox/test 请求体。"""

    code: str = "print('sandbox ok')"
    language: str = "python"


@router.get("/settings/sandbox", response_model=None)
async def get_sandbox_config():
    """读取沙箱配置(yaml + config_runtime 运行时覆盖合并, 蓝图 §6.14)。"""
    cfg = await _load_cfg()
    return cfg.get("sandbox", {})


@router.put("/settings/sandbox", response_model=None)
async def update_sandbox_config(body: SandboxConfigUpdateRequest):
    """修改沙箱运行时配置(写入 config_runtime, 下次执行生效, 蓝图 §6.14)。

    支持项: enabled / limits(cpu_timeout_sec, memory_limit_mb,
    disk_limit_mb, network_enabled) / security(code_scan_enabled,
    env_sanitization_enabled) / retention_days。
    """
    conn = await db.connect()
    try:
        if body.enabled is not None:
            await _set_runtime(conn, "sandbox.enabled", bool(body.enabled))
        if body.cpu_timeout_sec is not None:
            await _set_runtime(conn, "sandbox.limits.cpu_timeout_sec", body.cpu_timeout_sec)
        if body.memory_limit_mb is not None:
            await _set_runtime(conn, "sandbox.limits.memory_limit_mb", body.memory_limit_mb)
        if body.disk_limit_mb is not None:
            await _set_runtime(conn, "sandbox.limits.disk_limit_mb", body.disk_limit_mb)
        if body.network_enabled is not None:
            await _set_runtime(conn, "sandbox.limits.network_enabled", bool(body.network_enabled))
        if body.code_scan_enabled is not None:
            await _set_runtime(conn, "sandbox.security.code_scan_enabled", bool(body.code_scan_enabled))
        if body.env_sanitization_enabled is not None:
            await _set_runtime(
                conn,
                "sandbox.security.env_sanitization_enabled",
                bool(body.env_sanitization_enabled),
            )
        if body.retention_days is not None:
            await _set_runtime(conn, "sandbox.retention_days", body.retention_days)
    finally:
        await conn.close()
    return {"status": "ok"}


@router.post("/settings/sandbox/test", response_model=None)
async def test_sandbox(body: SandboxTestRequest):
    """测试沙箱执行: 运行示例代码验证当前配置可用(蓝图 §6.14)。"""
    cfg = await _load_cfg()
    from private_agent.sandbox.service import SandboxService

    try:
        svc = SandboxService(cfg)
        result = await svc.execute(
            code=body.code,
            language=body.language,
            timeout=15,
            session_id="sandbox-config-test",
        )
        return {
            "ok": result.exit_code == 0,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"{type(e).__name__}: {e}",
            "duration_ms": 0,
        }


class MemoryConfigUpdateRequest(BaseModel):
    """PUT /settings/memory 请求体(V1.3-7.1): 记忆注入强度/开关配置。

    全部可选, 传 None 不更新。写入 config_runtime, 下一轮生效。
    """

    enabled: bool | None = None
    inject_limit: int | None = None
    extract_interval_turns: int | None = None
    eviction_max_active_count: int | None = None
    eviction_min_importance_threshold: float | None = None
    eviction_expire_days: int | None = None


@router.get("/settings/memory", response_model=None)
async def get_memory_config():
    """读取记忆配置(yaml + config_runtime 合并, V1.3-7.1)。"""
    cfg = await _load_cfg()
    mem = cfg.get("memory", {}) or {}
    eviction = mem.get("eviction", {}) or {}
    return {
        "enabled": mem.get("enabled", True),
        "inject_limit": mem.get("inject_limit", 10),
        "extract_interval_turns": mem.get("extract_interval_turns", 8),
        "eviction": {
            "max_active_count": eviction.get("max_active_count", 200),
            "min_importance_threshold": eviction.get(
                "min_importance_threshold", 0.3
            ),
            "expire_days": eviction.get("expire_days", 30),
        },
    }


@router.put("/settings/memory", response_model=None)
async def update_memory_config(body: MemoryConfigUpdateRequest):
    """修改记忆配置(写入 config_runtime, 下一轮生效, V1.3-7.1)。

    enabled=False 等价于全部会话 memory_enabled=False(不注入/不提取)。
    """
    conn = await db.connect()
    try:
        if body.enabled is not None:
            await _set_runtime(conn, "memory.enabled", bool(body.enabled))
        if body.inject_limit is not None:
            await _set_runtime(
                conn, "memory.inject_limit",
                min(max(int(body.inject_limit), 1), 50),
            )
        if body.extract_interval_turns is not None:
            await _set_runtime(
                conn, "memory.extract_interval_turns",
                min(max(int(body.extract_interval_turns), 1), 100),
            )
        if body.eviction_max_active_count is not None:
            await _set_runtime(
                conn, "memory.eviction.max_active_count",
                min(max(int(body.eviction_max_active_count), 10), 5000),
            )
        if body.eviction_min_importance_threshold is not None:
            await _set_runtime(
                conn, "memory.eviction.min_importance_threshold",
                min(max(float(body.eviction_min_importance_threshold), 0.0), 1.0),
            )
        if body.eviction_expire_days is not None:
            await _set_runtime(
                conn, "memory.eviction.expire_days",
                min(max(int(body.eviction_expire_days), 1), 3650),
            )
    finally:
        await conn.close()
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════
# V1.4-8.3 系统设置完善: 存储路径 / 缓存清理 / 代理 / 日志 / master key
# ══════════════════════════════════════════════════════════════════════════


@router.post("/cache/clear", response_model=None)
async def clear_cache():
    """清理运行时缓存(V1.4-8.3)。

    - outputs/ 产物目录: 删除超过 retention 天(默认 7)的临时文件
    - Python 进程内: 触发 token_estimator / MCP tools 缓存重建(如有)
    Returns:
        200: {"ok": true, "cleaned_files": n, "freed_bytes": n}
    """
    from datetime import datetime, timezone

    retention_days = 7
    try:
        cfg = await _load_cfg()
        retention_days = int(
            (cfg.get("system") or {}).get("logs", {}).get(
                "retention_days", 7
            )
        )
    except Exception:  # noqa: BLE001
        pass
    root = _workspace_root()
    outputs_dir = root / "outputs"
    cleaned = 0
    freed = 0
    now = datetime.now(timezone.utc)
    if outputs_dir.exists():
        for p in outputs_dir.rglob("*"):
            if not p.is_file():
                continue
            try:
                age = now - datetime.fromtimestamp(
                    p.stat().st_mtime, tz=timezone.utc
                )
                if age.days >= retention_days:
                    size = p.stat().st_size
                    p.unlink(missing_ok=True)
                    cleaned += 1
                    freed += size
            except OSError:
                continue
    return {"ok": True, "cleaned_files": cleaned, "freed_bytes": freed,
            "retention_days": retention_days}


class SystemSettingsRequest(BaseModel):
    """PUT /settings/system 请求体(V1.4-8.3): 系统级设置。

    workspace_root: 工作区存储路径(改动影响文件工具路径, 建议重启后完整生效)。
    log_level: 日志级别(DEBUG/INFO/WARNING/ERROR)。
    log_retention_days: logs 日志保留天数(清理用)。
    proxy_http / proxy_https: 网络代理地址(空串清除)。
    """

    workspace_root: str | None = None
    log_level: str | None = None
    log_retention_days: int | None = None
    proxy_http: str | None = None
    proxy_https: str | None = None


@router.get("/settings/system", response_model=None)
async def get_system_settings():
    """读取系统设置 + master key 状态(V1.4-8.3)。"""
    import os

    cfg = await _load_cfg()
    sys_cfg = cfg.get("system", {}) or {}
    logs_cfg = sys_cfg.get("logs", {}) or {}
    proxy = sys_cfg.get("proxy", {}) or {}
    master_key = os.environ.get("PA_MASTER_KEY", "")
    return {
        "app_name": sys_cfg.get("app_name", "Private Agent"),
        "version": sys_cfg.get("version", "0.1.0"),
        "workspace_root": str(_workspace_root()),
        "log_level": (sys_cfg.get("sidecar") or {}).get("log_level", "INFO"),
        "log_retention_days": logs_cfg.get("retention_days", 7),
        "proxy_http": proxy.get("http"),
        "proxy_https": proxy.get("https"),
        "master_key_configured": bool(master_key) and master_key != "test-key",
        "database": cfg.get("database", {}).get("name", "private_agent"),
    }


@router.put("/settings/system", response_model=None)
async def update_system_settings(body: SystemSettingsRequest):
    """修改系统设置(写入 config_runtime, V1.4-8.3)。

    workspace_root 与代理: 存入配置供启动/加载时应用(运行时改存储路径
    需重启后端完整生效); log_level 即时由日志模块读取。
    """
    conn = await db.connect()
    try:
        if body.workspace_root is not None and body.workspace_root.strip():
            await _set_runtime(
                conn, "system.workspace_root", body.workspace_root.strip()
            )
        if body.log_level is not None:
            lvl = (body.log_level or "").strip().upper()
            if lvl in ("DEBUG", "INFO", "WARNING", "ERROR"):
                await _set_runtime(
                    conn, "system.sidecar.log_level", lvl
                )
        if body.log_retention_days is not None:
            await _set_runtime(
                conn, "system.logs.retention_days",
                min(max(int(body.log_retention_days), 1), 3650),
            )
        if body.proxy_http is not None:
            ph = (body.proxy_http or "").strip()
            if ph:
                await _set_runtime(conn, "system.proxy.http", ph)
            else:
                await conn.execute(
                    "DELETE FROM config_runtime WHERE key = 'system.proxy.http'"
                )
        if body.proxy_https is not None:
            ps = (body.proxy_https or "").strip()
            if ps:
                await _set_runtime(conn, "system.proxy.https", ps)
            else:
                await conn.execute(
                    "DELETE FROM config_runtime WHERE key = 'system.proxy.https'"
                )
    finally:
        await conn.close()
    return {"status": "ok"}


class PermissionModeUpdateRequest(BaseModel):
    """权限模式更新请求体。"""

    session_id: int
    mode: str


@router.get("/settings/permission", response_model=None)
async def get_permission_config(session_id: int = 1):
    """读取会话权限模式与支持的模式列表(阶段三批次1 T1.2)。

    Args:
        session_id: 会话 ID(默认 1, 桌面单用户场景)。
    """
    from private_agent.tools.permission_manager import PERMISSION_MODES

    conn = await db.connect()
    try:
        mode = (
            await conn.fetchval(
                "SELECT permission_mode FROM sessions WHERE id = $1", session_id
            )
            or "default"
        )
    finally:
        await conn.close()
    return {
        "session_id": session_id,
        "mode": mode,
        "modes": list(PERMISSION_MODES),
        "mode_descriptions": {
            "default": "默认: safe 自动 / elevated 确认(60s 超时拒绝 + 会话缓存)",
            "plan": "计划模式: 只读放行, 写操作每次确认(不缓存)",
            "acceptEdits": "编辑模式: 文件类工具自动批准, 其余 elevated 走确认",
            "cautious": "谨慎模式: 确认结果不缓存, 每次都询问",
            "deny_all": "全拒模式: 所有工具调用直接拦截",
        },
    }


@router.put("/settings/permission", response_model=None)
async def update_permission_config(body: PermissionModeUpdateRequest):
    """修改会话权限模式(阶段三批次1 T1.2)。

    写入 sessions.permission_mode; 运行中 PermissionManager 在下一轮
    user_message 时由 _sync_permission_manager 同步(模式变化清缓存)。

    修复(2026-08-04 设置页排查): 原 UPDATE 在会话不存在时 404, 而实际
    会话 id 非 1(会话被删除/重建后)导致设置页权限模式切换永远失败 →
    改 upsert, 任何 session_id 都可设置(不存在则创建占位会话记录)。
    """
    from private_agent.tools.permission_manager import PERMISSION_MODES

    if body.mode not in PERMISSION_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"invalid mode: {body.mode!r} (expected {list(PERMISSION_MODES)})",
        )
    conn = await db.connect()
    try:
        await conn.execute(
            """
            INSERT INTO sessions (id, permission_mode, title)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE SET permission_mode = EXCLUDED.permission_mode
            """,
            body.session_id, body.mode, f"session-{body.session_id}",
        )
    finally:
        await conn.close()
    return {"status": "ok", "session_id": body.session_id, "mode": body.mode}


# ── 阶段三批次 2(B-1, 调研 round2 §4.2.2): Hooks 管理 ───────────────────────


class HookItemRequest(BaseModel):
    """Hook 配置项请求体(与 HookConfig 字段对齐)。"""

    name: str
    event: str
    type: str = "command"
    command: str | None = None
    url: str | None = None
    mcp_server: str | None = None
    mcp_tool: str | None = None
    timeout: float = 5.0
    enabled: bool = True


async def _read_hooks(cfg: dict) -> list[dict]:
    """读取 hooks 配置(yaml + config_runtime 合并, runtime > yaml)。"""
    hooks = cfg.get("hooks") or []
    if not isinstance(hooks, list):
        return []
    return [h for h in hooks if isinstance(h, dict)]


async def _write_hooks(hooks: list[dict]) -> None:
    """写 hooks 到 config_runtime(运行时覆盖, 重启保留)。"""
    conn = await db.connect()
    try:
        if hooks:
            await _set_runtime(conn, "hooks", hooks)
        else:
            await conn.execute("DELETE FROM config_runtime WHERE key = 'hooks'")
    finally:
        await conn.close()


@router.get("/hooks", response_model=None)
async def list_hooks():
    """列出全部 hooks 配置(阶段三批次2 B-1)。"""
    cfg = await _load_cfg()
    return {"hooks": await _read_hooks(cfg)}


@router.post("/hooks", response_model=None)
async def create_hook(body: HookItemRequest):
    """新增 hook(阶段三批次2 B-1)。"""
    from private_agent.core.hooks import HookRunner

    try:
        HookRunner.config_from_dict(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    cfg = await _load_cfg()
    hooks = await _read_hooks(cfg)
    if any(h.get("name") == body.name for h in hooks):
        raise HTTPException(status_code=409, detail=f"hook '{body.name}' already exists")
    hooks.append(body.model_dump())
    await _write_hooks(hooks)
    return {"status": "ok", "hook": body.model_dump()}


@router.put("/hooks/{name}", response_model=None)
async def update_hook(name: str, body: HookItemRequest):
    """更新 hook(按 name 定位; 阶段三批次2 B-1)。"""
    from private_agent.core.hooks import HookRunner

    if body.name != name:
        raise HTTPException(status_code=422, detail="name mismatch")
    try:
        HookRunner.config_from_dict(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    cfg = await _load_cfg()
    hooks = await _read_hooks(cfg)
    idx = next((i for i, h in enumerate(hooks) if h.get("name") == name), None)
    if idx is None:
        raise HTTPException(status_code=404, detail=f"hook '{name}' not found")
    hooks[idx] = body.model_dump()
    await _write_hooks(hooks)
    return {"status": "ok", "hook": body.model_dump()}


@router.delete("/hooks/{name}", response_model=None)
async def delete_hook(name: str):
    """删除 hook(阶段三批次2 B-1)。"""
    cfg = await _load_cfg()
    hooks = await _read_hooks(cfg)
    remaining = [h for h in hooks if h.get("name") != name]
    if len(remaining) == len(hooks):
        raise HTTPException(status_code=404, detail=f"hook '{name}' not found")
    await _write_hooks(remaining)
    return {"status": "ok", "deleted": name}


@router.get("/hooks/events", response_model=None)
async def list_hook_events():
    """返回支持的事件与类型(前端表单用; 阶段三批次2 B-1)。"""
    from private_agent.core.hooks import HOOK_EVENTS, HOOK_TYPES

    return {
        "events": list(HOOK_EVENTS),
        "types": list(HOOK_TYPES),
        "event_descriptions": {
            "user_prompt_submit": "用户消息提交(可拒绝/修改)",
            "pre_tool_use": "工具执行前(permissionDecision 决策)",
            "post_tool_use": "工具执行后(注入 additionalContext)",
            "stop": "收尾前(可阻止过早收尾)",
            "pre_compact": "上下文压缩前(关键信息 flush)",
            "permission_request": "权限确认请求(外部策略接管)",
        },
    }


@router.get("/sessions", response_model=None)
async def list_sessions(
    limit: int = 50,
    has_messages: bool = True,
    folder: str | None = None,
):
    """列出历史会话(供侧边栏任务树, 蓝图 §2.10)。

    Args:
        limit: 返回条数上限(默认 50)。
        has_messages: 仅返回有真实对话消息的会话(默认 True, 过滤掉
            测试/占位产生的空会话, 让任务树只显示日常对话)。
        folder: 按文件夹过滤; "unfiled"=未分组; None=全部(V1.1-3.1)。

    Returns:
        200: [{
            id, title, status, model_id, summary, folder,
            locked_skill_name, locked_skill_version,
            created_at, updated_at, last_turn,
        }]
        503: {"error": "sessions_list_failed"}
    """
    try:
        conn = await db.connect()
        try:
            rows = await conn.fetch(
                """
                SELECT s.id, s.title, s.status, s.model_id, s.summary, s.folder,
                       s.locked_skill_name, s.locked_skill_version,
                       s.created_at, s.updated_at,
                       COALESCE(
                           (SELECT MAX(turn) FROM messages WHERE session_id = s.id),
                           0
                       ) AS last_turn,
                       COALESCE(
                           (SELECT msg_count FROM (
                               SELECT COUNT(*) AS msg_count
                               FROM messages m
                               WHERE m.session_id = s.id AND m.role = 'user'
                           ) t), 0
                       ) AS user_msg_count,
                       (
                           SELECT content FROM messages m
                           WHERE m.session_id = s.id AND m.role = 'user'
                           ORDER BY m.id ASC LIMIT 1
                       ) AS first_user_content
                FROM sessions s
                WHERE (NOT $2::bool
                   -- 仅"发生过对话"的会话入历史任务: 至少有一条 AI 回复
                   -- (只有提问没有回答/被打断的空会话不显示)
                   OR EXISTS (
                       SELECT 1 FROM messages m
                       WHERE m.session_id = s.id AND m.role = 'assistant'
                   ))
                  -- V1.5 项-1(ADR-012 R9): 过滤子代理会话(委派产生的
                  -- kind='sub' 会话不出现在历史任务树, 防污染)
                  AND (s.kind IS NULL OR s.kind <> 'sub')
                  AND ($3::text IS NULL
                       OR ($3 = 'unfiled' AND s.folder IS NULL)
                       OR s.folder = $3::text)
                ORDER BY s.updated_at DESC
                LIMIT $1
                """,
                min(max(int(limit), 1), 200),
                bool(has_messages),
                folder,
            )
            result = []
            for r in rows:
                title = r["title"] or r["summary"]
                # 懒创建占位标题(session-{id})视为无标题, 走首条用户消息兜底
                if not title or (
                    isinstance(title, str) and title.startswith("session-")
                ):
                    first = r["first_user_content"] or ""
                    title = first[:30].replace("\n", " ") if first else f"#{r['id']}"
                result.append({
                    "id": r["id"],
                    "title": title,
                    "status": r["status"],
                    "model_id": r["model_id"],
                    "summary": r["summary"],
                    "folder": r["folder"],
                    "locked_skill_name": r["locked_skill_name"],
                    "locked_skill_version": r["locked_skill_version"],
                    "user_msg_count": r["user_msg_count"],
                    "created_at": (
                        r["created_at"].isoformat() if r["created_at"] else None
                    ),
                    "updated_at": (
                        r["updated_at"].isoformat() if r["updated_at"] else None
                    ),
                    "last_turn": r["last_turn"],
                })
            return result
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "sessions_list_failed"},
        )


@router.get("/subagents", response_model=None)
async def list_subagents(session_id: int, parent_turn: int | None = None):
    """V1.5 项-1(ADR-012 §3.4 R7): 子代理列表 DB 轮询兜底。

    WS 事件(heartbeat/stalled)断线会丢, 前端子任务卡片以此端点全量重建;
    watchdog 判定依赖 DB 不依赖 WS(可靠性在 DB 侧)。

    Args:
        session_id: 父会话 id(必选)。
        parent_turn: 触发委派的轮次; None=该会话全部轮次。

    Returns:
        200: [{
            id, parent_task, prompt, model_id, status, result, error,
            tool_calls, restart_attempts, last_heartbeat_at, started_at,
            stalled_at, finished_at, created_at, sub_session_id,
        }] 按 id 升序
        400: session_id 缺失/非法
        503: {"error": "subagents_list_failed"}
    """
    if session_id <= 0:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid session_id"},
        )
    try:
        conn = await db.connect()
        try:
            if parent_turn is None:
                rows = await conn.fetch(
                    "SELECT * FROM subagents WHERE session_id=$1 ORDER BY id",
                    session_id,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM subagents "
                    "WHERE session_id=$1 AND parent_turn=$2 ORDER BY id",
                    session_id,
                    int(parent_turn),
                )
            result = []
            for r in rows:
                result.append({
                    "id": r["id"],
                    "parent_task": r["parent_task"],
                    "prompt": r["prompt"],
                    "model_id": r["model_id"],
                    "status": r["status"],
                    "result": r["result"],
                    "error": r["error"],
                    "tool_calls": r["tool_calls"],
                    "restart_attempts": r["restart_attempts"],
                    "sub_session_id": r["session_id"],  # 子代理独立会话 id
                    "last_heartbeat_at": (
                        r["last_heartbeat_at"].isoformat()
                        if r["last_heartbeat_at"] else None
                    ),
                    "started_at": (
                        r["started_at"].isoformat() if r["started_at"] else None
                    ),
                    "stalled_at": (
                        r["stalled_at"].isoformat() if r["stalled_at"] else None
                    ),
                    "finished_at": (
                        r["finished_at"].isoformat() if r["finished_at"] else None
                    ),
                    "created_at": (
                        r["created_at"].isoformat() if r["created_at"] else None
                    ),
                })
            return result
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "subagents_list_failed"},
        )


class WorkspaceRequest(BaseModel):
    """PUT /sessions/{id}/workspace 请求体: 会话工作区目录(画地为牢)。"""

    workspace: str | None = None  # None/空 = 清除(回退默认工作目录)


@router.get("/workspaces", response_model=None)
async def list_workspaces():
    """返回候选工作区目录列表(画地为牢选择器用)。

    Returns:
        200: {
            "workspaces": [str, ...],   # 候选目录(默认工作区 + 常用项目根)
            "default": str,             # 当前默认工作目录(config system.workspace_root)
        }
    """
    import os

    cfg = await _load_cfg()
    workspace_root = os.path.expandvars(
        str(cfg.get("system", {}).get("workspace_root", ""))
    )
    candidates: list[str] = []
    # 项目根常见目录(存在才返回)
    for p in (
        workspace_root,
        "D:/Private agent",
        "D:/WorkBuddy Tata",
        os.path.expanduser("~/Desktop"),
    ):
        if p and os.path.isdir(p) and p not in candidates:
            candidates.append(p)
    return {"workspaces": candidates, "default": workspace_root}


@router.put("/sessions/{session_id}/workspace", response_model=None)
async def set_session_workspace(session_id: int, body: WorkspaceRequest):
    """设置会话工作区目录(画地为牢: agent 操作范围 = 选定目录)。

    workspace 为 None/空 → 清除, 回退默认工作目录。
    路径须存在且为目录(校验失败返回 400)。

    Returns:
        200: {"ok": True, "session_id": int, "workspace": str | None}
        400: {"error": "workspace_invalid"}
        404: {"error": "session_not_found"}
    """
    import os

    ws = (body.workspace or "").strip()
    if ws:
        ws = os.path.expandvars(ws)
        if not os.path.isdir(ws):
            return JSONResponse(
                status_code=400, content={"error": "workspace_invalid"}
            )
    conn = await db.connect()
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM sessions WHERE id=$1", session_id
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        await conn.execute(
            "UPDATE sessions SET workspace=$1, updated_at=now() WHERE id=$2",
            ws or None, session_id,
        )
    finally:
        await conn.close()
    return {"ok": True, "session_id": session_id, "workspace": ws or None}


@router.delete("/sessions/{session_id}", response_model=None)
async def delete_session(session_id: int):
    """删除会话及其所有 messages(messages 表 FK ON DELETE CASCADE)。

    用于清理历史任务树里的测试/遗留 session, 让任务树只保留真实对话。
    """
    try:
        conn = await db.connect()
        try:
            # 不允许删当前活跃会话(简单保护, 防止误删正在用的会话)
            row = await conn.fetchrow(
                "SELECT id FROM sessions WHERE id = $1", session_id
            )
            if row is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            await conn.execute("DELETE FROM sessions WHERE id = $1", session_id)
        finally:
            await conn.close()
        return {"ok": True, "id": session_id}
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "session_delete_failed"},
        )


class SessionCreateRequest(BaseModel):
    """POST /admin/sessions 请求体(V1.1-3.1 会话管理闭环)。

    全部可选: 前端新建会话时通常只传空的 {}。
    """

    title: str | None = None
    folder: str | None = None
    skill_name: str | None = None


class SessionUpdateRequest(BaseModel):
    """PUT /admin/sessions/{id} 请求体(V1.1-3.1 + V1.3-7.2)。

    仅更新传入的非空字段; title=None 不触碰, status 必须为
    ('active','interrupted','archived','error') 之一。
    auto_execute/max_rounds(V1.3-7.2): 会话级自动连续执行配置。
    """

    title: str | None = None
    status: str | None = None
    auto_execute: bool | None = None
    max_rounds: int | None = None


class SessionFolderRequest(BaseModel):
    """PUT /admin/sessions/{id}/folder 请求体(V1.1-3.1): 设置/清除文件夹。

    folder: 文件夹名(非空), 或 None/空串 = 移出分组(置 NULL)。
    """

    folder: str | None = None


@router.post("/sessions", response_model=None)
async def create_session(body: SessionCreateRequest | None = None):
    """新建会话(V1.1-3.1 会话管理闭环)。

    前端"新建会话"入口: 创建一条空会话并返回 id, 前端切换过去。
    """
    body = body or SessionCreateRequest()
    title = (body.title or "").strip() or None
    folder = (body.folder or "").strip() or None
    skill_name = (body.skill_name or "").strip() or None
    try:
        conn = await db.connect()
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO sessions (title, folder, locked_skill_name, status)
                VALUES ($1, $2, $3, 'active')
                RETURNING id, created_at
                """,
                title,
                folder,
                skill_name,
            )
            return {"ok": True, "id": row["id"], "created_at": row["created_at"].isoformat()}
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "session_create_failed"},
        )


@router.put("/sessions/{session_id}", response_model=None)
async def update_session(session_id: int, body: SessionUpdateRequest):
    """重命名 / 归档 / 取消归档会话(V1.1-3.1)。

    - title 非空 → 更新标题(空串清除标题)
    - status 合法 → 更新状态; archived → 同时置 archived_at; 非 archived → 清除 archived_at
    """
    status = (body.status or "").strip() if body.status is not None else None
    if status is not None and status not in ("active", "interrupted", "archived", "error"):
        raise HTTPException(status_code=400, detail=f"invalid_status: {status}")
    try:
        conn = await db.connect()
        try:
            exists = await conn.fetchval(
                "SELECT id FROM sessions WHERE id = $1", session_id
            )
            if exists is None:
                raise HTTPException(status_code=404, detail="session_not_found")

            sets: list[str] = []
            params: list = []
            if body.title is not None:
                params.append((body.title or "").strip() or None)
                sets.append(f"title = ${len(params)}")
            if status is not None:
                if status == "archived":
                    params.append(status)
                    sets.append(f"status = ${len(params)}")
                    sets.append("archived_at = now()")
                else:
                    params.append(status)
                    sets.append(f"status = ${len(params)}")
                    sets.append("archived_at = NULL")
            if body.auto_execute is not None:
                params.append(bool(body.auto_execute))
                sets.append(f"auto_execute = ${len(params)}")
            if body.max_rounds is not None:
                params.append(min(max(int(body.max_rounds), 1), 20))
                sets.append(f"max_rounds = ${len(params)}")
            if not sets:
                return {"ok": True, "id": session_id, "unchanged": True}

            sets.append("updated_at = now()")
            params.append(session_id)
            await conn.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE id = ${len(params)}",
                *params,
            )
            return {"ok": True, "id": session_id}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "session_update_failed"},
        )


@router.put("/sessions/{session_id}/folder", response_model=None)
async def set_session_folder(session_id: int, body: SessionFolderRequest):
    """设置/清除会话文件夹(V1.1-3.1 会话管理闭环)。

    body.folder: 非空 → 置入该文件夹; None/空 → 移出分组(folder=NULL)。
    """
    folder = (body.folder or "").strip() or None
    try:
        conn = await db.connect()
        try:
            exists = await conn.fetchval(
                "SELECT id FROM sessions WHERE id = $1", session_id
            )
            if exists is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            await conn.execute(
                "UPDATE sessions SET folder = $1, updated_at = now() WHERE id = $2",
                folder,
                session_id,
            )
            return {"ok": True, "id": session_id, "folder": folder}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "session_folder_failed"},
        )


@router.get("/sessions/search", response_model=None)
async def search_sessions(q: str = "", limit: int = 50):
    """历史会话全文搜索(V1.1-3.2)。

    匹配范围: 会话 title / summary / 消息全文(含归档会话, 满足"搜索覆盖归档"约定)。
    Returns:
        200: [{id, title, status, folder, summary, msg_count, updated_at, hit_snippet}]
    """
    q = (q or "").strip()
    if not q:
        return []
    pattern = f"%{q}%"
    try:
        conn = await db.connect()
        try:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (s.id)
                       s.id, s.title, s.status, s.folder, s.summary,
                       s.updated_at,
                       m.content AS hit_content,
                       (SELECT COUNT(*) FROM messages c
                        WHERE c.session_id = s.id) AS msg_count
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id
                WHERE s.title ILIKE $1 OR s.summary ILIKE $1 OR m.content ILIKE $1
                ORDER BY s.id, s.updated_at DESC
                LIMIT $2
                """,
                pattern,
                min(max(int(limit), 1), 100),
            )
            result = []
            for r in rows:
                title = r["title"] or r["summary"]
                if not title or (isinstance(title, str) and title.startswith("session-")):
                    title = f"#{r['id']}"
                hit = r["hit_content"] or ""
                # 命中片段: 截取关键词附近文本
                idx = hit.lower().find(q.lower())
                start = max(0, idx - 40)
                snippet = ("…" if start > 0 else "") + hit[start:start + 120] + ("…" if start + 120 < len(hit) else "")
                result.append({
                    "id": r["id"],
                    "title": title,
                    "status": r["status"],
                    "folder": r["folder"],
                    "msg_count": r["msg_count"],
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                    "hit_snippet": snippet[:160],
                })
            return result
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "sessions_search_failed"},
        )


@router.get("/sessions/{session_id}/export", response_model=None)
async def export_session(session_id: int, format: str = "md"):
    """导出会话(V1.1-3.2): format=md|json。

    - md: 对话流 Markdown(User/Assistant 分节)
    - json: 完整结构(含 meta + 全部消息, 压缩/工具消息含原始字段)
    前端拿到 content 后 Blob 下载; PDF 由前端打印实现, 后端不生成。
    """
    fmt = (format or "md").lower()
    if fmt not in ("md", "json"):
        raise HTTPException(status_code=400, detail=f"invalid_format: {format}")
    try:
        conn = await db.connect()
        try:
            meta = await conn.fetchrow(
                "SELECT id, title, status, folder, summary, model_id, "
                "locked_skill_name, created_at, updated_at FROM sessions WHERE id = $1",
                session_id,
            )
            if meta is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            msgs = await conn.fetch(
                "SELECT id, turn, role, content, tool_calls, tool_call_id, name, "
                "created_at FROM messages WHERE session_id = $1 "
                "ORDER BY id ASC",
                session_id,
            )
            title = meta["title"] or f"会话 #{session_id}"
            from datetime import datetime, timezone
            exported_at = datetime.now(timezone.utc).isoformat()
            if fmt == "json":
                content = {
                    "meta": {
                        "id": meta["id"],
                        "title": title,
                        "status": meta["status"],
                        "folder": meta["folder"],
                        "summary": meta["summary"],
                        "model_id": meta["model_id"],
                        "locked_skill_name": meta["locked_skill_name"],
                        "created_at": meta["created_at"].isoformat() if meta["created_at"] else None,
                        "updated_at": meta["updated_at"].isoformat() if meta["updated_at"] else None,
                    },
                    "messages": [
                        {
                            "id": m["id"],
                            "turn": m["turn"],
                            "role": m["role"],
                            "content": m["content"],
                            "tool_calls": m["tool_calls"],
                            "tool_call_id": m["tool_call_id"],
                            "name": m["name"],
                            "created_at": m["created_at"].isoformat() if m["created_at"] else None,
                        }
                        for m in msgs
                    ],
                }
            else:
                lines = [f"# {title}", "", f"> 导出时间: {exported_at}", ""]
                for m in msgs:
                    role_label = {
                        "user": "用户",
                        "assistant": "私人智能体",
                        "tool": "工具",
                        "system": "系统",
                    }.get(m["role"], m["role"])
                    body = m["content"] or ""
                    if m["role"] == "tool":
                        body = f"工具: {m['name'] or m['tool_call_id'] or '?'}\n\n```\n{body}\n```"
                    lines.append(f"## {role_label}")
                    lines.append("")
                    lines.append(body)
                    lines.append("")
                content = "\n".join(lines)

            return {
                "ok": True,
                "format": fmt,
                "title": title,
                "session_id": session_id,
                "content": content,
            }
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "session_export_failed"},
        )


# ══════════════════════════════════════════════════════════════════════════
# V1.4-8.1 导入导出与备份体系: 全局备份 / 事务还原 / 会话批量导出
# ══════════════════════════════════════════════════════════════════════════


def _skill_dev_dir() -> Path:
    """技能源目录(config skills.storage.dev_dir, 相对 backend cwd)。"""
    from pathlib import Path

    cfg = loader.load_config()
    dev = (cfg.get("skills") or {}).get("storage", {}).get("dev_dir", "./skills")
    return Path(os.path.expandvars(str(dev))).resolve()


async def _dump_json_lines(conn, table: str) -> list[dict]:
    """导出表全量数据(JSON 行), 兼容 asyncpg JSONB 返回 str 的约定。"""
    import base64 as _b64

    rows = await conn.fetch(f"SELECT * FROM {table}")
    out = []
    for r in rows:
        d = dict(r)
        for k, v in d.items():
            if isinstance(v, (bytes, memoryview)):
                d[k] = {"__bytes_b64__": _b64.b64encode(bytes(v)).decode("ascii")}
            elif isinstance(v, str) and k in (
                "manifest", "tools", "tool_calls", "compressed_from", "payload",
            ):
                # JSONB 文本字段保持原样(json.dumps 时再序列化)
                pass
        out.append(d)
    return out


@router.get("/backup", response_model=None)
async def create_backup():
    """全局一键备份(V1.4-8.1): config_runtime + skills 目录 + 核心表打包 zip。

    表: sessions / messages / messages_archive / user_memories /
        kb_documents / react_events。kb_chunks 不导出(向量大, 还原后重建)。
    config_runtime 含 API Key 密文(备份=完整还原, 请妥善保管 zip)。
    """
    from datetime import datetime, timezone
    from io import BytesIO
    import json as _json
    import zipfile

    root = _workspace_root()
    buffer = BytesIO()
    try:
        conn = await db.connect()
        try:
            tables = (
                "sessions", "messages", "messages_archive", "user_memories",
                "kb_documents", "react_events",
            )
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                meta = {
                    "app": "private-agent",
                    "backup_version": 1,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "tables": list(tables),
                    "notes": "kb_chunks 未导出, 还原后在知识库页重索引重建",
                }
                zf.writestr("backup.json", _json.dumps(meta, ensure_ascii=False, indent=1))
                # config_runtime(asyncpg JSONB 返回 str, 需 json.loads)
                cr = await conn.fetch("SELECT key, value FROM config_runtime")
                zf.writestr(
                    "config_runtime.json",
                    _json.dumps(
                        {
                            r["key"]: (
                                _json.loads(r["value"])
                                if isinstance(r["value"], str)
                                else r["value"]
                            )
                            for r in cr
                        },
                        ensure_ascii=False, indent=1,
                    ),
                )
                # 表数据
                for t in tables:
                    rows = await _dump_json_lines(conn, t)
                    zf.writestr(
                        f"db/{t}.json",
                        _json.dumps(rows, ensure_ascii=False, default=str),
                    )
                # skills 源目录(仅 manifest/prompt/tools/小素材, 跳过输出与日志)
                dev = _skill_dev_dir()
                if dev.exists():
                    for p in sorted(dev.rglob("*")):
                        if p.is_file() and p.stat().st_size <= 2 * 1024 * 1024:
                            rel = p.relative_to(dev).as_posix()
                            if rel.startswith(("outputs/", "logs/", "__pycache__/")):
                                continue
                            zf.write(str(p), f"skills/{rel}")
        finally:
            await conn.close()
        from fastapi.responses import Response

        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="pa-backup-{ts}.zip"'
            },
        )
    except Exception:
        return JSONResponse(status_code=500, content={"error": "backup_failed"})


class SessionBatchExportRequest(BaseModel):
    """POST /sessions/export_batch 请求体(V1.4-8.1): 批量导出。"""

    session_ids: list[int]
    format: str = "md"


@router.post("/sessions/export_batch", response_model=None)
async def export_sessions_batch(body: SessionBatchExportRequest):
    """会话批量导出(V1.4-8.1): 多会话合并导出(md/json 单文件)。"""
    fmt = (body.format or "md").lower()
    if fmt not in ("md", "json"):
        raise HTTPException(status_code=400, detail=f"invalid_format: {fmt}")
    if not body.session_ids:
        raise HTTPException(status_code=400, detail="session_ids_required")
    import json as _json
    try:
        conn = await db.connect()
        try:
            from datetime import datetime, timezone

            exported_at = datetime.now(timezone.utc).isoformat()
            parts: list[str] = []
            json_data: dict = {"exported_at": exported_at, "sessions": []}
            for sid in body.session_ids:
                meta = await conn.fetchrow(
                    "SELECT id, title, status, summary, model_id, created_at "
                    "FROM sessions WHERE id = $1",
                    sid,
                )
                if meta is None:
                    continue
                msgs = await conn.fetch(
                    "SELECT id, turn, role, content, name, created_at "
                    "FROM messages WHERE session_id = $1 ORDER BY id ASC",
                    sid,
                )
                title = meta["title"] or f"会话 #{sid}"
                if fmt == "json":
                    json_data["sessions"].append({
                        "meta": {
                            "id": meta["id"], "title": title,
                            "status": meta["status"], "summary": meta["summary"],
                            "model_id": meta["model_id"],
                        },
                        "messages": [
                            {
                                "turn": m["turn"], "role": m["role"],
                                "content": m["content"], "name": m["name"],
                            }
                            for m in msgs
                        ],
                    })
                    continue
                parts.append(f"# {title}")
                parts.append("")
                for m in msgs:
                    label = {
                        "user": "用户", "assistant": "私人智能体",
                        "tool": "工具", "system": "系统",
                    }.get(m["role"], m["role"])
                    body = m["content"] or ""
                    if m["role"] == "tool":
                        body = f"工具: {m['name'] or '?'}\n\n```\n{body}\n```"
                    parts.append(f"## {label}")
                    parts.append("")
                    parts.append(body)
                    parts.append("")
                parts.append("---")
                parts.append("")
            content = (
                _json.dumps(json_data, ensure_ascii=False, indent=1)
                if fmt == "json" else "\n".join(parts)
            )
            return {"ok": True, "format": fmt, "content": content,
                    "exported_sessions": len(json_data["sessions"]) if fmt == "json"
                    else parts.count("# ")}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500, content={"error": "export_batch_failed"}
        )


@router.post("/backup/restore", response_model=None)
async def restore_backup(file: UploadFile = File(...)):
    """上传备份 zip 还原(V1.4-8.1, 事务回滚保护)。

    流程: 解析 zip(校验 backup.json) → DB 恢复在单事务内执行(任一失败
    整体回滚) → 提交后落盘 skills 目录(失败仅警告)。kb_chunks 不还原,
    返回 chunks_rebuild_pending 提示到知识库页重索引。
    """
    import base64 as _b64
    import json as _json
    import zipfile
    from io import BytesIO as _BytesIO

    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="invalid_backup_zip")
    try:
        raw = await file.read()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid_backup_zip")
    if len(raw) > 200 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="backup_too_large")

    try:
        zf = zipfile.ZipFile(_BytesIO(raw))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid_backup_zip")
    names = set(zf.namelist())
    if "backup.json" not in names:
        raise HTTPException(status_code=400, detail="not_a_backup: backup.json missing")
    try:
        meta = _json.loads(zf.read("backup.json").decode("utf-8"))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="backup.json corrupted")

    def _load_json(name: str) -> list[dict]:
        if name not in names:
            return []
        return _json.loads(zf.read(name).decode("utf-8"))

    tables = ("sessions", "messages", "messages_archive", "user_memories",
              "kb_documents", "react_events")
    table_data: dict[str, list[dict]] = {
        t: _load_json(f"db/{t}.json") for t in tables
    }
    # config_runtime
    cr_data: dict = {}
    if "config_runtime.json" in names:
        cr_data = _json.loads(zf.read("config_runtime.json").decode("utf-8"))

    conn = await db.connect()
    restored = {}
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            # config_runtime 全量替换
            await conn.execute("DELETE FROM config_runtime")
            for k, v in cr_data.items():
                await conn.execute(
                    "INSERT INTO config_runtime (key, value) VALUES ($1, $2::jsonb)",
                    k, _json.dumps(v, ensure_ascii=False),
                )
            # 各表: 清空 + 批量插入(保留原 id)
            from datetime import datetime as _dt

            for t in tables:
                await conn.execute(f"DELETE FROM {t}")
                rows = table_data[t]
                for r in rows:
                    cols = list(r.keys())
                    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
                    vals = []
                    for c in cols:
                        v = r[c]
                        if (
                            isinstance(v, dict)
                            and "__bytes_b64__" in v
                        ):
                            vals.append(_b64.b64decode(v["__bytes_b64__"]))
                        elif (
                            isinstance(v, str)
                            and c.endswith("_at")
                            and v
                        ):
                            # datetime 列: ISO 字符串 → datetime(备份 default=str 序列化)
                            try:
                                vals.append(_dt.fromisoformat(v))
                            except ValueError:
                                vals.append(v)
                        else:
                            vals.append(v)
                    await conn.execute(
                        f"INSERT INTO {t} ({', '.join(cols)}) "
                        f"VALUES ({placeholders})",
                        *vals,
                    )
                restored[t] = len(rows)
            await tr.commit()
        except Exception:  # noqa: BLE001
            await tr.rollback()
            return JSONResponse(
                status_code=500,
                content={"error": "restore_failed_rolled_back"},
            )
    finally:
        await conn.close()

    # 提交后落盘 skills(失败仅警告, 不阻塞)
    skills_ok = True
    try:
        dev = _skill_dev_dir()
        dev.mkdir(parents=True, exist_ok=True)
        for name in names:
            if not name.startswith("skills/"):
                continue
            rel = name[len("skills/"):]
            if not rel:
                continue
            target = (dev / rel).resolve()
            if target != dev and dev not in target.parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
    except Exception:  # noqa: BLE001
        skills_ok = False

    return {
        "ok": True,
        "restored": restored,
        "skills_restored": skills_ok,
        "chunks_rebuild_pending": True,
        "hint": "kb_chunks 未在备份中, 请到知识库页对各库执行重索引",
    }


@router.get("/sessions/{session_id}/turn/{turn}/messages", response_model=None)
async def list_turn_messages(session_id: int, turn: int):
    """查询某轮的全部消息(V1.1-3.3, 供前端收藏/删除定位 msg_id)。

    Returns:
        200: [{id, role, content, starred, tool_call_id, name}]
    """
    try:
        conn = await db.connect()
        try:
            exists = await conn.fetchval(
                "SELECT id FROM sessions WHERE id = $1", session_id
            )
            if exists is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            rows = await conn.fetch(
                """
                SELECT id, role, content, starred, tool_call_id, name
                FROM messages
                WHERE session_id = $1 AND turn = $2
                ORDER BY id ASC
                """,
                session_id,
                turn,
            )
            return [
                {
                    "id": r["id"],
                    "role": r["role"],
                    "content": r["content"],
                    "starred": r["starred"],
                    "tool_call_id": r["tool_call_id"],
                    "name": r["name"],
                }
                for r in rows
            ]
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "turn_messages_failed"},
        )


class MessageStarredRequest(BaseModel):
    """PUT /admin/messages/{id}/starred 请求体(V1.1-3.3): 收藏/取消收藏。"""

    starred: bool


@router.put("/messages/{message_id}/starred", response_model=None)
async def set_message_starred(message_id: int, body: MessageStarredRequest):
    """收藏/取消收藏单条消息(V1.1-3.3)。"""
    try:
        conn = await db.connect()
        try:
            exists = await conn.fetchval(
                "SELECT id FROM messages WHERE id = $1", message_id
            )
            if exists is None:
                raise HTTPException(status_code=404, detail="message_not_found")
            await conn.execute(
                "UPDATE messages SET starred = $1 WHERE id = $2",
                bool(body.starred),
                message_id,
            )
            return {"ok": True, "id": message_id, "starred": bool(body.starred)}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "message_starred_failed"},
        )


@router.delete("/messages/{message_id}", response_model=None)
async def delete_message(message_id: int):
    """软删除单条消息(V1.1-3.3)。

    策略(保持压缩/回写一致性): 完整副本写入 messages_archive(original_msg_id 关联),
    原消息标记 compressed=true 使其被 get_messages 排除出模型上下文;
    禁止物理删除(Stable Zone 合并/压缩回写依赖完整消息链)。
    """
    try:
        conn = await db.connect()
        try:
            msg = await conn.fetchrow(
                "SELECT id, session_id, turn, role, content, reasoning_content, "
                "tool_calls, zone FROM messages WHERE id = $1",
                message_id,
            )
            if msg is None:
                raise HTTPException(status_code=404, detail="message_not_found")
            # 完整副本入归档(压缩存档同一张表, archive_reason 区分: compressed|deleted)
            await conn.execute(
                """
                INSERT INTO messages_archive (
                    original_msg_id, session_id, turn, role, content,
                    reasoning_content, tool_calls, zone, archived_at
                )
                SELECT id, session_id, turn, role, content,
                       reasoning_content, tool_calls, zone, now()
                FROM messages WHERE id = $1
                """,
                message_id,
            )
            # 原消息标记 compressed(排除出上下文) + deleted 标识
            await conn.execute(
                """
                UPDATE messages
                SET compressed = TRUE,
                    compressed_from = jsonb_build_object(
                        'deleted_at', to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')
                    )
                WHERE id = $1
                """,
                message_id,
            )
            return {"ok": True, "id": message_id, "deleted": True}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "message_delete_failed"},
        )


@router.get("/sessions/{session_id}", response_model=None)
async def get_session_detail(session_id: int):
    """会话详情(V1.1-3.5, 会话设置弹窗用): 元数据 + memory_enabled + folder。"""
    try:
        conn = await db.connect()
        try:
            row = await conn.fetchrow(
                "SELECT id, title, status, folder, model_id, summary, "
                "memory_enabled, auto_execute, max_rounds, locked_skill_name, "
                "created_at, updated_at "
                "FROM sessions WHERE id = $1",
                session_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            return {
                "id": row["id"],
                "title": row["title"],
                "status": row["status"],
                "folder": row["folder"],
                "model_id": row["model_id"],
                "summary": row["summary"],
                "memory_enabled": row["memory_enabled"],
                "auto_execute": row["auto_execute"],
                "max_rounds": row["max_rounds"],
                "locked_skill_name": row["locked_skill_name"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "session_detail_failed"},
        )


@router.get("/sessions/{session_id}/resume", response_model=None)
async def session_resume_info(session_id: int):
    """断点恢复信息查询(V1.5 项-4)。

    前端"断点继续"按钮可用性依据: status='interrupted' 且存在 checkpoint
    (或至少有 final 事件)。返回最新 checkpoint turn(已完整完成的轮次),
    可恢复的续跑轮 = checkpoint_turn + 1。

    Returns:
        200: {
            "session_id", "status",
            "resumable": bool,          # 是否可断点恢复
            "checkpoint_turn": int|None, # 最新 checkpoint 轮(无则为 None)
            "last_final_turn": int|None, # 最后 final 事件轮
        }
        404: session_not_found
    """
    try:
        conn = await db.connect()
        try:
            row = await conn.fetchrow(
                "SELECT id, status FROM sessions WHERE id = $1",
                session_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            # asyncpg JSONB 返回 str → 手动解析(项目既有约定)
            import json as _json

            ckpt = await conn.fetchrow(
                """SELECT turn, payload FROM react_events
                   WHERE session_id = $1 AND event_type = 'checkpoint'
                   ORDER BY turn DESC LIMIT 1""",
                session_id,
            )
            final_turn = await conn.fetchval(
                """SELECT MAX(turn) FROM react_events
                   WHERE session_id = $1 AND event_type = 'final'""",
                session_id,
            )
            ckpt_turn = None
            if ckpt is not None:
                ckpt_turn = int(ckpt["turn"])
            last_final = int(final_turn) if final_turn is not None else None
            return {
                "session_id": session_id,
                "status": row["status"],
                # interrupted 且有断点/完成轮 → 可恢复
                "resumable": (
                    row["status"] == "interrupted"
                    and (ckpt_turn is not None or last_final is not None)
                ),
                "checkpoint_turn": ckpt_turn,
                "last_final_turn": last_final,
            }
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "session_resume_failed"},
        )


class SessionTruncateRequest(BaseModel):
    """POST /admin/sessions/{id}/truncate 请求体(V1.1-3.5): 上下文截断。

    after_turn: 保留 <= after_turn 的轮次, 之后的消息 soft-delete 出上下文。
    """

    after_turn: int


class SessionMemoryRequest(BaseModel):
    """PUT /admin/sessions/{id}/memory-enabled 请求体(V1.1-3.5): 记忆开关。"""

    enabled: bool


@router.post("/sessions/{session_id}/truncate", response_model=None)
async def truncate_session(session_id: int, body: SessionTruncateRequest):
    """截断会话上下文(V1.1-3.5): soft-delete after_turn 之后的消息。

    复用软删除语义: 完整副本入 messages_archive + compressed 标记,
    保持压缩/Stable Zone 回写一致性(禁止硬删)。
    """
    after_turn = int(body.after_turn)
    if after_turn < 0:
        raise HTTPException(status_code=400, detail="after_turn must be >= 0")
    try:
        conn = await db.connect()
        try:
            exists = await conn.fetchval(
                "SELECT id FROM sessions WHERE id = $1", session_id
            )
            if exists is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            # 1. 副本入 archive
            await conn.execute(
                """
                INSERT INTO messages_archive (
                    original_msg_id, session_id, turn, role, content,
                    reasoning_content, tool_calls, zone, archived_at
                )
                SELECT id, session_id, turn, role, content,
                       reasoning_content, tool_calls, zone, now()
                FROM messages
                WHERE session_id = $1 AND turn > $2
                """,
                session_id,
                after_turn,
            )
            # 2. 原消息标记 compressed(排除出上下文)
            rows = await conn.fetch(
                """
                UPDATE messages
                SET compressed = TRUE,
                    compressed_from = jsonb_build_object(
                        'truncated_at', to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
                        'after_turn', $2::int
                    )
                WHERE session_id = $1 AND turn > $2
                RETURNING id
                """,
                session_id,
                after_turn,
            )
            n = len(rows)
            return {"ok": True, "id": session_id, "truncated_messages": n, "after_turn": after_turn}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "session_truncate_failed"},
        )


@router.put("/sessions/{session_id}/memory-enabled", response_model=None)
async def set_session_memory_enabled(session_id: int, body: SessionMemoryRequest):
    """会话级记忆开关(V1.1-3.5)。关闭后该会话不再注入/提取记忆。"""
    try:
        conn = await db.connect()
        try:
            exists = await conn.fetchval(
                "SELECT id FROM sessions WHERE id = $1", session_id
            )
            if exists is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            await conn.execute(
                "UPDATE sessions SET memory_enabled = $1 WHERE id = $2",
                bool(body.enabled),
                session_id,
            )
            return {"ok": True, "id": session_id, "memory_enabled": bool(body.enabled)}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "session_memory_failed"},
        )


@router.get("/sessions/{session_id}/system-prompt", response_model=None)
async def get_session_system_prompt(session_id: int):
    """查看组装后的完整系统提示词(V1.1-3.5, 调试用)。

    延迟 import main(避免循环依赖): 运行时 main 已加载。
    包含: skill prompt(模板+少样本) + 身份段 + MCP 工具速查指南。
    """
    try:
        from private_agent import main as main_mod
    except Exception:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={"error": "main_module_unavailable"},
        )
    try:
        conn = await db.connect()
        try:
            exists = await conn.fetchval(
                "SELECT id FROM sessions WHERE id = $1", session_id
            )
            if exists is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            cfg = await _load_cfg()
            prompt = await main_mod._get_system_prompt(cfg, session_id, conn)
            return {"ok": True, "id": session_id, "system_prompt": prompt}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "system_prompt_failed"},
        )


# ══════════════════════════════════════════════════════════════════════════
# V1.1-3.7 文件管理闭环(workspace 内文件树/预览/操作, 全部锁根目录)
# ══════════════════════════════════════════════════════════════════════════

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", "${WORKSPACE}", "outputs", "logs"}


def _workspace_root() -> Path:
    """工作区根目录(config system.workspace_root, 展开环境变量)。"""
    from pathlib import Path

    cfg = loader.load_config()
    ws = os.path.expandvars(cfg.get("system", {}).get("workspace_root", "."))
    return Path(ws).resolve()


def _resolve_workspace_path(rel: str) -> Path:
    """将相对路径解析为工作区内绝对路径(越界 → 400)。"""
    from pathlib import Path

    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        raise HTTPException(status_code=400, detail="empty_path")
    root = _workspace_root()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="path_outside_workspace")
    return target


@router.get("/files/tree", response_model=None)
async def list_files_tree(path: str = "", depth: int = 4):
    """工作区文件树(V1.1-3.7): 返回目录结构, 排除常见噪音目录。

    Returns:
        200: {"root": str, "tree": {name, path, type: dir|file, size, children?}}
    """
    root = _workspace_root()
    base = _resolve_workspace_path(path) if path else root

    async def _walk(node: Path, remaining: int) -> dict | None:
        if remaining < 0:
            return None
        if node.is_file():
            try:
                size = node.stat().st_size
            except OSError:
                size = 0
            return {"name": node.name, "path": str(node.relative_to(root)).replace("\\", "/"), "type": "file", "size": size}
        if not node.is_dir():
            return None
        try:
            entries = sorted(node.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return None
        children = []
        for e in entries:
            if e.name in _SKIP_DIRS and e.is_dir():
                continue
            child = await _walk(e, remaining - 1)
            if child is not None:
                children.append(child)
        return {
            "name": node.name,
            "path": str(node.relative_to(root)).replace("\\", "/") if node != root else "",
            "type": "dir",
            "children": children,
        }

    tree = await _walk(base, int(depth))
    return {"root": str(root), "tree": tree}


@router.get("/files/content", response_model=None)
async def get_file_content(path: str):
    """读取文本文件内容(V1.1-3.7): utf-8 文本直出, 二进制返回提示。

    Returns:
        200: {"path", "type": "text"|"binary", "content"?, "size"}
    """
    target = _resolve_workspace_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file_not_found")
    size = target.stat().st_size
    if size > 2 * 1024 * 1024:
        return {"path": path, "type": "text", "truncated": True, "size": size,
                "content": target.read_text(encoding="utf-8", errors="replace")[:200_000]}
    try:
        content = target.read_text(encoding="utf-8")
        return {"path": path, "type": "text", "content": content, "size": size}
    except UnicodeDecodeError:
        return {"path": path, "type": "binary", "size": size}


@router.get("/files/download", response_model=None)
async def download_workspace_file(path: str):
    """下载工作区内文件(V1.1-3.7, 流式 FileResponse)。"""
    from fastapi.responses import FileResponse

    target = _resolve_workspace_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file_not_found")
    return FileResponse(
        str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


class FilePathRequest(BaseModel):
    """文件操作请求体(V1.1-3.7): path / from_path+to_path。"""

    path: str
    to_path: str | None = None


@router.post("/files/mkdir", response_model=None)
async def mkdir_workspace_dir(body: FilePathRequest):
    """新建目录(可多级, 越界 400)。"""
    target = _resolve_workspace_path(body.path)
    if target.exists():
        return {"ok": True, "path": body.path, "existed": True}
    try:
        target.mkdir(parents=True)
        return {"ok": True, "path": body.path}
    except OSError:
        return JSONResponse(status_code=500, content={"error": "mkdir_failed"})


@router.put("/files/rename", response_model=None)
async def rename_workspace_file(body: FilePathRequest):
    """重命名/移动文件或目录(目标越界 400)。"""
    if not body.to_path:
        raise HTTPException(status_code=400, detail="to_path_required")
    src = _resolve_workspace_path(body.path)
    dst = _resolve_workspace_path(body.to_path)
    if not src.exists():
        raise HTTPException(status_code=404, detail="source_not_found")
    if dst.exists():
        raise HTTPException(status_code=400, detail="target_exists")
    try:
        src.rename(dst)
        return {"ok": True, "path": body.to_path}
    except OSError:
        return JSONResponse(status_code=500, content={"error": "rename_failed"})


@router.delete("/files/delete", response_model=None)
async def delete_workspace_file(path: str):
    """删除工作区内文件或空目录(V1.1-3.7)。

    仅允许删除文件或空目录(个人安全边界: 禁止递归删除, 防误删)。
    """
    target = _resolve_workspace_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="not_found")
    try:
        if target.is_dir():
            if any(target.iterdir()):
                raise HTTPException(status_code=400, detail="dir_not_empty")
            target.rmdir()
        else:
            target.unlink()
        return {"ok": True, "path": path, "deleted": True}
    except HTTPException:
        raise
    except OSError:
        return JSONResponse(status_code=500, content={"error": "delete_failed"})


# ══════════════════════════════════════════════════════════════════════════
# V1.3-7.4 高级文件能力: 压缩包解压(防穿越) + 批量打包下载
# ══════════════════════════════════════════════════════════════════════════


class FileExtractRequest(BaseModel):
    """POST /files/extract 请求体(V1.3-7.4): 解压压缩包。

    archive: 工作区内压缩包相对路径(zip / tar.gz / tgz)。
    to_dir: 目标目录相对路径(可选, 默认解压到压缩包所在目录)。
    """

    archive: str
    to_dir: str | None = None


@router.post("/files/extract", response_model=None)
async def extract_archive(body: FileExtractRequest):
    """解压 zip/tar.gz 到工作区(V1.3-7.4)。

    安全约束: 每个条目解析后必须位于目标目录内(路径穿越防护);
    跳过符号链接条目; 单文件大小上限 100MB。
    """
    archive = _resolve_workspace_path(body.archive)
    if not archive.exists() or not archive.is_file():
        raise HTTPException(status_code=404, detail="archive_not_found")
    name = archive.name.lower()
    is_zip = name.endswith(".zip")
    is_tar = name.endswith(".tar.gz") or name.endswith(".tgz")
    if not (is_zip or is_tar):
        raise HTTPException(
            status_code=400, detail="unsupported_archive: 仅支持 zip / tar.gz / tgz"
        )
    # 目标目录: 显式 to_dir 或压缩包所在目录
    if body.to_dir and body.to_dir.strip():
        dest = _resolve_workspace_path(body.to_dir)
        dest.mkdir(parents=True, exist_ok=True)
    else:
        dest = archive.parent
    root = _workspace_root()
    extracted = 0

    def _safe_target(rel_name: str, base: Path) -> Path | None:
        """解析条目路径并防穿越(返回 None 表示拒绝)。"""
        rel = rel_name.replace("\\", "/").lstrip("/")
        if not rel:
            return None
        target = (base / rel).resolve()
        if target != base and base not in target.parents:
            return None
        if root not in target.parents and target != root:
            return None
        return target

    try:
        if is_zip:
            import zipfile

            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    if info.filename.endswith("/"):
                        continue
                    if info.file_size > 100 * 1024 * 1024:
                        continue
                    target = _safe_target(info.filename, dest)
                    if target is None:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as out:
                        out.write(src.read())
                    extracted += 1
        else:
            import tarfile

            with tarfile.open(archive, "r:gz") as tf:
                for member in tf.getmembers():
                    if not member.isfile() and not member.islnk():
                        continue
                    if member.size > 100 * 1024 * 1024:
                        continue
                    target = _safe_target(member.name, dest)
                    if target is None:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tf.extractfile(member) as src, open(target, "wb") as out:
                        if src is not None:
                            out.write(src.read())
                    extracted += 1
        return {"ok": True, "extracted": extracted, "to_dir": str(dest.relative_to(root))}
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": "extract_failed"})


@router.get("/files/download_zip", response_model=None)
async def download_files_zip(paths: str, name: str = "workspace-export"):
    """批量打包下载(V1.3-7.4): paths 逗号分隔, 逐条校验后 zip 打包。

    Returns:
        200: ZIP 流(application/zip)
        400: {"error": "paths_required"} / {"error": "no_valid_files"}
    """
    from io import BytesIO
    import zipfile

    items = [p.strip() for p in (paths or "").split(",") if p.strip()]
    if not items:
        raise HTTPException(status_code=400, detail="paths_required")
    buffer = BytesIO()
    added = 0
    root = _workspace_root()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in items:
            try:
                target = _resolve_workspace_path(rel)
            except HTTPException:
                continue
            if not target.exists():
                continue
            arcname = str(target.relative_to(root)).replace("\\", "/")
            if target.is_dir():
                # 目录: 递归加入(同样防越界, resolve 已保证在工作区内)
                for p in sorted(target.rglob("*")):
                    if p.is_file():
                        zf.write(str(p), str(p.relative_to(root)).replace("\\", "/"))
                        added += 1
            else:
                zf.write(str(target), arcname)
                added += 1
    if added == 0:
        raise HTTPException(status_code=400, detail="no_valid_files")
    from fastapi.responses import Response

    safe_name = "".join(c for c in name if c.isalnum() or c in "-_") or "workspace-export"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.zip"'},
    )


@router.get("/tasks", response_model=None)
async def list_session_tasks(session_id: int, limit: int = 20):
    """会话任务执行状态(V1.1-3.8)。

    复用 react_events 数据面聚合(不引入 async_tasks 空表):
    每轮(turn)的 thinking/tool_call/tool_result/error 次数 + 最后时间 + 会话状态。
    Returns:
        200: {status, turns: [{turn, events: {...}, error?}], total_turns}
    """
    try:
        conn = await db.connect()
        try:
            srow = await conn.fetchrow(
                "SELECT status, updated_at FROM sessions WHERE id = $1", session_id
            )
            if srow is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            rows = await conn.fetch(
                """
                SELECT turn, event_type, COUNT(*) AS n, MAX(created_at) AS last_ts
                FROM react_events
                WHERE session_id = $1 AND turn > 0
                GROUP BY turn, event_type
                ORDER BY turn ASC
                LIMIT $2
                """,
                session_id,
                min(max(int(limit), 1), 200),
            )
            turns_map: dict[int, dict] = {}
            for r in rows:
                turn = r["turn"]
                t = turns_map.setdefault(turn, {"turn": turn, "events": {}, "last_ts": None})
                t["events"][r["event_type"]] = r["n"]
                t["last_ts"] = (
                    r["last_ts"].isoformat() if r["last_ts"] else t["last_ts"]
                )
            # 每轮错误信息(最近一条 error/tool_error payload)
            err_rows = await conn.fetch(
                """
                SELECT turn, payload FROM react_events
                WHERE session_id = $1 AND event_type IN ('error', 'tool_error')
                ORDER BY id DESC LIMIT 10
                """,
                session_id,
            )
            err_by_turn: dict[int, str] = {}
            for e in err_rows:
                if e["turn"] not in err_by_turn:
                    p = e["payload"]
                    if isinstance(p, str):
                        import json as _json

                        try:
                            p = _json.loads(p)
                        except Exception:  # noqa: BLE001
                            p = {}
                    err_by_turn[e["turn"]] = str(
                        (p or {}).get("message") or (p or {}).get("error") or "未知错误"
                    )
            for turn, err in err_by_turn.items():
                if turn in turns_map:
                    turns_map[turn]["error"] = err

            return {
                "status": srow["status"],
                "updated_at": srow["updated_at"].isoformat() if srow["updated_at"] else None,
                "total_turns": len(turns_map),
                "turns": sorted(turns_map.values(), key=lambda t: t["turn"]),
            }
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "tasks_list_failed"},
        )


@router.get("/events", response_model=None)
async def list_session_events(session_id: int, limit: int = 50):
    """会话工具事件日志(V1.2-6.2): 读 react_events 时间线(倒序)。

    Returns:
        200: [{id, turn, event_type, ts, summary}]
        summary: payload 摘要(内容截断, 供前端日志视图)
    """
    import json as _json

    try:
        conn = await db.connect()
        try:
            srow = await conn.fetchval(
                "SELECT id FROM sessions WHERE id = $1", session_id
            )
            if srow is None:
                raise HTTPException(status_code=404, detail="session_not_found")
            rows = await conn.fetch(
                """
                SELECT id, turn, event_type, payload, created_at
                FROM react_events
                WHERE session_id = $1
                ORDER BY id DESC
                LIMIT $2
                """,
                session_id,
                min(max(int(limit), 1), 200),
            )
            result = []
            for r in rows:
                p = r["payload"]
                if isinstance(p, str):
                    try:
                        p = _json.loads(p)
                    except Exception:  # noqa: BLE001
                        p = {}
                p = p or {}
                # 摘要: 不同事件取关键字段
                summary = _json.dumps(p, ensure_ascii=False)[:300]
                result.append({
                    "id": r["id"],
                    "turn": r["turn"],
                    "event_type": r["event_type"],
                    "ts": r["created_at"].isoformat() if r["created_at"] else None,
                    "summary": summary,
                })
            return result
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "events_list_failed"},
        )


@router.get("/subagents/{subagent_id}/events", response_model=None)
async def list_subagent_events(subagent_id: int, limit: int = 200):
    """子代理完整对话流(2026-08-06): 读子 session 的 react_events,
    与主对话流 replay 同源(子代理 ReactLoop 事件已全量入库)。

    Returns:
        200: [{id, turn, event_type, payload, ts}] 按 id 升序(时间顺序),
        payload 为完整负载(thinking 增量 / tool_call 参数 / tool_result
        输出 / final 文本), 供前端子任务卡片展开渲染思考链+工具调用+结果。
        未创建子 session(未开始执行) → []。
        404: subagent_not_found
    """
    import json as _json

    try:
        conn = await db.connect()
        try:
            # 仅 kind='sub' 的子会话返回(未回填/指向父会话 → []),
            # 防误读父会话历史事件
            sub_session = await conn.fetchval(
                """
                SELECT s.id FROM subagents sa
                JOIN sessions s ON s.id = sa.session_id
                WHERE sa.id = $1 AND s.kind = 'sub'
                """,
                subagent_id,
            )
            if sub_session is None:
                # 子代理不存在 vs 未回填: 区分 404 / []
                exists = await conn.fetchval(
                    "SELECT 1 FROM subagents WHERE id = $1", subagent_id
                )
                if not exists:
                    raise HTTPException(
                        status_code=404, detail="subagent_not_found"
                    )
                return []  # 未创建子 session(委派后未执行到子代理)
            rows = await conn.fetch(
                """
                SELECT id, turn, event_type, payload, created_at
                FROM react_events
                WHERE session_id = $1
                ORDER BY id ASC
                LIMIT $2
                """,
                sub_session,
                min(max(int(limit), 1), 500),
            )
            result = []
            for r in rows:
                p = r["payload"]
                if isinstance(p, str):
                    try:
                        p = _json.loads(p)
                    except Exception:  # noqa: BLE001
                        p = {}
                result.append({
                    "id": r["id"],
                    "turn": r["turn"],
                    "event_type": r["event_type"],
                    "payload": p or {},
                    "ts": r["created_at"].isoformat() if r["created_at"] else None,
                })
            return result
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "subagent_events_failed"},
        )


@router.get("/usage", response_model=None)
async def get_llm_usage(session_id: int | None = None, limit: int = 20):
    """LLM 用量统计(V1.2-6.3): 聚合 react_events 的 token_usage 事件。

    Args:
        session_id: 省略 → 全部会话汇总; 提供 → 仅该会话。
        limit: 返回的会话明细条数。
    Returns:
        200: {total_calls, total_tokens, input_tokens, output_tokens,
              total_cost, currency, by_session: [{session_id, calls, tokens, cost}]}
    """
    import json as _json

    try:
        conn = await db.connect()
        try:
            if session_id is not None:
                srow = await conn.fetchval(
                    "SELECT id FROM sessions WHERE id = $1", session_id
                )
                if srow is None:
                    raise HTTPException(status_code=404, detail="session_not_found")
                rows = await conn.fetch(
                    """
                    SELECT session_id, payload FROM react_events
                    WHERE event_type = 'token_usage' AND session_id = $1
                    """,
                    session_id,
                )
            else:
                rows = await conn.fetch(
                    "SELECT session_id, payload FROM react_events WHERE event_type = 'token_usage'"
                )
            per_session: dict[int, dict] = {}
            totals = {"calls": 0, "tokens": 0, "input": 0, "output": 0, "cost": 0.0}
            currency = "CNY"
            for r in rows:
                p = r["payload"]
                if isinstance(p, str):
                    try:
                        p = _json.loads(p)
                    except Exception:  # noqa: BLE001
                        p = {}
                p = p or {}
                sid = r["session_id"]
                s = per_session.setdefault(
                    sid, {"session_id": sid, "calls": 0, "tokens": 0, "cost": 0.0}
                )
                s["calls"] += 1
                s["tokens"] += int(p.get("total_tokens") or 0)
                s["cost"] += float(p.get("cost") or 0)
                totals["calls"] += 1
                totals["tokens"] += int(p.get("total_tokens") or 0)
                totals["input"] += int(p.get("input_tokens") or 0)
                totals["output"] += int(p.get("output_tokens") or 0)
                totals["cost"] += float(p.get("cost") or 0)
                if p.get("currency"):
                    currency = p["currency"]
            by_session = sorted(
                per_session.values(),
                key=lambda s: s["tokens"],
                reverse=True,
            )[: max(1, min(int(limit), 100))]
            return {
                "total_calls": totals["calls"],
                "total_tokens": totals["tokens"],
                "input_tokens": totals["input"],
                "output_tokens": totals["output"],
                "total_cost": round(totals["cost"], 6),
                "currency": currency,
                "by_session": by_session,
            }
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "usage_query_failed"},
        )


@router.get("/errors/summary", response_model=None)
async def error_summary(session_id: int | None = None, limit: int = 20):
    """最近错误摘要(V1.2-6.3): 聚合 error/tool_error 事件, 按消息去重。"""
    import json as _json
    from collections import Counter

    try:
        conn = await db.connect()
        try:
            if session_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT event_type, payload, created_at FROM react_events
                    WHERE event_type IN ('error', 'tool_error') AND session_id = $1
                    ORDER BY id DESC LIMIT $2
                    """,
                    session_id,
                    min(max(int(limit), 1), 100),
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT event_type, payload, created_at FROM react_events
                    WHERE event_type IN ('error', 'tool_error')
                    ORDER BY id DESC LIMIT $1
                    """,
                    min(max(int(limit), 1), 100),
                )
            counter: Counter = Counter()
            samples: list[dict] = []
            for r in rows:
                p = r["payload"]
                if isinstance(p, str):
                    try:
                        p = _json.loads(p)
                    except Exception:  # noqa: BLE001
                        p = {}
                p = p or {}
                msg = str(
                    (p.get("message") or p.get("error") or p.get("reason")
                     or f"{r['event_type']}({r['payload']})")[:120]
                )
                counter[msg] += 1
                if len(samples) < 10:
                    samples.append({
                        "event_type": r["event_type"],
                        "message": msg,
                        "ts": r["created_at"].isoformat() if r["created_at"] else None,
                    })
            return {
                "total_errors": sum(counter.values()),
                "distinct_errors": len(counter),
                "top": [
                    {"message": msg, "count": n}
                    for msg, n in counter.most_common(10)
                ],
                "samples": samples,
            }
        finally:
            await conn.close()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "error_summary_failed"},
        )


@router.get("/logs", response_model=None)
async def tail_logs(lines: int = 200):
    """读后端日志尾部(V1.2-6.3): 找 logs/ 下最新 .log 文件, 返回末尾 N 行。"""
    from pathlib import Path

    logs_dir = Path(os.environ.get("WORKSPACE", "")) / "logs"
    if not logs_dir.exists():
        logs_dir = Path(_get_outputs_dir()).parent / "logs"
    try:
        candidates = sorted(
            (p for p in logs_dir.glob("*.log") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        candidates = []
    if not candidates:
        return {"path": None, "lines": [], "truncated": False}
    target = candidates[0]
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"path": str(target), "lines": [], "truncated": False}
    arr = text.splitlines()
    n = min(max(int(lines), 1), 1000)
    return {
        "path": str(target),
        "lines": arr[-n:],
        "truncated": len(arr) > n,
    }


class SessionModelRequest(BaseModel):
    """POST /sessions/{id}/model 请求体: 会话级模型选择。

    model_id: "auto"(fallback 链, 自动降级) 或具体 provider 名(手动锁定, 不降级)。
    """

    model_id: str


@router.post("/sessions/{id}/model", response_model=None)
async def set_session_model(id: int, body: SessionModelRequest):
    """设置会话使用的模型(自动/手动模式)。

    - model_id = "auto" → sessions.model_id 置 NULL(自动模式, fallback 链)
    - model_id = provider 名 → 校验存在且未删除, 存入 sessions.model_id(手动锁定)

    对话时 WS 处理按 model_id 构建 adapter(见 main._build_session_adapter)。
    """
    import os

    model_id = (body.model_id or "").strip()
    try:
        conn = await db.connect()
        try:
            row = await conn.fetchrow(
                "SELECT id FROM sessions WHERE id = $1", id
            )
            if row is None:
                raise HTTPException(status_code=404, detail="session_not_found")

            if not model_id or model_id == "auto":
                await conn.execute(
                    "UPDATE sessions SET model_id = NULL WHERE id = $1", id
                )
                return {"ok": True, "id": id, "model_id": "auto"}

            # 校验 provider 存在且未删除
            cfg = await _load_cfg()
            prov = cfg.get("models", {}).get("providers", {}).get(model_id)
            if prov is None or prov.get("deleted"):
                raise HTTPException(
                    status_code=400,
                    detail=f"provider '{model_id}' 不存在或已删除",
                )
            await conn.execute(
                "UPDATE sessions SET model_id = $1 WHERE id = $2", model_id, id
            )
            return {"ok": True, "id": id, "model_id": model_id}
        finally:
            await conn.close()
    except HTTPException:
        raise
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "session_model_set_failed"},
        )


# ══════════════════════════════════════════════════════════════════════════
# 首页壁纸上传/查询/移除(V1.5, 前端 HomeView)
# ══════════════════════════════════════════════════════════════════════════


def _wallpaper_path() -> str | None:
    """返回当前壁纸/视频背景文件路径(不存在返回 None)。

    按修改时间取最新(而非固定扩展名顺序),避免残留旧扩展名文件
    (如 .png 测试图)覆盖用户最新上传的 .jpeg/.mp4。
    支持图片(.png/.jpg/.jpeg/.webp) + 视频(.mp4/.webm, V2 首页动态背景)。
    """
    outputs_dir = _get_outputs_dir()
    candidates: list = []
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"):
        p = outputs_dir / f"wallpaper{ext}"
        if p.exists() and p.is_file():
            candidates.append(p)
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return newest.name


def _wallpaper_type(name: str) -> str:
    """根据扩展名判断背景类型('image' | 'video'), 供前端选择渲染元素。"""
    return "video" if name.lower().endswith((".mp4", ".webm")) else "image"


def _wallpaper_style() -> dict:
    """读取壁纸显示样式(不存在返回默认)。"""
    import json as _json

    try:
        p = _get_outputs_dir() / "wallpaper-style.json"
        if p.exists():
            data = _json.loads(p.read_text(encoding="utf-8"))
            return {
                "position_x": float(data.get("position_x", 50)),
                "position_y": float(data.get("position_y", 50)),
                "fit": data.get("fit", "cover"),
                "scale": float(data.get("scale", 100)),
                "rotate": float(data.get("rotate", 0)),
            }
    except Exception:  # noqa: BLE001
        pass
    return {
        "position_x": 50.0,
        "position_y": 50.0,
        "fit": "cover",
        "scale": 100.0,
        "rotate": 0.0,
    }


class WallpaperUploadRequest(BaseModel):
    """POST /admin/wallpaper 请求体: data URL(base64 图片)。"""

    data_url: str


class WallpaperStyleRequest(BaseModel):
    """PUT /admin/wallpaper/style 请求体: 显示位置/填充/缩放/旋转。"""

    position_x: float = 50.0
    position_y: float = 50.0
    fit: str = "cover"  # cover | contain
    scale: float = 100.0  # 50-200(%)
    rotate: float = 0.0  # -45~45(度)


@router.get("/wallpaper", response_model=None)
async def get_wallpaper():
    """返回当前壁纸/视频背景可访问路径、类型与显示样式。

    Returns:
        200: {
            "wallpaper": "/files/outputs/wallpaper.png" | None,
            "type": "image" | "video"(按扩展名, 前端选择 <img> 或 <video>),
            "style": {"position_x": float, "position_y": float, "fit": str},
        }
    """
    name = _wallpaper_path()
    return {
        "wallpaper": f"/files/outputs/{name}" if name else None,
        "type": _wallpaper_type(name) if name else "image",
        "style": _wallpaper_style(),
    }


@router.put("/wallpaper/style", response_model=None)
async def update_wallpaper_style(body: WallpaperStyleRequest):
    """保存壁纸显示样式(首页背景位置/填充方式)。

    Returns:
        200: {"style": {"position_x", "position_y", "fit"}}
    """
    import json as _json

    style = {
        "position_x": min(max(float(body.position_x), 0.0), 100.0),
        "position_y": min(max(float(body.position_y), 0.0), 100.0),
        "fit": body.fit if body.fit in ("cover", "contain") else "cover",
        "scale": min(max(float(body.scale), 50.0), 200.0),
        "rotate": min(max(float(body.rotate), -360.0), 360.0),
    }
    try:
        outputs_dir = _get_outputs_dir()
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "wallpaper-style.json").write_text(
            _json.dumps(style), encoding="utf-8"
        )
        return {"style": style}
    except Exception:  # noqa: BLE001
        return JSONResponse(
            status_code=500,
            content={"error": "wallpaper_style_save_failed"},
        )


class ChatFileUploadRequest(BaseModel):
    """POST /admin/files/upload 请求体: 对话文档上传(base64)。"""

    filename: str = "upload.bin"
    content_base64: str


@router.post("/files/upload", response_model=None)
async def upload_chat_file(body: ChatFileUploadRequest):
    """对话文档上传: 存 {WORKSPACE}/uploads/, 返回绝对路径供模型 file_read。

    Args:
        body: {"filename": "xxx.pdf", "content_base64": "..."}

    Returns:
        200: {"path": str, "name": str, "size": int}
        400: {"error": "invalid_file" | "file_too_large"}
        500: {"error": "upload_failed"}
    """
    import base64 as _b64
    import re as _re
    from pathlib import Path

    safe_name = _re.sub(r'[\\/:*?"<>|]', "_", Path(body.filename).name or "upload.bin")
    if not safe_name:
        safe_name = "upload.bin"
    try:
        decoded = _b64.b64decode(body.content_base64, validate=False)
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": "invalid_file"})
    if len(decoded) > 15 * 1024 * 1024:  # ≤15MB
        return JSONResponse(status_code=400, content={"error": "file_too_large"})
    try:
        ws = os.environ.get("WORKSPACE", "")
        uploads_dir = Path(ws) / "uploads" if ws else Path(_get_outputs_dir()).parent / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        target = uploads_dir / safe_name
        target.write_bytes(decoded)
        return {"path": str(target), "name": safe_name, "size": len(decoded)}
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": "upload_failed"})


class SkillUploadRequest(BaseModel):
    """POST /admin/skills/upload 请求体: 上传新技能(skill.yaml + system_prompt)。"""

    name: str
    skill_yaml: str
    system_prompt: str = ""


@router.post("/skills/upload", response_model=None)
async def upload_skill(body: SkillUploadRequest):
    """上传新 skill: 写 backend/skills/{name}/skill.yaml(+system_prompt.md)。

    Args:
        body: {"name": "my_skill", "skill_yaml": "...", "system_prompt": "..."}

    Returns:
        200: {"name": str, "path": str}
        400: {"error": "invalid_skill_name" | "invalid_skill_yaml"}
        500: {"error": "skill_save_failed"}
    """
    import re as _re
    from pathlib import Path

    name = body.name.strip().lower()
    if not _re.fullmatch(r"[a-z0-9_]+", name):
        return JSONResponse(status_code=400, content={"error": "invalid_skill_name"})
    try:
        parsed = yaml.safe_load(body.skill_yaml)
        if not isinstance(parsed, dict) or not parsed.get("name"):
            return JSONResponse(
                status_code=400, content={"error": "invalid_skill_yaml"},
            )
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": "invalid_skill_yaml"})
    try:
        cfg = loader.load_config()
        dev_dir = Path(cfg.get("skills", {}).get("storage", {}).get("dev_dir", "./skills"))
        if not dev_dir.is_absolute():
            dev_dir = Path.cwd() / dev_dir
        target_dir = dev_dir / name
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "skill.yaml").write_text(body.skill_yaml, encoding="utf-8")
        if body.system_prompt.strip():
            (target_dir / "system_prompt.md").write_text(
                body.system_prompt, encoding="utf-8",
            )
        return {"name": name, "path": str(target_dir)}
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=500, content={"error": "skill_save_failed"})


@router.post("/skills/upload-zip", response_model=None)
async def upload_skill_zip(file: UploadFile = File(...)):
    """2026-08-04: 上传技能 zip(支持集合包, 无需手填 yaml)。

    三种模式(按 zip 内容自动判定):
      single     - 1 个 skill.yaml(根/子目录)     → {"name","path","files"}
      collection - 多个 skill.yaml(技能集合包)     → {"mode":"collection","skills":[...],"total":N}
      auto       - 无 skill.yaml 的素材库
                    (如 awesome-design-md: design-md/<brand>/DESIGN.md)
                    自动为每个含文档的子目录生成 skill.yaml → {"mode":"auto","skills":[...]}

    Returns:
        200: 见上; 400: {"error": invalid_zip|invalid_skill_yaml|zip_path_traversal|skill_yaml_not_found}
        500: {"error": "skill_save_failed"}
    """
    import io as _io
    import re as _re
    import zipfile as _zipfile
    from pathlib import Path

    try:
        raw = await file.read()
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": "invalid_zip"})
    if not raw or len(raw) > 50 * 1024 * 1024:
        return JSONResponse(
            status_code=400, content={"error": "invalid_zip", "detail": "empty or >50MB"},
        )

    try:
        zf = _zipfile.ZipFile(_io.BytesIO(raw))
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": "invalid_zip"})

    # zip slip 防护: 所有成员必须先校验相对路径合法
    members = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        norm = _posix_norm(info.filename)
        if norm.startswith("..") or norm.startswith("/") or ".." in norm.split("/"):
            return JSONResponse(
                status_code=400, content={"error": "zip_path_traversal"},
            )
        members.append((info, norm))

    cfg = loader.load_config()
    dev_dir = Path(cfg.get("skills", {}).get("storage", {}).get("dev_dir", "./skills"))
    if not dev_dir.is_absolute():
        dev_dir = Path.cwd() / dev_dir

    # 收集所有 skill.yaml(任意深度, 大小写不敏感), 按技能根分组
    yaml_hits = [
        (i, n) for i, n in members
        if n.lower() == "skill.yaml" or n.lower().endswith("/skill.yaml")
    ]
    groups: dict[str, tuple] = {}
    for info, norm in yaml_hits:
        root = norm[: -len("skill.yaml")]  # 技能根前缀(含尾部斜杠或空)
        groups.setdefault(root, (info, norm))

    if groups:
        installed = []
        for root, (info, norm) in groups.items():
            try:
                yaml_text = zf.read(info).decode("utf-8")
                parsed = yaml.safe_load(yaml_text)
                if not isinstance(parsed, dict) or not parsed.get("name"):
                    continue
                name = str(parsed["name"]).strip().lower()
                if not _re.fullmatch(r"[a-z0-9_]+", name):
                    continue
            except Exception:  # noqa: BLE001
                continue
            try:
                target = dev_dir / name
                target.mkdir(parents=True, exist_ok=True)
                written = 0
                for i2, n2 in members:
                    if root and not n2.startswith(root):
                        continue
                    rel = n2[len(root):] if root else n2
                    if not rel or rel.lower() == "skill.yaml":
                        continue
                    dest = (target / rel).resolve()
                    if not str(dest).startswith(str(target.resolve()) + os.sep):
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(i2))
                    written += 1
                (target / "skill.yaml").write_text(yaml_text, encoding="utf-8")
                installed.append(
                    {"name": name, "path": str(target), "files": written + 1}
                )
            except Exception:  # noqa: BLE001
                continue
        if not installed:
            return JSONResponse(status_code=500, content={"error": "skill_save_failed"})
        if len(installed) == 1:
            return installed[0]
        return {
            "mode": "collection",
            "skills": installed,
            "total": len(installed),
            "note": "检测到技能集合包, 已批量安装",
        }

    # 无 skill.yaml → 素材库自动技能化(如 awesome-design-md)
    auto = _auto_generate_skills(zf, members, dev_dir)
    if auto:
        return {
            "mode": "auto",
            "skills": auto,
            "total": len(auto),
            "note": "素材库已自动转换为技能(skill.yaml 自动生成, 可编辑调整)",
        }
    listing = ", ".join(n for _, n in members[:10]) or "(空压缩包)"
    return JSONResponse(
        status_code=400,
        content={
            "error": "skill_yaml_not_found",
            "detail": f"压缩包内未找到 skill.yaml, 实际文件: {listing}",
        },
    )


def _ext_of(name: str) -> str:
    """取小写扩展名(无扩展名返回空)。"""
    slash = name.rfind("/")
    dot = name.rfind(".")
    return name[dot:].lower() if dot > slash else ""


def _auto_generate_skills(zf, members, dev_dir):
    """素材库自动技能化(2026-08-04 集合包支持)。

    规则: 每个"含文档文件(.md/.txt/.yaml/.json/.csv)的非隐藏子目录"生成一个
    读取型技能(file_read), 目录名规范化为技能名, 目录内文件全部装入。
    根目录散文件/隐藏目录(.github 等)自动跳过。
    """
    import re as _re

    DOC_EXT = {".md", ".txt", ".yaml", ".yml", ".json", ".csv"}
    skill_files: dict[str, list] = {}
    for info, norm in members:
        if _ext_of(norm) not in DOC_EXT:
            continue
        parts = norm.split("/")
        if len(parts) < 2:
            continue  # 根目录散文件
        parent_parts = parts[:-1]
        # 修复(2026-08-04): 顶层目录内容(GitHub zip 的 repo_root/README.md)
        # 不是技能; 隐藏目录(.github 等)任一段以 . 开头也跳过
        if len(parent_parts) < 2:
            continue
        if any(p.startswith(".") for p in parent_parts):
            continue
        parent = "/".join(parent_parts)  # 文件直接所在目录 = 技能根
        skill_files.setdefault(parent, []).append((info, norm))
    if not skill_files:
        return []

    used: set[str] = set()
    installed = []
    # 文件多的目录优先(主规范优先)
    for root, files in sorted(skill_files.items(), key=lambda kv: -len(kv[1])):
        base = root.rsplit("/", 1)[-1]
        name = _re.sub(r"[^a-z0-9_]", "_", base.lower()).strip("_") or "design_ref"
        if name in used:
            i = 2
            while f"{name}_{i}" in used:
                i += 1
            name = f"{name}_{i}"
        used.add(name)
        try:
            target = dev_dir / name
            target.mkdir(parents=True, exist_ok=True)
            written = 0
            for info, norm in files:
                rel = norm[len(root) + 1:]
                if not rel:
                    continue
                dest = (target / rel).resolve()
                if not str(dest).startswith(str(target.resolve()) + os.sep):
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(info))
                written += 1
            skill_yaml = (
                f"name: {name}\n"
                "version: 1.0.0\n"
                f"scenario: 需要参考或实现与 {base} 相关的设计风格/UI 规范时\n"
                f"description: {base} 设计规范参考(素材库自动转换, 可编辑 skill.yaml 调整)\n"
                "tools:\n"
                "  - file_read\n"
            )
            (target / "skill.yaml").write_text(skill_yaml, encoding="utf-8")
            installed.append(
                {"name": name, "path": str(target), "files": written + 1}
            )
        except Exception:  # noqa: BLE001
            continue
    return installed


def _posix_norm(name: str) -> str:
    """zip 成员名转 POSIX 规范路径。

    修复(2026-08-04): 原 lstrip(\"./\") 会把前导 ../ 的 . / 字符全部剥离,
    导致 zip slip 第一道校验被绕过(第二道 resolve 校验兜底, 但规范上要修)。
    normpath 保留前导 .., 由上层 startswith(\"..\") 正确拦截。
    """
    import posixpath as _pp

    norm = _pp.normpath(name.replace("\\", "/"))
    return "" if norm == "." else norm


@router.post("/wallpaper", response_model=None)
async def upload_wallpaper(body: WallpaperUploadRequest):
    """上传首页壁纸/视频背景(存 outputs/wallpaper.*)。

    支持图片(data:image/png|jpeg|webp, ≤6MB) + 视频
    (data:video/mp4|webm, ≤50MB, V2 首页动态背景)。

    Args:
        body: {"data_url": "data:image/png;base64,..." 或 "data:video/mp4;base64,..."}

    Returns:
        200: {"wallpaper": "/files/outputs/wallpaper.mp4", "type": "video"}
        400: {"error": "invalid_image" | "image_too_large"}
        500: {"error": "wallpaper_save_failed"}
    """
    import base64 as _b64
    import re as _re

    # 图片 + 视频(video 上限 50MB)
    m = _re.match(
        r"^data:(image/(png|jpeg|webp)|video/(mp4|webm));base64,(.+)$",
        body.data_url,
        _re.S,
    )
    if not m:
        return JSONResponse(status_code=400, content={"error": "invalid_image"})
    media_type = m.group(1)  # image/png | video/mp4 ...
    raw = m.group(4)
    is_video = media_type.startswith("video/")
    ext = m.group(2) or m.group(3) if is_video else m.group(2)
    # 大小上限: 图片 6MB, 视频 50MB(base64 解码后)
    try:
        decoded = _b64.b64decode(raw, validate=False)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_image"})
    limit = 50 * 1024 * 1024 if is_video else 6 * 1024 * 1024
    if len(decoded) > limit:
        return JSONResponse(status_code=400, content={"error": "image_too_large"})

    try:
        outputs_dir = _get_outputs_dir()
        outputs_dir.mkdir(parents=True, exist_ok=True)
        # 先清理旧壁纸/旧视频(unlink 失败不阻断写入, 沙箱环境可能拦截删除)
        for ext_old in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm"):
            old = outputs_dir / f"wallpaper{ext_old}"
            if old.exists():
                try:
                    old.unlink()
                except Exception:  # noqa: BLE001
                    pass
        target = outputs_dir / f"wallpaper.{ext}"
        target.write_bytes(decoded)
        return {
            "wallpaper": f"/files/outputs/wallpaper.{ext}",
            "type": "video" if is_video else "image",
        }
    except Exception:
        return JSONResponse(
            status_code=500,
            content={"error": "wallpaper_save_failed"},
        )


@router.delete("/wallpaper", response_model=None)
async def delete_wallpaper():
    """移除壁纸, 恢复默认背景。

    Returns:
        200: {"wallpaper": None}
    """
    import logging

    logger = logging.getLogger("private_agent.api.admin.wallpaper")
    name = _wallpaper_path()
    if name:
        try:
            (_get_outputs_dir() / name).unlink()
        except Exception as e:  # noqa: BLE001
            logger.warning("wallpaper 删除失败(%s): %s", name, e)
    return {"wallpaper": None}


@router.get("/mcp/servers", response_model=None)
async def list_mcp_servers():
    """返回 MCP servers 配置列表(蓝图 §5.3/§5.4)。

    Returns:
        200: {"servers": [{id, type, command, args, url, tags, timeout_sec, has_auth}],
              "protocol_version": str}
        has_auth: 是否已配置 Bearer token(不返回 token 明文)。
    """
    cfg = await _load_cfg()
    mcp_cfg = cfg.get("tools", {}).get("mcp", {})
    servers = []
    for s in mcp_cfg.get("servers", []):
        item = dict(s)
        if "auth_token_encrypted" in item:
            item["has_auth"] = True
            item.pop("auth_token_encrypted", None)
        else:
            item["has_auth"] = False
        servers.append(item)
    return {
        "servers": servers,
        "protocol_version": mcp_cfg.get("protocol_version", ""),
    }


# ──────────────────────────────────────────────────────────────────────────────
# V1.5 项-2 连接器"开箱即用": MCP 预置模板
# 模板为纯配置(不含凭证), 前端"从模板添加"选中即填充表单, 用户只补
# 凭证/URL/目录等个性化字段后走 POST /settings/mcp 正常保存。
# 维护: 增删模板需同步 docs/mcp-templates.md。
# ──────────────────────────────────────────────────────────────────────────────

# fmt: off
_MCP_TEMPLATES: list[dict] = [
    {
        "id": "fetch",
        "name": "Fetch · 网页抓取",
        "description": "官方参考服务器: 抓取网页/转 Markdown 入库(配合知识库网页入库)或喂给模型",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-fetch"],
        "env": {},
        "timeout_sec": 30,
        "protocol_version": "auto",
        "requires": [],
    },
    {
        "id": "time",
        "name": "Time · 时间/时区",
        "description": "官方参考服务器: 当前时间与多时区查询(离线, 零配置)",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-time"],
        "env": {},
        "timeout_sec": 30,
        "protocol_version": "auto",
        "requires": [],
    },
    {
        "id": "filesystem",
        "name": "Filesystem · 文件系统",
        "description": "官方参考服务器: 读写本地文件(需指定允许访问的目录)",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
        "env": {},
        "timeout_sec": 30,
        "protocol_version": "auto",
        "requires": ["将 args 中的 /path/to/dir 改为实际允许访问的目录"],
    },
    {
        "id": "memory",
        "name": "Memory · 持久记忆",
        "description": "官方参考服务器: 知识图谱式持久记忆(JSON 文件存储)",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "env": {},
        "timeout_sec": 30,
        "protocol_version": "auto",
        "requires": [],
    },
    {
        "id": "sequential-thinking",
        "name": "Sequential Thinking",
        "description": "官方参考服务器: 引导模型分步结构化思考(复杂推理场景)",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "env": {},
        "timeout_sec": 30,
        "protocol_version": "auto",
        "requires": [],
    },
    {
        "id": "github",
        "name": "GitHub · 仓库/Issue/PR",
        "description": "官方参考服务器: 仓库浏览/Issue/PR 查询(需 GITHUB_TOKEN)",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": ""},
        "timeout_sec": 30,
        "protocol_version": "auto",
        "requires": ["在环境变量 GITHUB_TOKEN 填入 GitHub Personal Access Token"],
    },
    {
        "id": "postgres",
        "name": "PostgreSQL · 数据库查询",
        "description": "官方参考服务器: 只读查询 PostgreSQL(需连接串, 谨慎评估数据暴露范围)",
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://user:pass@host:5432/db"],
        "env": {},
        "timeout_sec": 30,
        "protocol_version": "auto",
        "requires": ["将 args 中的连接串改为实际 PostgreSQL 连接串"],
    },
    {
        "id": "mempalace",
        "name": "Mempalace · 本地语义记忆",
        "description": "本地优先记忆系统(逐字存储+语义检索, 本项目已接入)。复用模板需先按 docs 安装 venv 并修改 command 为实际路径",
        "type": "stdio",
        "command": "D:\\skills\\mempalace-develop\\mempalace-develop\\.venv\\Scripts\\mempalace-mcp.exe",
        "args": [],
        "env": {},
        "timeout_sec": 30,
        "protocol_version": "auto",
        "requires": ["修改 command 为本机 mempalace-mcp 可执行文件路径(参考 docs/mcp-templates.md)"],
    },
    {
        "id": "ifind",
        "name": "iFind · 金融数据(Bearer 认证示例)",
        "description": "同花顺 iFinD 金融数据(A股/基金/宏观/新闻)。模板示例: 补 URL 与 Bearer token 即可接入同类 Bearer 认证的 HTTP MCP",
        "type": "http",
        "command": None,
        "args": [],
        "url": "https://your-ifind-mcp-endpoint/mcp",
        "env": {},
        "timeout_sec": 30,
        "protocol_version": "auto",
        "requires": ["将 url 改为实际 MCP 端点", "在认证 token 填入 Bearer token"],
    },
]
# fmt: on


@router.get("/mcp/templates", response_model=None)
async def list_mcp_templates():
    """MCP 预置模板列表(V1.5 项-2 连接器开箱即用)。

    Returns:
        200: {"templates": [
            {id, name, description, type, command, args, url, env,
             timeout_sec, protocol_version, requires: [需补充字段提示]}
        ]}
        模板为纯配置, 不含任何凭证; 前端选中后填充表单, 用户补凭证保存。
    """
    return {"templates": _MCP_TEMPLATES}


class McpServerRequest(BaseModel):
    """POST /settings/mcp 请求体: 新增/更新 MCP server(存 config_runtime)。"""

    name: str
    type: str = "http"  # http | stdio
    command: str | None = None  # stdio 用
    args: list[str] = []  # stdio 用
    url: str | None = None  # http 用
    enabled: bool = True
    timeout_sec: float = 30.0
    auth_token: str | None = None  # Bearer 认证 token(提供才更新, AES 加密存库)
    protocol_version: str = "auto"  # auto(自动协商) | 2026-07-28 | 2025-11-25
    # V1.2-6.2: 额外环境变量(stdio 模式注入子进程, 如 API Key)
    env: dict[str, str] | None = None


@router.post("/settings/mcp", response_model=None)
async def upsert_mcp_server(body: McpServerRequest):
    """新增/更新 MCP server 配置。

    MCP servers 是列表结构, 存 config_runtime 的 tools.mcp.servers(整体列表,
    runtime > yaml 整体覆盖)。MCP client 在启动时加载, 改动后重启后端生效。
    auth_token 提供时 AES-256-GCM 加密存储(auth_token_encrypted), 不落明文。
    """
    value = _build_server_value(
        name=body.name,
        server_type=body.type,
        url=body.url,
        command=body.command,
        args=body.args,
        auth_token=body.auth_token,
        enabled=body.enabled,
        timeout_sec=body.timeout_sec,
        protocol_version=body.protocol_version,
        env=body.env,
    )
    conn = await db.connect()
    try:
        await _write_servers_runtime(conn, [value])
    finally:
        await conn.close()
    return {"ok": True, "server": body.name, "count": 1}


def _build_server_value(
    *,
    name: str,
    server_type: str = "http",
    url: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    auth_token: str | None = None,
    enabled: bool = True,
    timeout_sec: float = 30.0,
    env: dict | None = None,
    protocol_version: str = "auto",
) -> dict:
    """构造 MCP server 配置 dict(含 auth_token AES 加密存储)。"""
    value: dict = {
        "id": name,
        "type": "http" if (server_type == "http" or url) else "stdio",
        "enabled": enabled,
        "timeout_sec": timeout_sec,
        "protocol_version": protocol_version,
    }
    if value["type"] == "http":
        value["url"] = url or ""
    else:
        value["command"] = command or ""
        value["args"] = list(args or [])
    if env:
        value["env"] = env
    if auth_token is not None and auth_token.strip():
        master = _ensure_master_key()
        from private_agent.config import secrets

        value["auth_token_encrypted"] = secrets.encrypt_api_key(
            auth_token.strip(), master
        )
    return value


async def _write_servers_runtime(conn, new_servers: list[dict]) -> None:
    """将 server dict 列表合并写入 config_runtime tools.mcp.servers(同名覆盖)。"""
    import json as _json

    row = await conn.fetchval(
        "SELECT value FROM config_runtime WHERE key = 'tools.mcp.servers'"
    )
    if row:
        servers = _json.loads(row) if isinstance(row, str) else row
    else:
        cfg = await _load_cfg()
        servers = list(cfg.get("tools", {}).get("mcp", {}).get("servers", []))

    for value in new_servers:
        servers = [
            s for s in servers
            if isinstance(s, dict)
            and (s.get("id") != value["id"] and s.get("name") != value["id"])
        ]
        servers.append(value)
    await _set_runtime(conn, "tools.mcp.servers", servers)


class McpImportRequest(BaseModel):
    """POST /settings/mcp/import-json 请求体: 粘贴 Claude Desktop 风格 JSON。"""

    config_json: str


@router.post("/settings/mcp/import-json", response_model=None)
async def import_mcp_json(body: McpImportRequest):
    """从 JSON 批量导入 MCP server(主流接入方式)。

    兼容格式:
    1. Claude Desktop: {"mcpServers": {"name": {"url"|"command", "args", "env", "headers"}}}
       - headers["Authorization"] == "Bearer xxx" → 提取为 auth_token(加密存储)
    2. 数组: [{"id"|"name", "type", "url"|"command", "auth_token", ...}]
    3. 单个对象: {"id", "type", "url"|"command", ...}
    """
    import json as _json

    try:
        parsed = _json.loads(body.config_json)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"JSON 解析失败: {e}"}

    servers = _parse_mcp_json(parsed)
    if not servers:
        return {"ok": False, "error": "未识别到任何 MCP server 配置"}

    values = [
        _build_server_value(
            name=s["id"],
            server_type=s.get("type", "http"),
            url=s.get("url"),
            command=s.get("command"),
            args=s.get("args"),
            auth_token=s.get("auth_token"),
            enabled=s.get("enabled", True),
            timeout_sec=s.get("timeout_sec", 30.0),
            env=s.get("env"),
            protocol_version=s.get("protocol_version", "auto"),
        )
        for s in servers
    ]
    conn = await db.connect()
    try:
        await _write_servers_runtime(conn, values)
    finally:
        await conn.close()
    return {
        "ok": True,
        "imported": [{"id": v["id"], "type": v["type"]} for v in values],
        "count": len(values),
    }


def _extract_auth(headers) -> str:
    """从 headers 中提取认证 token(兼容各种写法)。

    支持键名: Authorization / authorization / api-key / apikey / x-api-key / token。
    支持值格式: "Bearer xxx" / "Token xxx" / "ApiKey xxx" / 裸 token(直接是值)。
    返回值统一为裸 token(无前缀)。
    """
    if not isinstance(headers, dict):
        return ""
    for key in ("Authorization", "authorization", "api-key", "apikey", "x-api-key", "token"):
        val = headers.get(key)
        if isinstance(val, str) and val.strip():
            raw = val.strip()
            # 去掉常见认证前缀(不区分大小写)
            lowered = raw.lower()
            for prefix in ("bearer ", "token ", "apikey ", "basic "):
                if lowered.startswith(prefix):
                    return raw[len(prefix):].strip()
            return raw  # 裸 token
    return ""


def _parse_mcp_json(parsed) -> list[dict]:
    """把各种 JSON 形态解析为 server dict 列表。"""
    if isinstance(parsed, dict):
        # Claude Desktop 风格: {"mcpServers": {...}}
        mcp_servers = parsed.get("mcpServers")
        if isinstance(mcp_servers, dict):
            result = []
            for name, cfg in mcp_servers.items():
                if not isinstance(cfg, dict):
                    continue
                item = {"id": name, "type": "http" if cfg.get("url") else "stdio"}
                if cfg.get("protocol_version"):
                    item["protocol_version"] = cfg["protocol_version"]
                if item["type"] == "http":
                    item["url"] = cfg.get("url", "")
                    auth = _extract_auth(cfg.get("headers"))
                    if auth:
                        item["auth_token"] = auth
                else:
                    item["command"] = cfg.get("command", "")
                    item["args"] = list(cfg.get("args") or [])
                    item["env"] = cfg.get("env")
                if cfg.get("env") and item["type"] == "http":
                    item["env"] = cfg.get("env")
                result.append(item)
            return result
        # 单个 server 对象(兼容 headers 认证提取)
        if parsed.get("url") or parsed.get("command"):
            entry = dict(parsed)
            auth = _extract_auth(parsed.get("headers"))
            if auth:
                entry["auth_token"] = auth
            return [entry]
        return []
    if isinstance(parsed, list):
        # 数组: 兼容本项目格式 [{id|name, type, url|command, headers, ...}]
        result = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            entry["id"] = entry.get("id") or entry.get("name") or "server"
            auth = _extract_auth(item.get("headers"))
            if auth:
                entry["auth_token"] = auth
            result.append(entry)
        return result
    return []


@router.delete("/settings/mcp/{name}", response_model=None)
async def delete_mcp_server(name: str):
    """删除 MCP server 配置(config_runtime tools.mcp.servers 列表)。"""
    import json as _json

    conn = await db.connect()
    try:
        row = await conn.fetchval(
            "SELECT value FROM config_runtime WHERE key = 'tools.mcp.servers'"
        )
        if row:
            servers = _json.loads(row) if isinstance(row, str) else row
        else:
            cfg = await _load_cfg()
            servers = list(cfg.get("tools", {}).get("mcp", {}).get("servers", []))

        remaining = [
            s for s in servers
            if isinstance(s, dict) and (s.get("id") != name and s.get("name") != name)
        ]
        if remaining:
            await _set_runtime(conn, "tools.mcp.servers", remaining)
        else:
            await conn.execute(
                "DELETE FROM config_runtime WHERE key = 'tools.mcp.servers'"
            )
    finally:
        await conn.close()
    return {"ok": True, "server": name}


class McpAssembleRequest(BaseModel):
    """PUT /settings/mcp/{name}/assemble 请求体: 装配开关。"""

    assemble: bool


@router.put("/settings/mcp/{name}/assemble", response_model=None)
async def set_mcp_assemble(name: str, body: McpAssembleRequest):
    """设置 MCP server 的"装配到对话"开关(V2 P2)。

    assemble=false 时该 server 的工具不再进入对话(模型不可见/不可调用),
    但 server 配置保留(探活/测试仍可用)。默认 True。
    """
    import json as _json

    conn = await db.connect()
    try:
        row = await conn.fetchval(
            "SELECT value FROM config_runtime WHERE key = 'tools.mcp.servers'"
        )
        if row:
            servers = _json.loads(row) if isinstance(row, str) else row
        else:
            cfg = await _load_cfg()
            servers = list(cfg.get("tools", {}).get("mcp", {}).get("servers", []))

        target = next(
            (
                s for s in servers
                if isinstance(s, dict) and (s.get("id") == name or s.get("name") == name)
            ),
            None,
        )
        if target is None:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "server": name, "error": "server_not_found"},
            )
        target["assemble"] = body.assemble
        await _set_runtime(conn, "tools.mcp.servers", servers)
    finally:
        await conn.close()
    return {"ok": True, "server": name, "assemble": body.assemble}


class McpEnabledRequest(BaseModel):
    """PUT /settings/mcp/{name}/enabled 请求体(2026-08-04 设置页补齐)。"""

    enabled: bool


@router.put("/settings/mcp/{name}/enabled", response_model=None)
async def set_mcp_enabled(name: str, body: McpEnabledRequest):
    """设置 MCP server 启用/禁用(2026-08-04 设置页排查补齐)。

    与 assemble(装配到对话)区分: enabled=false 时该 server 整体停用
    (含测试/探活); assemble=false 仅工具不进对话。存储于 config_runtime。
    """
    import json as _json

    conn = await db.connect()
    try:
        row = await conn.fetchval(
            "SELECT value FROM config_runtime WHERE key = 'tools.mcp.servers'"
        )
        if row:
            servers = _json.loads(row) if isinstance(row, str) else row
        else:
            cfg = await _load_cfg()
            servers = list(cfg.get("tools", {}).get("mcp", {}).get("servers", []))

        target = next(
            (
                s for s in servers
                if isinstance(s, dict) and (s.get("id") == name or s.get("name") == name)
            ),
            None,
        )
        if target is None:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "server": name, "error": "server_not_found"},
            )
        target["enabled"] = body.enabled
        await _set_runtime(conn, "tools.mcp.servers", servers)
    finally:
        await conn.close()
    return {"ok": True, "server": name, "enabled": body.enabled}


@router.post("/settings/mcp/{name}/test", response_model=None)
async def test_mcp_server(name: str):
    """MCP server 连通性测试: 复用 MCPClient(自动协商协议 + SSE, 带认证)。

    探活成功后若为 auto 协商, 将协商出的协议版本持久化回 config_runtime,
    后续请求直接用该版本, 无需每次先失败重试。
    """
    import json as _json

    cfg = await _load_cfg()
    servers = cfg.get("tools", {}).get("mcp", {}).get("servers", [])
    svc = next(
        (s for s in servers if s.get("id") == name or s.get("name") == name),
        None,
    )
    if not svc:
        return {"ok": False, "server": name, "error": "server_not_found"}
    # 解密 Bearer token(配置时 AES 加密存储, 探活时还原)
    auth_token = _decrypt_server_auth(svc)
    try:
        if svc.get("type") == "http" or svc.get("url"):
            result = await _test_mcp_http(
                name,
                svc["url"],
                auth_token=auth_token,
                protocol_version=svc.get("protocol_version", "auto"),
            )
            # 持久化 auto 协商结果(避免后续每次先失败重试)
            if result.get("ok") and result.get("negotiated"):
                conn = await db.connect()
                try:
                    row = await conn.fetchval(
                        "SELECT value FROM config_runtime WHERE key = 'tools.mcp.servers'"
                    )
                    if row:
                        srv_list = _json.loads(row) if isinstance(row, str) else row
                        for item in srv_list:
                            if isinstance(item, dict) and (
                                item.get("id") == name or item.get("name") == name
                            ):
                                item["protocol_version"] = result["negotiated"]
                        await _set_runtime(conn, "tools.mcp.servers", srv_list)
                finally:
                    await conn.close()
            return result
        return await _test_mcp_stdio(name, svc.get("command", ""), svc.get("args", []), auth_token)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "server": name, "error": f"{type(e).__name__}: {e}"}


def _decrypt_server_auth(svc: dict) -> str:
    """解密 MCP server 的 Bearer token(无则返回空串)。"""
    encrypted = svc.get("auth_token_encrypted")
    if not encrypted:
        return ""
    try:
        from private_agent.config import secrets

        master = _ensure_master_key()
        return secrets.decrypt_api_key(encrypted, master)
    except Exception:  # noqa: BLE001 - 解密失败按无 token 处理(探活会报 401 提示)
        return ""


async def _test_mcp_http(
    name: str, url: str, auth_token: str = "", protocol_version: str = "auto"
) -> dict:
    """HTTP MCP 探活: 复用 MCPClient(自动协商协议 + SSE 流式 + Bearer 认证)。"""
    from private_agent.tools.mcp_client import MCPClient, MCPClientConfig

    client = MCPClient(
        MCPClientConfig(
            server_id=name,
            server_type="http",
            url=url,
            timeout_sec=10,
            auth_token=auth_token,
            protocol_version=protocol_version,
        )
    )
    async with client:
        await client.connect()
        tools = await client.discover_tools()
        return {
            "ok": True,
            "server": name,
            "protocol": client.negotiated_version or protocol_version,
            "negotiated": client.negotiated_version,
            "tools_count": len(tools),
        }


async def _test_mcp_stdio(name: str, command: str, args: list[str], auth_token: str = "") -> dict:
    import asyncio

    if not command:
        return {"ok": False, "server": name, "error": "stdio server 缺少 command"}
    # 2026-08-07: 注入 PYTHONIOENCODING=utf-8 + server 级 env —— 与主链路
    # MCPClient(env={**os.environ, **config.env}) 对齐; 缺 PYTHONIOENCODING
    # 时 Windows Python 子进程 stdout 为 GBK, 含中文 JSON 响应乱码 →
    # tools/list 解析为空(Searchpin tools_count=0 根因)
    svc_env = {"PYTHONIOENCODING": "utf-8"}
    proc = await asyncio.create_subprocess_exec(
        command, *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **svc_env},
    )
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {
                "_meta": {
                    "protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "private-agent-test",
                        "version": "0.1",
                    },
                    "capabilities": {},
                }
            },
        }
        import json as _json

        proc.stdin.write((_json.dumps(payload) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        data = _json.loads(line.decode("utf-8"))
        result = data.get("result", {})
        tools = result.get("tools", [])
        return {
            "ok": True,
            "server": name,
            "protocol": "2026-07-28",
            "server_info": result.get("serverInfo", {}).get("name", ""),
            "tools_count": len(tools),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "server": name, "error": f"{type(e).__name__}: {e}"}
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass



class SaveVersionRequest(BaseModel):
    """POST /admin/skills/{name}/save-version 请求体(AC-12)。"""

    version: str
    manifest: dict
    system_prompt: str = ""
    tools_yaml: list[dict] = []


# ══════════════════════════════════════════════════════════════════════════
# Skill 版本保存 + SkillVersionListener 触发(M4 §8.13,AC-12)
# ══════════════════════════════════════════════════════════════════════════


@router.post("/skills/{skill_name}/save-version", response_model=None)
async def save_skill_version(skill_name: str, request: SaveVersionRequest):
    """AC-12: 保存新版本到 version_snapshots + 触发 SkillVersionListener 快速回归。

    Returns:
        200: {"saved_version": str, "scope": "skill"}
        500: {"error": "save_version_failed"}
    """
    import json as _json

    from private_agent.eval.repos import VersionSnapshotRepo
    from private_agent.observability.logging import setup_logger

    logger = setup_logger("private_agent.api.admin.save_version")
    try:
        conn = await db.connect()
        try:
            # 1. 保存到 version_snapshots
            payload = {
                "skill_name": skill_name,
                "manifest": request.manifest,
                "system_prompt": request.system_prompt,
                "tools_yaml": request.tools_yaml,
            }
            repo = VersionSnapshotRepo(conn)
            await repo.save(
                scope="skill",
                version=request.version,
                payload=payload,
            )
            # 2. 同步 skills 表(upsert)
            await conn.execute(
                """
                INSERT INTO skills (name, version, manifest, system_prompt, tools)
                VALUES ($1, $2, $3::jsonb, $4, $5::jsonb)
                ON CONFLICT (name) DO UPDATE
                    SET version=$2, manifest=$3::jsonb, system_prompt=$4,
                        tools=$5::jsonb, updated_at=now()
                """,
                skill_name,
                request.version,
                _json.dumps(request.manifest),
                request.system_prompt,
                _json.dumps(request.tools_yaml),
            )
            # 3. 触发 SkillVersionListener(失败仅日志,不阻塞)
            try:
                cfg = loader.load_config()
                listener = _build_skill_version_listener(cfg)
                await listener.on_skill_version_saved(
                    skill_name=skill_name,
                    version=request.version,
                    conn=conn,
                )
            except Exception as listener_err:
                logger.warning(
                    "SkillVersionListener 触发失败(不阻塞版本保存): %s",
                    listener_err,
                )
            return {"saved_version": request.version, "scope": "skill"}
        finally:
            await conn.close()
    except Exception as e:
        logger.exception("save_skill_version 失败")
        return JSONResponse(
            status_code=500,
            content={"error": "save_version_failed", "detail": str(e)},
        )