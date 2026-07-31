"""蓝图 §2.15 Python Sidecar 启动入口 (§2.2 uvicorn+asyncio).

集成 /health 控制面、/ws 数据面,端口与日志由 config.yaml 驱动(蓝图 §9.13)。
"""
from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from private_agent.config import loader
from private_agent.observability.logging import setup_logger

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
    """WS 数据面最小端点(蓝图 §2.3)。

    B3.2b 将集成 ws_offset 补发(依赖 config_runtime + react_events 表)。
    """
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
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
