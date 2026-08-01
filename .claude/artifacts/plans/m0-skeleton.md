# M0 基础骨架层 Implementation Plan (m0-skeleton)

> Status: RETROACTIVE (回溯补写,基于 commit 152780d → f1bbaeb 实际实施步骤)
> Author: zongxin
> Last updated: 2026-08-01
> Source: 蓝图 §9.4 M0 + §9.6 step 1-6 + commit 链回溯

## 实施步骤(回溯)

### Step 1-3: 骨架 + Sidecar + HTTP + WS(commit 333f281,10 tests green)
1. `backend/private_agent/` 包结构 + `__init__.py`
2. `main.py` FastAPI app + `GET /` + `GET /health`
3. `WS /ws` 端点(ping/pong)
4. `frontend/` Electron 结构(main/preload/renderer/static)
5. `pyproject.toml` 基础依赖(fastapi/uvicorn/asyncpg/pyyaml/pydantic)
6. `test_health.py` + `test_ws.py` + `test_package.py` + `test_structure.py`

### Step 4-6: DB Schema + Config + Observability(commit b7f9ed2,29 tests green)
1. `storage/schema.sql` 13 张表 + 索引 + CHECK 约束
2. `storage/migrations.py` migrate_all(conn)
3. `storage/db.py` build_dsn + connect + create_pool + get_pool + close_pool
4. `config/loader.py` load_config()
5. `config/secrets.py` AES-256-GCM encrypt/decrypt
6. `config/config.yaml` 全局配置骨架
7. `observability/logging.py` setup_logger
8. `storage/react_events.py` 事件类型枚举
9. 测试:test_migrations / test_db_pool / test_config / test_secrets / test_config_runtime / test_logging / test_react_events

### Step P1/P2 修复(commit 42a2029,code-review)
1. main.py 端口从 load_config() 读取(禁止硬编码)
2. 全模块 wire setup_logger 替代 print
3. 删除未使用导入

### Step B4.2 + B4.3 + B3.2b 闭环(commit f1bbaeb,60 tests green)
1. `storage/disk_alert.py` evaluate_disk_alert_level + get_disk_status + get_pg_data_dir_size
2. `storage/ttl_cleanup.py` run_ttl_cleanup
3. `storage/ws_offset.py` build_replay_messages + handle_ack
4. 测试:test_disk_alert / test_disk_alert_status / test_ttl_cleanup / test_ws_offset / test_ws_ack / test_ws_offset_ack

## 验证

- M0 最终测试:60 tests green(commit f1bbaeb)
- 蓝图 §9.4 M0 Done Criteria 5 条全过
- M1 spec Background 确认:"M0 已完成(commit f1bbaeb,60 tests green)"

## 提交链

```
152780d chore: bootstrap M0 repo with blueprint and approved plan
333f281 feat(m0): steps 1-3 skeleton + sidecar + http + ws (10 tests green)
b7f9ed2 feat(m0): steps 4-6 db schema + config layer + observability (29 tests green)
42a2029 fix(m0): wire setup_logger + config-driven port, drop unused imports
f1bbaeb feat(m0): complete B4.2 disk alert + B4.3 TTL cleanup + B3.2b ws_offset replay (60 tests green)
```

## Notes

- 本 plan 为回溯文档,不参与 RALPLAN-DR 评审流程
- 实际开发顺序与蓝图 §9.6 step 1-6 一致
- M0 闭环缺口(磁盘告警 HTTP/WS、TTL 调度、ws_offset ACK)由 M1 spec 闭环
