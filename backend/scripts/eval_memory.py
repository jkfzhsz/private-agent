"""0.5.0 M3 B5: 记忆评估工具 —— 命中统计 + 提取回测采样。

用法:
  python scripts/eval_memory.py stats                 # 命中统计(按 scope/type/低访问)
  python scripts/eval_memory.py backtest --sample 5   # 提取回测: 抽样最近 N 条已提取记忆,
                                                      # 输出(原文一致性/场景归属)人工复核清单
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

# 允许脚本在 backend 目录下直接运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from private_agent.memory.memories_repo import MemoriesRepo  # noqa: E402

PROD_DSN = "postgresql://postgres:123123@localhost:5432/private_agent"


async def cmd_stats() -> None:
    conn = await asyncpg.connect(PROD_DSN)
    try:
        repo = MemoriesRepo(conn)
        stats = await repo.memory_stats()
        print("=== 记忆命中统计 ===")
        print(f"活跃记忆: {stats['active']}  归档: {stats['archived']}  画像: {'有' if stats['profile_exists'] else '无'}")
        print(f"按场景: {stats['by_scope'] or '(空)'}")
        print(f"按类型: {stats['by_type'] or '(空)'}")
        print(f"低访问候选(7天0命中, 可整合): {len(stats['low_access_candidates'])} 条")
        for c in stats["low_access_candidates"][:10]:
            print(f"  #{c['id']} [{c['scope']}/{c['type']}] imp={c['importance']} {c['content']}")
    finally:
        await conn.close()


async def cmd_backtest(sample: int) -> None:
    conn = await asyncpg.connect(PROD_DSN)
    try:
        repo = MemoriesRepo(conn)
        # 抽样最近提取的记忆(含来源会话, 供人工复核原文一致性 + 场景归属)
        rows = await conn.fetch(
            """
            SELECT m.id, m.type, m.scope, m.content, m.importance,
                   m.source_session_id, m.created_at
            FROM user_memories m
            WHERE m.is_active = TRUE AND m.source_session_id IS NOT NULL
            ORDER BY m.created_at DESC
            LIMIT $1
            """,
            sample,
        )
        print(f"=== 提取回测抽样({len(rows)} 条, 请人工复核) ===")
        for r in rows:
            print("-" * 60)
            print(f"#id={r['id']} type={r['type']} scope={r['scope']} imp={r['importance']}")
            print(f"来源会话: #{r['source_session_id']}  提取时间: {r['created_at']}")
            print(f"记忆内容: {r['content'][:200]}")
            print("复核项: [1] 与原文一致? [2] 场景归属正确?(global/office/data_analysis/frontend_design)")
        if not rows:
            print("(无已提取记忆, 先让系统跑几轮对话触发提取)")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="记忆评估工具(M3 B5)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats", help="命中统计")
    bt = sub.add_parser("backtest", help="提取回测抽样")
    bt.add_argument("--sample", type=int, default=5, help="抽样条数")
    args = parser.parse_args()
    if args.cmd == "stats":
        asyncio.run(cmd_stats())
    else:
        asyncio.run(cmd_backtest(args.sample))


if __name__ == "__main__":
    main()
