"""蓝图 §2.15 Python Sidecar 启动入口 (§2.2 uvicorn+asyncio).

集成 /health 控制面、/ws 数据面,端口与日志由 config.yaml 驱动(蓝图 §9.13)。
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from private_agent.api import admin, eval, files
from private_agent.config import loader
from private_agent.core.checkpoint import CheckpointManager
from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop
from private_agent.memory.manager import MemoryManager
from private_agent.memory.memories_repo import MemoriesRepo
from private_agent.observability.logging import setup_logger
from private_agent.storage import db, ws_offset

app = FastAPI(title="Private Agent Sidecar", version="0.1.0")
# 浏览器模式(vite dev)跨域访问 8765:允许 localhost 任意端口来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(admin.router)
app.include_router(eval.router)
app.include_router(files.router)

# B1 P1-2: 模块级仅持有 logger 句柄(无 handler),file_path 由 _on_startup / run_sidecar 延迟配置
_logger = logging.getLogger("private_agent.main")

_scheduler = None  # APScheduler 单例(startup 创建,shutdown 停止)
# MCP 外轨工具管理器(进程级单例, 懒连接 + 缓存, shutdown 时关闭)
_mcp_manager = None  # type: ignore[assignment]


def _get_mcp_manager():
    """惰性初始化 MCPToolManager(避免 import 时产生循环依赖)。"""
    global _mcp_manager
    if _mcp_manager is None:
        from private_agent.tools.mcp_tools import MCPToolManager

        _mcp_manager = MCPToolManager()
    return _mcp_manager


def _build_adapter(cfg):
    """构造模型适配器(默认 FallbackChain,测试可 monkeypatch)。"""
    from private_agent.models.registry import build_fallback_chain
    return build_fallback_chain(cfg)


def _build_compress_adapter(cfg):
    """构造压缩模型适配器(蓝图 §4.2,spec AC-7),测试可 monkeypatch。"""
    from private_agent.models.registry import build_compress_adapter
    return build_compress_adapter(cfg)


async def _get_frozen_tools(cfg, session_id: int, conn):
    """内置工具按 skill 白名单过滤(与 activate_skill 完全同源)。

    用于 ContextManager 的 Frozen Zone hash 计算——MCP 工具属运行时扩展,
    不参与锁定 hash, 保证 activate 时与 WS 处理时 hash 一致。

    - session 未 activate → 全部内置工具
    - session 已 activate → skill manifest.dependencies.tools 白名单过滤(AC-3)
    """
    from private_agent.skills.loader import SkillLoader
    from private_agent.tools.builtins import register_all_builtins
    from private_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_all_builtins(registry)

    locked_skill = await conn.fetchval(
        "SELECT locked_skill_name FROM sessions WHERE id = $1",
        session_id,
    )
    if not locked_skill:
        return registry.list_tools()

    loader = SkillLoader.from_cfg(cfg)
    skill = await loader.load(locked_skill, conn)
    whitelist = [t.name for t in skill.manifest.dependencies.tools if t.enabled]
    return registry.list_tools_for_session(whitelist)


async def _get_tools(cfg, session_id: int, conn):
    """获取全量工具列表: 内置白名单(frozen 同源) + MCP 外轨(全量)。

    - frozen_tools 供 ContextManager hash 锁定(与 activate 一致)
    - 全部工具供 ReactLoop 调用(内置 + mcp__ 前缀工具)
    """
    frozen_tools = await _get_frozen_tools(cfg, session_id, conn)
    mcp_tools = await _get_mcp_manager().get_tools(cfg)
    return frozen_tools + mcp_tools


async def _get_system_prompt(cfg, session_id: int, conn):
    """获取系统提示词(测试可 monkeypatch)。

    - session 未 activate (locked_skill_name IS NULL) → 返回默认提示词(M1 行为)
    - session 已 activate → 返回锁定 skill 的 system_prompt(模板替换 + 少样本,
      与 activate_skill 生成完全一致,保证 Frozen Zone hash 稳定,AC-3)
    """
    from private_agent.skills.example_loader import ExampleLoader
    from private_agent.skills.loader import SkillLoader
    from private_agent.skills.manager import SkillManager
    from private_agent.tools.builtins import register_all_builtins
    from private_agent.tools.registry import ToolRegistry

    locked_skill = await conn.fetchval(
        "SELECT locked_skill_name FROM sessions WHERE id = $1",
        session_id,
    )
    if not locked_skill:
        return "You are a helpful assistant."

    loader = SkillLoader.from_cfg(cfg)
    skill = await loader.load(locked_skill, conn)
    registry = ToolRegistry()
    register_all_builtins(registry)
    mgr = SkillManager(
        loader=loader,
        example_loader=ExampleLoader.from_cfg(cfg),
        tool_registry=registry,
    )
    return await mgr.build_system_prompt(skill, locked_skill, session_id, conn)


@app.get("/")
async def root() -> dict[str, str]:
    """根路由(健康检查占位)。"""
    return {"status": "sidecar_running"}


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查端点(蓝图 §9.4 M0 Done Criteria 1)。"""
    return {"status": "ok"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """WS 数据面端点(蓝图 §2.3)。

    支持消息类型:
    - ping: 心跳,返回 pong
    - replay: ws_offset 补发(蓝图 §2.3 line 449),从 react_events 查 turn > last_turn 的事件
    """
    await ws.accept()
    session_id = None
    try:
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type")
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
            elif msg_type == "replay":
                # ws_offset 补发(蓝图 §2.3 line 449)
                try:
                    session_id = int(msg["session_id"])
                    last_turn = int(msg.get("last_turn", 0))
                    full = bool(msg.get("full", False))
                except (KeyError, ValueError, TypeError):
                    await ws.send_json({
                        "type": "error",
                        "message": "invalid replay: session_id and last_turn must be int",
                    })
                    continue
                if session_id <= 0 or last_turn < 0:
                    await ws.send_json({
                        "type": "error",
                        "message": "invalid replay: session_id>0 and last_turn>=0 required",
                    })
                    continue
                try:
                    conn = await db.connect()
                    try:
                        messages = await ws_offset.build_replay_messages(
                            conn, session_id=session_id, last_turn=last_turn,
                            full=full,
                        )
                    finally:
                        await conn.close()
                    for m in messages:
                        await ws.send_json(m)
                except Exception:
                    # DB 异常不冒泡到 WS 循环外,发 error 后继续处理下一条消息
                    await ws.send_json({
                        "type": "error",
                        "message": "replay_failed",
                    })
            elif msg_type == "ack":
                # AC-6: 客户端 ACK,回写 config_runtime ws_offset:{session_id}=turn
                try:
                    session_id = int(msg["session_id"])
                    turn = int(msg["turn"])
                except (KeyError, ValueError, TypeError):
                    await ws.send_json({
                        "type": "error",
                        "message": "invalid ack: session_id and turn must be int",
                    })
                    continue
                if session_id <= 0 or turn < 0:
                    await ws.send_json({
                        "type": "error",
                        "message": "invalid ack: session_id>0 and turn>=0 required",
                    })
                    continue
                try:
                    conn = await db.connect()
                    try:
                        await ws_offset.handle_ack(
                            conn, session_id=session_id, turn=turn,
                        )
                    finally:
                        await conn.close()
                    await ws.send_json({
                        "type": "ack_confirm",
                        "session_id": session_id,
                        "turn": turn,
                    })
                except Exception:
                    # DB 异常不冒泡,发 error 后继续处理下一条消息
                    await ws.send_json({
                        "type": "error",
                        "message": "ack_failed",
                    })
            elif msg_type == "user_message":
                # AC-1: 用户消息触发 ReAct 循环(蓝图 §2.4/§2.6)
                try:
                    session_id = int(msg["session_id"])
                    content = str(msg["content"])
                except (KeyError, ValueError, TypeError):
                    await ws.send_json({
                        "type": "error",
                        "message": "invalid user_message: session_id (int) and content required",
                    })
                    continue
                if session_id <= 0:
                    await ws.send_json({
                        "type": "error",
                        "message": "invalid user_message: session_id>0 required",
                    })
                    continue
                try:
                    cfg = await _load_cfg_with_runtime()
                    conn = await db.connect()
                    try:
                        # 会话懒创建(蓝图 §2.10):WS 收到首条 user_message 时,
                        # sessions 无该行则插入,保证 ensure_initial 外键不失败
                        # 懒创建: title 留空(NULL), list_sessions 用首条用户消息兜底生成可读标题
                        exists = await conn.fetchval(
                            "SELECT 1 FROM sessions WHERE id=$1", session_id
                        )
                        if exists is None:
                            await conn.execute(
                                "INSERT INTO sessions (id) VALUES ($1)",
                                session_id,
                            )
                        # frozen_tools: 内置白名单(与 activate 同源, hash 锁定)
                        # tools: 全量(内置 + MCP, 供 ReactLoop 调用)
                        frozen_tools = await _get_frozen_tools(cfg, session_id, conn)
                        tools = frozen_tools + await _get_mcp_manager().get_tools(cfg)
                        # 构造 MemoryManager(蓝图 §4.2-§4.5)
                        memories_repo = MemoriesRepo(conn)
                        memory_mgr = MemoryManager(
                            memories_repo=memories_repo,
                            compress_adapter=_build_compress_adapter(cfg),
                            extract_interval_turns=cfg.get("memory", {}).get(
                                "extract_interval_turns", 8
                            ),
                            inject_limit=cfg.get("memory", {}).get("inject_limit", 10),
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
                        cm = ContextManager(
                            session_id=session_id,
                            system_prompt=await _get_system_prompt(
                                cfg, session_id, conn
                            ),
                            tools=frozen_tools,
                            memory_manager=memory_mgr,
                        )
                        await cm.ensure_initial(conn)
                        adapter = _build_adapter(cfg)
                        # per-provider 对话参数上限: 取 session.model_id 或 fallback 首选
                        from private_agent.config.loader import resolve_provider_limits

                        model_id = await conn.fetchval(
                            "SELECT model_id FROM sessions WHERE id = $1", session_id
                        )
                        chain = cfg.get("models", {}).get("router", {}).get(
                            "fallback_chain", []
                        )
                        provider_name = model_id or (chain[0] if chain else None)
                        provider_limits = resolve_provider_limits(cfg, provider_name)
                        loop = ReactLoop(
                            session_id=session_id,
                            context_manager=cm,
                            adapter=adapter,
                            tools=tools,
                            conn=conn,
                            cfg=cfg,
                            provider_limits=provider_limits,
                            # 实时推送: 事件边产生边发给 WS(流式逐块, 而非结束后批量)
                            event_sink=lambda ev: ws.send_json(ev),
                        )
                        await loop.run_turn(content)
                        # 事件已通过 event_sink 实时推送, 无需再排空 event_queue
                        await ws.send_json({
                            "type": "turn_end",
                            "session_id": session_id,
                            "turn": loop._turn,
                        })
                        # 每轮结束后触发记忆提取(蓝图 §4.2)
                        await memory_mgr.maybe_extract(
                            session_id=session_id, current_turn=loop._turn,
                        )
                    finally:
                        await conn.close()
                except Exception:
                    _logger.exception("user_message handling failed")
                    try:
                        await CheckpointManager.mark_session_interrupted(
                            conn, session_id
                        )
                    except Exception:
                        pass
                    await ws.send_json({
                        "type": "error",
                        "message": "user_message_failed",
                    })
    except WebSocketDisconnect:
        if session_id is not None:
            try:
                conn = await db.connect()
                try:
                    await CheckpointManager.mark_session_interrupted(
                        conn, session_id
                    )
                finally:
                    await conn.close()
            except Exception:
                _logger.exception(
                    "Failed to mark session interrupted on disconnect"
                )


@app.on_event("startup")
async def _on_startup() -> None:
    """启动钩子(蓝图 §2.10 + §9.4 AC-5 + §9.13)。

    - db.create_pool 创建连接池(失败时 log warning,不阻止启动)
    - 注册 APScheduler TTL 清理任务(cron `0 3 * * *`)
    - scheduler.start()
    - B1 P1-2: 读 cfg.observability.logging.file_path 配置 FileHandler
    """
    global _scheduler, _logger
    cfg = loader.load_config()
    # B1 P1-2: 配置 file_path(展开环境变量)
    _configure_logger(cfg)
    try:
        db._pool = await db.create_pool(cfg)
    except Exception as e:
        _logger.warning(f"DB pool creation failed at startup: {e}")
    # 启动自动迁移(migrate_all 幂等:全新库建表,已有库跑增量补丁)
    if db._pool is not None:
        from private_agent.storage import migrations

        try:
            async with db._pool.acquire() as conn:
                await migrations.migrate_all(conn)
            _logger.info("DB schema migrated (idempotent)")
            # 从 config_runtime 恢复 AES 加密的 API key → 环境变量(设置页录入后重启仍生效)
            await _restore_keys_from_runtime()
        except Exception as e:
            _logger.warning(f"DB schema migration failed at startup: {e}")
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from private_agent.storage.ttl_cleanup import schedule_ttl_cleanup
    _scheduler = AsyncIOScheduler()
    schedule_ttl_cleanup(_scheduler, cfg)
    _scheduler.start()


async def _load_cfg_with_runtime() -> dict:
    """加载 config.yaml 并合并 config_runtime 运行时覆盖(蓝图 §2.12, runtime > yaml)。

    设置页对 provider/MCP 的修改写入 config_runtime 后, 后续对话即用合并后的配置。
    """
    conn = await db.connect()
    try:
        return await loader.load_config_with_overrides(conn)
    finally:
        await conn.close()


async def _restore_keys_from_runtime() -> None:
    """从 config_runtime 读取 models.providers.*.api_key_encrypted, 解密后设置环境变量。

    设置页录入的 API key 重启后依赖此恢复(否则 env 丢失 → 模型 401)。
    """
    import os

    from private_agent.config import secrets

    master_hex = os.environ.get("PA_MASTER_KEY", "")
    if not master_hex:
        return  # 无 master key 则跳过(从未在设置页录入过 key)
    try:
        master = bytes.fromhex(master_hex)
    except ValueError:
        return
    async with db._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value FROM config_runtime WHERE key LIKE 'models.providers.%.api_key_encrypted'"
        )
    for row in rows:
        key_path = row["key"]  # models.providers.deepseek.api_key_encrypted
        parts = key_path.split(".")
        if len(parts) < 4:
            continue
        prov_name = parts[2]
        try:
            plain = secrets.decrypt_api_key(row["value"], master)
        except Exception:  # noqa: BLE001
            continue
        os.environ[f"PA_{prov_name.upper()}_API_KEY"] = plain


def _configure_logger(cfg: dict) -> None:
    """B1 P1-2: 从 cfg 读 file_path 并配置 logger(FileHandler 失败时降级仅 stdout)。"""
    global _logger
    obs_cfg = cfg.get("observability", {}).get("logging", {})
    file_path = obs_cfg.get("file_path")
    level = obs_cfg.get("level", "INFO").upper()
    level_int = getattr(logging, level, logging.INFO)
    expanded_path = os.path.expandvars(file_path) if file_path else None
    try:
        _logger = setup_logger(
            "private_agent.main", level=level_int, file_path=expanded_path
        )
    except (OSError, PermissionError) as e:
        # FileHandler 创建失败(路径不可写/权限不足)时降级仅 StreamHandler
        _logger = setup_logger("private_agent.main", level=level_int)
        _logger.warning(f"FileHandler setup failed, fallback to stdout only: {e}")


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    """关闭钩子:停止 scheduler + 关闭 MCP 客户端 + 关闭连接池。"""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    # 断开 MCP 客户端(进程级单例, 装配用)
    try:
        await _mcp_manager.close_all()
    except Exception:  # noqa: BLE001
        pass
    await db.close_pool()


def run_sidecar() -> None:
    """启动 Sidecar:从 config.yaml 读取端口,uvicorn 监听(蓝图 §2.2 + §9.13)."""
    import uvicorn

    global _logger
    cfg = loader.load_config()
    # B1 P1-2: 配置 file_path(展开环境变量)
    _configure_logger(cfg)
    host = cfg["server"]["http"]["host"]
    http_port = cfg["server"]["http"]["port"]
    _logger.info(f"Sidecar started: host={host} http_port={http_port}")
    uvicorn.run(app, host=host, port=http_port)


if __name__ == "__main__":
    run_sidecar()