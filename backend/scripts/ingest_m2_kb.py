"""0.5.1: KB 语料落地脚本 —— 白圭投资类 + 清和设计系统/健康类入库。

来源:
- 白圭(data_analysis): backend/skills/data_analysis/kb_assets/investment_framework.md
- 清和(frontend_design): FlowSpace-Design-System.md(项目根)
  + backend/skills/frontend_design/kb_assets/health_design_guide.md
  + backend/skills/frontend_design/kb_assets/taste_design_guide.md
  (2026-08-20: Taste Skill 裁剪适配版, 强化清和前端审美)
- 腾讯控股研报: 本地未找到, 用户提供后可补充入库

0.5.1 变更:
- 装配走 factory.build_kb_service(注入真实 embedding worker);
- `--reset` 参数: 清空 kb_chunks + kb_documents 后全量重灌
  (0.5.1 迁移 mock 全 0 → 真实向量场景; 已有真实业务数据时勿用, 走增量 update_document);
- DSN 从环境变量 PA_DB_* 读取(后端 .env 已加载时自动生效), 兼容旧硬编码。

幂等: 按 filename+scenario 查重, 已存在且 hash 相同则跳过。
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

ASSETS: list[dict] = [
    {
        "scenario": "data_analysis",
        "filename": "白圭_投资分析框架与指标口径.md",
        "path": Path("skills/data_analysis/kb_assets/investment_framework.md"),
    },
    {
        "scenario": "frontend_design",
        "filename": "FlowSpace-Design-System.md",
        "path": Path("../FlowSpace-Design-System.md"),
    },
    {
        "scenario": "frontend_design",
        "filename": "清和_健康管理常识与设计规范.md",
        "path": Path("skills/frontend_design/kb_assets/health_design_guide.md"),
    },
    {
        "scenario": "frontend_design",
        "filename": "清和_Taste设计品味指南.md",
        "path": Path("skills/frontend_design/kb_assets/taste_design_guide.md"),
    },
]


def _dsn() -> str:
    """从 PA_DB_* 环境变量构建 DSN(backend/.env 已由启动方加载)。"""
    pw = os.environ.get("PA_DB_PASSWORD", "123123")
    host = os.environ.get("PA_DB_HOST", "localhost")
    port = os.environ.get("PA_DB_PORT", "5432")
    user = os.environ.get("PA_DB_USER", "postgres")
    name = os.environ.get("PA_DB_NAME", "private_agent")
    return f"postgresql://{user}:{pw}@{host}:{port}/{name}"


async def main() -> None:
    reset = "--reset" in sys.argv
    conn = await asyncpg.connect(_dsn())
    try:
        if reset:
            # 0.5.1: 清空后全量重灌(mock 全 0 → 真实向量场景专用;
            # 真实业务数据请勿使用, 走 update_document 增量)
            async with conn.transaction():
                await conn.execute("DELETE FROM kb_chunks")
                await conn.execute("DELETE FROM kb_documents")
            print("RESET: kb_chunks + kb_documents cleared")

        svc = build_kb_service(conn)
        repo = KnowledgeBaseRepo(conn)
        for asset in ASSETS:
            p = asset["path"]
            if not p.exists():
                print(f"SKIP(不存在): {p}")
                continue
            content = p.read_text(encoding="utf-8")
            existing = await repo.get_document_by_source(asset["filename"])
            if existing is not None and existing.hash:
                content_hash = hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest()
                if existing.hash == content_hash:
                    print(
                        f"SKIP(已存在且未变): {asset['filename']} "
                        f"[{asset['scenario']}]"
                    )
                    continue
                print(
                    f"UPDATE(内容变化): {asset['filename']} "
                    f"[{asset['scenario']}]"
                )
                doc_id, chunks = await svc.update_document(
                    existing.id, content, asset["filename"], asset["scenario"]
                )
                print(f"OK: doc_id={doc_id} chunks={len(chunks)}")
                continue
            doc_id, chunks = await svc.process_document(
                content=content,
                filename=asset["filename"],
                scenario=asset["scenario"],
            )
            print(
                f"OK: {asset['filename']} [{asset['scenario']}] "
                f"doc_id={doc_id} chunks={len(chunks)}"
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
