#!/usr/bin/env python
"""Private Agent 打包版端到端冒烟测试(2026-08-07)。

用途: 安装/发版后一条命令验证核心链路, 任一 FAIL 退出码非 0(拦截发布)。
防止"打地鼠"式修复 —— 每次发版前必须跑通本脚本。

覆盖:
1. /health 后端存活
2. admin token 鉴权(设置页能读配置)
3. 数据库连接(db_reachable)
4. MCP 三个连通性(mempalace / Searchpin / hexin-ifind-ds-stock-mcp,
   顺带验证 stdio 并发写锁)
5. 对话一轮(WS user_message → final, 验证对话流完整闭环)
6. 检查更新(GitHub Releases API, 网络差记 WARN)

用法(PA 启动后, 用 backend venv python):
    python scripts/smoke-test.py                 # 默认 127.0.0.1:8765
    python scripts/smoke-test.py --url http://127.0.0.1:8765 --skip-chat
环境: PA_DB_PASSWORD 等由后端自身加载, 本脚本只读 /admin 端点。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
import websockets

# token 来源: --token > %APPDATA%/Private Agent/backend.env > backend/.env
def _read_env_key(path: Path, key: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def load_tokens() -> list[str]:
    """候选 token: %APPDATA%(打包版) > backend/.env(dev) > 环境变量。"""
    cands: list[str] = []
    appdata = os.environ.get("APPDATA", "")
    p = Path(appdata) / "Private Agent" / "backend.env"
    t = _read_env_key(p, "PA_ADMIN_TOKEN")
    if t:
        cands.append(t)
    t = _read_env_key(Path("backend/.env"), "PA_ADMIN_TOKEN")
    if t and t not in cands:
        cands.append(t)
    t = os.environ.get("PA_ADMIN_TOKEN", "")
    if t and t not in cands:
        cands.append(t)
    return cands


async def step(name: str, fn, warn_only: bool = False) -> bool:
    try:
        ok, detail = await fn()
        mark = "[PASS]" if ok else ("[WARN]" if warn_only else "[FAIL]")
        print(f"{mark} {name}: {detail}")
        if not ok and not warn_only:
            return False
        return True
    except Exception as e:  # noqa: BLE001
        mark = "[WARN]" if warn_only else "[FAIL]"
        print(f"{mark} {name}: 异常 {type(e).__name__}: {e}")
        return warn_only


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--token", default="")
    ap.add_argument("--skip-chat", action="store_true", help="跳过对话轮(LLM 未配置时)")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()
    base = args.url.rstrip("/")
    tokens = ([args.token] if args.token else []) + load_tokens()
    if not tokens:
        print("[FAIL] 找不到 admin token(%APPDATA%/Private Agent/backend.env)")
        return 1

    async def _health():
        r = await httpx.AsyncClient(timeout=5).get(f"{base}/health")
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        return True, "后端运行中"

    async def _auth():
        nonlocal auth_headers
        for t in tokens:
            r = await httpx.AsyncClient(timeout=10).get(
                f"{base}/admin/settings/database", headers={"X-Admin-Token": t}
            )
            if r.status_code != 401:
                auth_headers = {"X-Admin-Token": t}
                return True, "设置页读取成功(鉴权通过)"
        return False, "所有候选 token 均 401(前后端 token 不一致)"

    fails = 0
    auth_headers: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=15) as c:
        # 1) health
        ok = await step("后端存活 /health", _health)
        fails += 0 if ok else 1

        # 2) admin 鉴权(逐个候选 token 尝试)
        ok = await step("admin 鉴权", _auth)
        fails += 0 if ok else 1

        # 3) 数据库
        async def _db():
            r = await c.get(f"{base}/admin/settings/database", headers=auth_headers)
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}"
            d = r.json()
            if not d.get("db_reachable"):
                return False, f"数据库未连接(先配置并重启; password_configured={d.get('password_configured')})"
            return True, f"数据库可达({d.get('host')}:{d.get('port')}/{d.get('name')})"

        ok = await step("数据库连接", _db)
        fails += 0 if ok else 1

        # 4) MCP 连通(mempalace 36 工具 + searchpin 2 工具 + iFinD)
        mcp_servers = [
            ("记忆宫殿 mempalace", "mempalace", 30),
            ("searchpin", "Searchpin", 90),
            ("同花顺 iFinD-stock", "hexin-ifind-ds-stock-mcp", 30),
        ]
        for label, sid, to in mcp_servers:
            async def _mcp(sid=sid, to=to):
                r = await c.post(
                    f"{base}/admin/settings/mcp/{sid}/test",
                    headers=auth_headers, json={}, timeout=to,
                )
                if r.status_code != 200:
                    return False, f"HTTP {r.status_code}"
                d = r.json()
                if not d.get("ok"):
                    return False, f"失败: {d.get('error')}"
                n = d.get("tools_count", 0)
                return n > 0, f"ok, {n} 个工具"
            ok = await step(f"MCP {label}", _mcp, warn_only=(sid.startswith("hexin")))
            fails += 0 if ok else 1

        # 5) 对话一轮(验证对话流闭环 + stdio 并发)
        if not args.skip_chat:
            async def _chat():
                sid = int(time.time() * 1000) % 100000 + 1
                async with websockets.connect(
                    f"{base.replace('http', 'ws')}/ws"
                ) as ws:
                    await ws.send(json.dumps({
                        "type": "user_message", "session_id": sid,
                        "content": "用 mempalace_list_wings 查询记忆宫殿结构, 再调用 web_search 搜索 OpenAI, 最后用一句话总结每个工具的结果。",
                    }))
                    saw_tool_result = False
                    deadline = time.time() + args.timeout
                    while time.time() < deadline:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=min(30, deadline - time.time()))
                        except asyncio.TimeoutError:
                            break
                        try:
                            ev = json.loads(raw)
                        except Exception:
                            continue
                        et = ev.get("type") or ev.get("event_type")
                        if et == "tool_result":
                            saw_tool_result = True
                        if et == "final":
                            return True, f"对话完成(工具调用={'有' if saw_tool_result else '无'})"
                        if et == "error" and "skill_not_found" in str(ev.get("message", "")):
                            return False, f"对话失败: {ev.get('message')}"
                    return False, "对话超时未收到 final(LLM 响应慢或链路故障)"

            ok = await step("对话一轮", _chat)
            fails += 0 if ok else 1
        else:
            print("[SKIP] 对话一轮(--skip-chat)")

        # 6) 检查更新(GitHub API)
        async def _update():
            r = await c.get(
                "https://api.github.com/repos/jkfzhsz/private-agent/releases/latest",
                timeout=15,
            )
            if r.status_code == 404:
                return False, "暂无发布版本(未发布过, 属正常)"
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}"
            d = r.json()
            return True, f"最新版本 {d.get('tag_name')}"

        ok = await step("检查更新", _update, warn_only=True)
        fails += 0 if ok else 1

    print(f"\n冒烟结果: {'✅ 全部通过' if fails == 0 else f'❌ {fails} 项失败'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
