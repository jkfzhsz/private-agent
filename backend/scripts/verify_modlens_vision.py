# -*- coding: utf-8 -*-
"""真实链路验证: WS 发图 → ReactLoop 双链 → vision_chain(glm-vision) → GLM-4.6V-Flash。

验证路径 = 用户真实触发路径(WS user_message → run_turn 装配), 非 test/直连。
"""
import asyncio
import json
import os
import sys
import time

import asyncpg
import httpx
import websockets

BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/ws"
IMAGE = r"D:/Private agent/pictures/微信图片_20260808101955_110_2.jpg"


async def create_session(client: httpx.AsyncClient, token: str) -> int:
    r = await client.post(
        f"{BASE}/admin/sessions", headers={"X-Admin-Token": token}, json={}
    )
    r.raise_for_status()
    return int(r.json()["id"])


async def main() -> None:
    token = os.environ["PA_ADMIN_TOKEN"]
    async with httpx.AsyncClient(timeout=30) as client:
        sid = await create_session(client, token)
    print(f"session_id={sid}")

    content = (
        f"[用户粘贴图片: 银河插画 路径: {IMAGE}]\n"
        "请用中文描述这张图片的内容（画面元素、色彩、风格）。"
    )
    events: list[dict] = []
    async with websockets.connect(WS, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "user_message", "session_id": sid, "content": content}))
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue
            ev = json.loads(raw)
            events.append(ev)
            t = ev.get("type")
            if t == "delta":
                pass
            elif t in ("turn_complete", "turn_failed", "error", "turn_end"):
                print(f"terminal event: {t}")
                break
    # 汇总事件类型
    from collections import Counter
    print("event types:", dict(Counter(e.get("type") for e in events)))
    # 打印非 delta 的关键事件
    for e in events:
        t = e.get("type")
        if t not in ("delta", "reasoning_delta"):
            print(f"EVENT {t}: {json.dumps(e, ensure_ascii=False)[:400]}")

    # DB 校验: 该 session 最新 assistant 消息
    conn = await asyncpg.connect(
        host="127.0.0.1", port=5432, user="postgres",
        password=os.environ["PA_DB_PASSWORD"], database="private_agent",
    )
    try:
        rows = await conn.fetch(
            "SELECT id, role, substr(content, 1, 200) AS content "
            "FROM messages WHERE session_id=$1 ORDER BY id DESC LIMIT 4",
            sid,
        )
        for r in rows:
            print(f"DB msg id={r['id']} role={r['role']} content={r['content'][:120]!r}")
    finally:
        await conn.close()
    print("RESULT provider chain:", [e.get("provider") for e in events if e.get("type") == "provider"])


if __name__ == "__main__":
    asyncio.run(main())
