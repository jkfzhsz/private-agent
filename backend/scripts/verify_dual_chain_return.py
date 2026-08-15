# -*- coding: utf-8 -*-
"""验证"发图看完后是否自动回到纯文本主模型"(轮次级回切)。

场景 A(自动模式): 轮1 发图 → 应 glm-vision; 轮2 纯文本 → 应回 text 链首选(deepseek-flash)
场景 B(手动锁定纯文本 deepseek-flash): 轮1 发图 → 应 glm-vision; 轮2 纯文本 → 应回 deepseek-flash

证据: react_events.token_usage.payload.model_id(计费记录实际 provider)。
"""
import asyncio
import json
import os
import time

import asyncpg
import httpx
import websockets

BASE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8765/ws"
IMAGE = r"D:/Private agent/pictures/微信图片_20260808101955_110_2.jpg"


async def ws_send(ws, sid: int, content: str, timeout: float = 120) -> None:
    await ws.send(json.dumps({"type": "user_message", "session_id": sid, "content": content}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
        except asyncio.TimeoutError:
            continue
        ev = json.loads(raw)
        if ev.get("type") == "turn_end":
            return


async def token_usage_providers(conn, sid: int) -> list[str]:
    rows = await conn.fetch(
        "SELECT payload FROM react_events WHERE session_id=$1 AND event_type='token_usage' ORDER BY id",
        sid,
    )
    out = []
    for r in rows:
        p = json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
        out.append(p.get("model_id"))
    return out


async def main() -> None:
    # 后端 run_sidecar 的 ensure_admin_token 优先读用户配置 backend.env
    user_env = os.path.join(os.environ.get("APPDATA", ""), "Private Agent", "backend.env")
    token = ""
    if os.path.exists(user_env):
        for line in open(user_env, encoding="utf-8"):
            if line.startswith("PA_ADMIN_TOKEN="):
                token = line.strip().split("=", 1)[1]
                break
    if not token:
        token = os.environ.get("PA_ADMIN_TOKEN", "")
    print(f"admin token 来源: {user_env}")
    conn = await asyncpg.connect(
        host="127.0.0.1", port=5432, user="postgres",
        password=os.environ["PA_DB_PASSWORD"], database="private_agent",
    )
    async with httpx.AsyncClient(timeout=30) as client:
        h = {"X-Admin-Token": token}

        # 场景 A: 自动模式
        r = await client.post(f"{BASE}/admin/sessions", headers=h, json={})
        sid_a = r.json()["id"]
        async with websockets.connect(WS, max_size=64 * 1024 * 1024) as ws:
            await ws_send(ws, sid_a, f"[用户粘贴图片: 测试图 路径: {IMAGE}]\n描述图片内容")
            await ws_send(ws, sid_a, "继续(纯文本): 1+1=?")
        prov_a = await token_usage_providers(conn, sid_a)
        print(f"[A 自动模式] token_usage model 序列: {prov_a}")

        # 场景 B: 手动锁定纯文本 deepseek-flash
        r = await client.post(f"{BASE}/admin/sessions", headers=h, json={})
        sid_b = r.json()["id"]
        r = await client.post(f"{BASE}/admin/sessions/{sid_b}/model", headers=h, json={"model_id": "deepseek-flash"})
        print(f"[B] set model: {r.json()}")
        async with websockets.connect(WS, max_size=64 * 1024 * 1024) as ws:
            await ws_send(ws, sid_b, f"[用户粘贴图片: 测试图 路径: {IMAGE}]\n描述图片内容")
            await ws_send(ws, sid_b, "继续(纯文本): 1+1=?")
        prov_b = await token_usage_providers(conn, sid_b)
        print(f"[B 锁定deepseek-flash] token_usage model 序列: {prov_b}")

        # 汇总判断
        def verdict(name, seq, expect_first, expect_second):
            ok1 = seq and seq[0] == expect_first
            ok2 = len(seq) > 1 and seq[1] == expect_second
            print(f"[{name}] 发图轮={seq[0] if seq else None} -> 期望{expect_first} | 纯文本轮={seq[1] if len(seq)>1 else None} -> 期望{expect_second} => {'PASS' if ok1 and ok2 else 'FAIL'}")
            return ok1 and ok2

        v_a = verdict("A", prov_a, "glm-vision", "deepseek-flash")
        v_b = verdict("B", prov_b, "glm-vision", "deepseek-flash")
        print("== 结论:", "场景A/B 均符合'看图后自动回纯文本主模型'" if v_a and v_b else "存在偏差,需检查")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
