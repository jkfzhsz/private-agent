# B6 RAG 全栈 Spec

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

B6 修复 M2 RAG 核心链路的 3 个阻塞项:
- P0-5: RAG embedding/vector/HNSW 全栈(M2-AC-1,蓝图 §4.10/§4.11)
- P0-6: bge-small 自动切换 + 索引重建(M2-AC-3,蓝图 §4.10)
- P1-5: reranker 接入真实 bge-reranker(M2-AC-2,蓝图 §4.14)

现状: kb_chunks.embedding 为 BYTEA 占位, vector_search 恒返回 [], embedding 返回 mock 全 0 向量, reranker 跳过重排。

## In scope

### P0-5 RAG 全栈
- DB schema: kb_chunks.embedding 从 BYTEA 改为 vector(1024), 新增 HNSW 索引
- migrations: 幂等迁移(检测 pgvector 扩展 + 列存在性)
- embedding_service: 实现 _embed_worker_fn(FlagEmbedding bge-m3)
- kb_repo: 实现 vector_search(cosine 距离 + ef_search 参数)
- kb_service: 删除 BYTEA 辅助函数

### P0-6 bge-small 切换
- select_model_by_memory(): 可用内存 <6GB → bge-small-zh-v1.5
- 维度兼容: bge-m3(1024) vs bge-small(384) 不兼容, 切换需重建索引
- query LRU 缓存(maxsize=512)

### P1-5 reranker
- 实现 _rerank_worker_fn(FlagEmbedding bge-reranker-v2-m3)
- worker_pool=None 时降级跳过, 不阻断

## Out of scope

- FlagEmbedding 模型下载(首次启动自动, 约 2GB+1GB)
- 云端 embedding 降级路径(V2)
- 索引重建自动化调度(V2)

## Acceptance criteria

- AC-1: kb_chunks.embedding 为 vector(1024) 类型, 可 INSERT
- AC-2: HNSW 索引存在(pg_indexes 可查)
- AC-3: _embed_worker_fn 返回 1024 维向量(需 FlagEmbedding)
- AC-4: vector_search 返回 cosine similarity 排序的 top-k
- AC-5: vector_search 支持 scenario 过滤
- AC-6: vector_search 空表返回空列表
- AC-7: select_model_by_memory 内存 <6GB 返回 light, >=6GB 返回 default
- AC-8: _rerank_worker_fn 返回归一化分数列表
- AC-9: reranker worker_pool=None 降级不阻断
- AC-10: 全量 pytest 通过(743 现有 + B6 新增)