"""子瞻(office)知识库批量导入 —— 记忆宫殿三库 → PA 原生 KB(PostgreSQL)。

来源(2026-08-09 蒋先生指定, 均为 Karpathy wiki 形态 md 编译产物):
- D:/wiki-knowledge      商业银行公司金融/公司信贷(wiki/*.md, 18 篇)
- D:/finance-five-articles  金融五篇大文章 + 中央金融工作(wiki/*.md, 10 篇)
- D:/icbc-wiki          工商银行(wiki/*.md, 9 篇)

目标:
- scenario=office(子瞻), 技能 knowledge_base 已启用 auto_retrieve=true
  → 灌入后子瞻会话自动检索 + search_knowledge 可查。
- 增量导入: 不 --reset; 按 filename+scenario 查重, 已存在跳过。

用法: cd backend && python scripts/import_kb_dirs.py
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path

import asyncpg

from private_agent.knowledge.factory import build_kb_service
from private_agent.knowledge.kb_repo import KnowledgeBaseRepo

SOURCES: list[dict] = [
    {"dir": "D:/wiki-knowledge", "scenario": "office", "prefix": "wiki-knowledge"},
    {"dir": "D:/finance-five-articles", "scenario": "office", "prefix": "finance-five-articles"},
    {"dir": "D:/icbc-wiki", "scenario": "office", "prefix": "icbc-wiki"},
    # 2026-08-09 19:40: 白圭(data_analysis)两库 —— 记忆宫殿新增
    {"dir": "D:/family-wealth-wiki", "scenario": "data_analysis", "prefix": "family-wealth-wiki"},
    {"dir": "D:/securities-investing-wiki", "scenario": "data_analysis", "prefix": "securities-investing-wiki"},
]


async def main() -> None:
    conn = await asyncpg.connect(
        host=os.environ.get("PA_DB_HOST", "localhost"),
        port=int(os.environ.get("PA_DB_PORT", "5432")),
        user=os.environ.get("PA_DB_USER", "postgres"),
        password=os.environ["PA_DB_PASSWORD"],
        database=os.environ.get("PA_DB_NAME", "private_agent"),
    )
    try:
        svc = build_kb_service(conn)
        repo = KnowledgeBaseRepo(conn)
        total_docs = total_chunks = skipped = 0
        for src in SOURCES:
            wiki_dir = Path(src["dir"]) / "wiki"
            files = sorted(wiki_dir.glob("*.md"))
            print(f"\n== {src['prefix']}: {len(files)} 篇 ==")
            for f in files:
                content = f.read_text(encoding="utf-8", errors="replace")
                filename = f"{src['prefix']}/{f.name}"
                # 幂等: 已存在且 hash 相同 → 跳过
                row = await conn.fetchrow(
                    "SELECT hash FROM kb_documents WHERE source=$1 AND scenario=$2",
                    filename, src["scenario"],
                )
                h = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if row and row["hash"] == h:
                    skipped += 1
                    print(f"  skip(已存在): {filename}")
                    continue
                doc_id, chunks = await svc.process_document(
                    content=content, filename=filename, scenario=src["scenario"],
                    skip_dedup=True,
                )
                total_docs += 1
                total_chunks += len(chunks)
                print(f"  OK: {filename} doc_id={doc_id} chunks={len(chunks)}")
        print(f"\n完成: 新增 {total_docs} 篇 / {total_chunks} chunks, 跳过 {skipped} 篇")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
