"""修复 config_runtime tools.mcp.servers 中 codegraph 条目路径（正斜杠，防转义损坏）。"""
import asyncio, json, os
import asyncpg

FIX_ENTRY = {
    "id": "codegraph",
    "env": {"CODEGRAPH_PARSE_WORKERS": "1"},
    "url": "",
    "args": [
        "D:/github/codegraph-win32-x64/lib/dist/bin/codegraph.js",
        "serve",
        "--mcp",
    ],
    "type": "stdio",
    "command": "D:/github/codegraph-win32-x64/node.exe",
    "enabled": True,
    "timeout_sec": 60.0,
    "protocol_version": "auto",
}


async def main():
    conn = await asyncpg.connect(
        host="127.0.0.1", port=5432, user="postgres",
        password=os.environ["PA_DB_PASSWORD"], database="private_agent",
    )
    row = await conn.fetchrow(
        "SELECT value FROM config_runtime WHERE key = 'tools.mcp.servers'"
    )
    servers = json.loads(row["value"])
    idx = next(i for i, s in enumerate(servers) if s.get("id") == "codegraph")
    servers[idx] = FIX_ENTRY
    await conn.execute(
        "UPDATE config_runtime SET value = $1::jsonb WHERE key = 'tools.mcp.servers'",
        json.dumps(servers, ensure_ascii=False),
    )
    # 回读验证
    row2 = await conn.fetchrow(
        "SELECT value FROM config_runtime WHERE key = 'tools.mcp.servers'"
    )
    servers2 = json.loads(row2["value"])
    cg = next(s for s in servers2 if s.get("id") == "codegraph")
    print("FIXED:", json.dumps(cg, ensure_ascii=False, indent=2))
    await conn.close()


asyncio.run(main())
