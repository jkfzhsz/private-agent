"""蓝图 §2.15 Python Sidecar 启动入口 (§2.2 uvicorn+asyncio).

集成 /health 控制面、/ws 数据面,端口与日志由 config.yaml 驱动(蓝图 §9.13)。
"""
from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from private_agent.api import admin
from private_agent.config import loader
from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop
from private_agent.memory.manager import MemoryManager
from private_agent.memory.memories_repo import MemoriesRepo
from private_agent.observability.logging import setup_logger
from private_agent.storage import db, ws_offset

app = FastAPI(title="Private Agent Sidecar", version="0.1.0")
app.include_router(admin.router)

_logger = setup_logger("private_agent.main")

_scheduler = None  # APScheduler 单例(startup 创建,shutdown 停止)


def _build_adapter(cfg):
    """构造模型适配器(默认 FallbackChain,测试可 monkeypatch)。"""
    from private_agent.models.registry import build_fallback_chain
    return build_fallback_chain(cfg)


def _get_tools(cfg):
    """获取工具列表(M2:从 ToolRegistry 注册所有内置工具,测试可 monkeypatch)。

    MCP 工具发现由 startup 阶段异步完成,此处仅返回内置工具。
    """
    from private_agent.tools.builtins import register_all_builtins
    from private_agent.tools.registry import ToolRegistry
    registry = ToolRegistry()
    register_all_builtins(registry)
    return registry.list_tools()


def _get_system_prompt(cfg):
    """获取系统提示词(M1 默认值,测试可 monkeypatch)。"""
    return "You are a helpful assistant."


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
                    cfg = loader.load_config()
                    tools = _get_tools(cfg)
                    conn = await db.connect()
                    try:
                        # 构造 MemoryManager(蓝图 §4.2-§4.5)
                        memories_repo = MemoriesRepo(conn)
                        memory_mgr = MemoryManager(
                            memories_repo=memories_repo,
                            compress_adapter=None,  # MVP 复用压缩模型,暂缺
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
                            system_prompt=_get_system_prompt(cfg),
                            tools=tools,
                            memory_manager=memory_mgr,
                        )
                        await cm.ensure_initial(conn)
                        adapter = _build_adapter(cfg)
                        loop = ReactLoop(
                            session_id=session_id,
                            context_manager=cm,
                            adapter=adapter,
                            tools=tools,
                            conn=conn,
                        )
                        await loop.run_turn(content)
                        # 排空 event_queue,逐条推送 react_event
                        while not loop.event_queue.empty():
                            event = loop.event_queue.get_nowait()
                            await ws.send_json(event)
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
                    await ws.send_json({
                        "type": "error",
                        "message": "user_message_failed",
                    })
    except WebSocketDisconnect:
        pass


@app.on_event("startup")
async def _on_startup() -> None:
    """启动钩子(蓝图 §2.10 + §9.4 AC-5 + §9.13)。

    - db.create_pool 创建连接池(失败时 log warning,不阻止启动)
    - 注册 APScheduler TTL 清理任务(cron `0 3 * * *`)
    - scheduler.start()
    """
    global _scheduler
    cfg = loader.load_config()
    try:
        db._pool = await db.create_pool(cfg)
    except Exception as e:
        _logger.warning(f"DB pool creation failed at startup: {e}")
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from private_agent.storage.ttl_cleanup import schedule_ttl_cleanup
    _scheduler = AsyncIOScheduler()
    schedule_ttl_cleanup(_scheduler, cfg)
    _scheduler.start()


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    """关闭钩子:停止 scheduler + 关闭连接池。"""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    await db.close_pool()


def run_sidecar() -> None:
    """启动 Sidecar:从 config.yaml 读取端口,uvicorn 监听(蓝图 §2.2 + §9.13)."""
    import uvicorn

    cfg = loader.load_config()
    host = cfg["server"]["http"]["host"]
    http_port = cfg["server"]["http"]["port"]
    logger = setup_logger("private_agent.main")
    logger.info(f"Sidecar started: host={host} http_port={http_port}")
    uvicorn.run(app, host=host, port=http_port)


if __name__ == "__main__":
    run_sidecar()