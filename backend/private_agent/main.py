"""蓝图 §2.15 Python Sidecar 启动入口 (§2.2 uvicorn+asyncio).

集成 /health 控制面、/ws 数据面,端口与日志由 config.yaml 驱动(蓝图 §9.13)。
"""
from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from private_agent.config import loader
from private_agent.observability.logging import setup_logger
from private_agent.storage import db, ws_offset

app = FastAPI(title="Private Agent Sidecar", version="0.1.0")


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
    except WebSocketDisconnect:
        pass


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
