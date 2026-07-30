"""蓝图 §2.15 Python Sidecar 启动入口 (§2.2 uvicorn+asyncio).

B2.1:最小启动 + 打印 HTTP 端口。
B3.1:在 app 上添加 /health 与控制面路由。
B3.2:挂载 WS 数据面。
"""
from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

# 蓝图 §9.13 server.http.port / server.websocket.port
HTTP_PORT = 8765
WS_PORT = 8766

app = FastAPI(title="Private Agent Sidecar", version="0.1.0")


@app.get("/")
async def root() -> dict[str, str]:
    """B2.1 占位根路由;B3.1 替换为 /health 与控制面路由。"""
    return {"status": "sidecar_running"}


@app.get("/health")
async def health() -> dict[str, str]:
    """B3.1 健康检查端点(蓝图 §9.4 M0 Done Criteria 1 健康检查)。"""
    return {"status": "ok"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """B3.2a WS 数据面最小端点(蓝图 §2.3)。

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


def run_sidecar(host: str = "127.0.0.1", http_port: int = HTTP_PORT) -> None:
    """启动 Sidecar:打印端口 + uvicorn 监听(蓝图 §2.2)."""
    import uvicorn

    print(f"Sidecar started: host={host} http_port={http_port}", flush=True)
    uvicorn.run(app, host=host, port=http_port)


if __name__ == "__main__":
    run_sidecar()
