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


# ══════════════════════════════════════════════════════════════════════════
# 记忆/设置查询端点(V1.5 Phase 1 Task 12, 供前端记忆页/设置页)
# ══════════════════════════════════════════════════════════════════════════


@router.get("/memories", response_model=None)
async def list_memories(type: str | None = None, limit: int = 100):
    """查询活跃用户记忆列表(蓝图 §4.3)。

    Args:
        type: 记忆类型过滤(preference/fact/todo/decision,可选)。
        limit: 返回条数上限(默认 100)。

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
                WHERE is_active = TRUE AND ($1::text IS NULL OR type = $1)
                ORDER BY importance DESC, created_at DESC
                LIMIT $2
                """,
                type,
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

    cfg = await _load_cfg()
    providers = cfg.get("models", {}).get("providers", {})
    result = []
    for name, prov in providers.items():
        env_var = f"PA_{name.upper()}_API_KEY"
        key_val = os.environ.get(env_var, "")
        result.append({
            "name": name,
            "enabled": prov.get("enabled", True),
            "model_name": prov.get("model_name"),
            "base_url": prov.get("base_url"),
            # 不返回 key 明文,仅返回是否已配置(非空且非 test-key 占位)
            "api_key_configured": bool(key_val) and key_val != "test-key",
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
    """确保 PA_MASTER_KEY 可用: 未设置时生成 64 hex 并追加到 backend/.env。"""
    import os

    from private_agent.config import secrets

    hex_key = os.environ.get("PA_MASTER_KEY", "")
    if hex_key:
        return bytes.fromhex(hex_key)
    # 自动生成并持久化到 backend/.env(个人应用, 首次使用自动初始化)
    import secrets as _secrets

    new_key = _secrets.token_hex(32)  # 64 hex chars = 32 bytes
    cfg = loader.load_config()
    import os as _os

    workspace = _os.path.expandvars(cfg.get("system", {}).get("workspace_root", "."))
    env_path = _os.path.join(workspace, ".env")
    try:
        with open(env_path, "a", encoding="utf-8") as f:
            f.write(f"\n# AES master key (auto-generated)\nPA_MASTER_KEY={new_key}\n")
    except OSError:
        pass
    os.environ["PA_MASTER_KEY"] = new_key
    return bytes.fromhex(new_key)


class ProviderUpdateRequest(BaseModel):
    """PUT /settings/providers/{name} 请求体(至少一项)。"""

    base_url: str | None = None
    model_name: str | None = None
    enabled: bool | None = None
    api_key: str | None = None  # 提供才更新; 明文仅走 HTTPS/本机回环, 加密后存库


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

    conn = await db.connect()
    try:
        prefix = f"models.providers.{name}"
        if body.base_url is not None:
            await _set_runtime(conn, f"{prefix}.base_url", body.base_url)
        if body.model_name is not None:
            await _set_runtime(conn, f"{prefix}.model_name", body.model_name)
        if body.enabled is not None:
            await _set_runtime(conn, f"{prefix}.enabled", bool(body.enabled))

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


@router.get("/sessions", response_model=None)
async def list_sessions(limit: int = 50, has_messages: bool = True):
    """列出历史会话(供侧边栏任务树, 蓝图 §2.10)。

    Args:
        limit: 返回条数上限(默认 50)。
        has_messages: 仅返回有真实对话消息的会话(默认 True, 过滤掉
            测试/占位产生的空会话, 让任务树只显示日常对话)。

    Returns:
        200: [{
            id, title, status, model_id, summary,
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
                SELECT s.id, s.title, s.status, s.model_id, s.summary,
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
                WHERE NOT $2::bool
                   OR EXISTS (SELECT 1 FROM messages m WHERE m.session_id = s.id)
                ORDER BY s.updated_at DESC
                LIMIT $1
                """,
                min(max(int(limit), 1), 200),
                bool(has_messages),
            )
            result = []
            for r in rows:
                title = r["title"] or r["summary"]
                if not title:
                    first = r["first_user_content"] or ""
                    title = first[:30].replace("\n", " ") if first else f"#{r['id']}"
                result.append({
                    "id": r["id"],
                    "title": title,
                    "status": r["status"],
                    "model_id": r["model_id"],
                    "summary": r["summary"],
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


# ══════════════════════════════════════════════════════════════════════════
# 首页壁纸上传/查询/移除(V1.5, 前端 HomeView)
# ══════════════════════════════════════════════════════════════════════════


def _wallpaper_path() -> str | None:
    """返回当前壁纸文件路径(不存在返回 None)。

    按修改时间取最新(而非固定扩展名顺序),避免残留旧扩展名文件
    (如 .png 测试图)覆盖用户最新上传的 .jpeg。
    """
    outputs_dir = _get_outputs_dir()
    candidates: list = []
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = outputs_dir / f"wallpaper{ext}"
        if p.exists() and p.is_file():
            candidates.append(p)
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return newest.name


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
    """返回当前壁纸可访问路径与显示样式。

    Returns:
        200: {
            "wallpaper": "/files/outputs/wallpaper.png" | None,
            "style": {"position_x": float, "position_y": float, "fit": str},
        }
    """
    name = _wallpaper_path()
    return {
        "wallpaper": f"/files/outputs/{name}" if name else None,
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


@router.post("/wallpaper", response_model=None)
async def upload_wallpaper(body: WallpaperUploadRequest):
    """上传首页壁纸(存 outputs/wallpaper.*)。

    Args:
        body: {"data_url": "data:image/png;base64,..."}

    Returns:
        200: {"wallpaper": "/files/outputs/wallpaper.png"}
        400: {"error": "invalid_image"}
        500: {"error": "wallpaper_save_failed"}
    """
    import base64 as _b64
    import re as _re

    m = _re.match(r"^data:image/(png|jpeg|webp);base64,(.+)$", body.data_url, _re.S)
    if not m:
        return JSONResponse(status_code=400, content={"error": "invalid_image"})
    ext = m.group(1)
    raw = m.group(2)
    # 大小上限 6MB(base64 解码后)
    try:
        decoded = _b64.b64decode(raw, validate=False)
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_image"})
    if len(decoded) > 6 * 1024 * 1024:
        return JSONResponse(status_code=400, content={"error": "image_too_large"})

    try:
        outputs_dir = _get_outputs_dir()
        outputs_dir.mkdir(parents=True, exist_ok=True)
        # 先清理旧壁纸(unlink 失败不阻断写入, 沙箱环境可能拦截删除)
        for ext_old in (".png", ".jpg", ".jpeg", ".webp"):
            old = outputs_dir / f"wallpaper{ext_old}"
            if old.exists():
                try:
                    old.unlink()
                except Exception:  # noqa: BLE001
                    pass
        target = outputs_dir / f"wallpaper.{ext}"
        target.write_bytes(decoded)
        return {"wallpaper": f"/files/outputs/wallpaper.{ext}"}
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
    proc = await asyncio.create_subprocess_exec(
        command, *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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