# -*- coding: utf-8 -*-
"""锁定纯文本模型场景: 发图轮完整事件流诊断。

判定依据: turn=1 是否真正描述图片(云层/银河等视觉细节) → vision 链生效;
turn=2 纯文本回复 → 是否回到锁定模型(轮次级回切)。
"""
import asyncio
import json
import os
import time

import httpx
import websockets

BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/ws"
IMAGE = r"D:/Private agent/pictures/微信图片_20260808101955_110_2.jpg"


async def main() -> None:
    user_env = os.path.join(os.environ.get("APPDATA", ""), "Private Agent", "backend.env")
    token = ""
    for line in open(user_env, encoding="utf-8"):
        if line.startswith("PA_ADMIN_TOKEN="):
            token = line.strip().split("=", 1)[1]
            break
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/admin/sessions", headers={"X-Admin-Token": token}, json={})
        sid = r.json()["id"]
        r = await c.post(f"{BASE}/admin/sessions/{sid}/model", headers={"X-Admin-Token": token},
                         json={"model_id": "deepseek-flash"})
        print(f"session={sid} lock={r.json()['model_id']}")

    def dump(events: list[dict], tag: str) -> None:
        print(f"---- {tag} ----")
        for e in events:
            t = e.get("event_type")
            p = e.get("payload", {})
            if t == "thinking":
                pass  # 数量多, 跳过明细
            elif t == "delta":
                pass
            elif t in ("error", "final", "tool_call", "tool_result"):
                print(f"[{t}] {json.dumps(p, ensure_ascii=False)[:220]}")
        think = "".join(e.get("payload", {}).get("reasoning", "") for e in events if e.get("event_type") == "thinking")
        deltas = "".join(e.get("payload", {}).get("content", "") for e in events if e.get("event_type") == "delta")
        print(f"[thinking]{think[:150]!r}")
        print(f"[delta]{deltas[:200]!r}")

    async def run_turn(ws, content: str, tag: str) -> None:
        events: list[dict] = []
        await ws.send(json.dumps({"type": "user_message", "session_id": sid, "content": content}))
        deadline = time.time() + 150
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue
            ev = json.loads(raw)
            if ev.get("type") != "react_event":
                continue
            events.append(ev)
            if ev.get("event_type") == "turn_end":
                break
        dump(events, tag)

    async with websockets.connect(WS, max_size=64 * 1024 * 1024) as ws:
        await run_turn(ws, f"[用户粘贴图片: 测试图 路径: {IMAGE}]\n描述图片内容", "turn1 发图")
        await run_turn(ws, "继续(纯文本): 1+1=?", "turn2 纯文本")


if __name__ == "__main__":
    asyncio.run(main())
