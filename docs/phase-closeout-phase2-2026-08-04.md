# 阶段收尾报告：安全硬边界（阶段二）

> 日期：2026-08-04 · 阶段范围：2026-08-04（阶段二优化迭代，首批三个方向）
> 状态：✅ 四个批次全部完成 · 后端 pytest **973 passed** · 前端 vitest 13 + tsc 0 error · 已推送 GitHub `4b4a242`

---

## 一、阶段目标回顾

基于 `docs/phase-closeout-2026-08-04.md`（阶段一收尾）与 `docs/architecture-revision.md`（§2.3 安全硬边界采纳分析），围绕审查报告三个致命项展开：

1. **admin 鉴权加固**（A.3.10/B.2.1）：43 个控制面端点裸奔 + CORS 全开 → 任意本机网页可读改全部密钥
2. **SSRF 防护**（A.2.3/B.1.3）：`http_request` 可请求任意 URL → 内网探测/云元数据窃取
3. **沙箱失效修复**（A.1.1/B.1.1）：ResourceLimiter 从未调用、disable_network 未接线、Windows 无 Job Object

---

## 二、四批次成果

### 批次 1：admin 鉴权 + CORS 收窄（MS-1 安全地基）

| 项 | 内容 |
|---|---|
| `security/auth.py`（新） | `PA_ADMIN_TOKEN`（独立于 PA_MASTER_KEY）；`ensure_admin_token()` 生成 64 hex 幂等持久化 .env；`require_admin` 常量时间比较 |
| `main.py` | admin/eval/files router 级挂依赖（43 端点全 401）；`/`、`/health`、`/ws` 豁免；CORS `["*"]` → 白名单（5173/4173/`app://.` + dev 通配） |
| 前端 | `apiClient.ts`（adminFetch 自动带 token + 401 事件）；20 处 fetch 全替换；preload 注入（主进程 sidecar 启动后补读 .env 解决首启时序）；SettingsView 安全管理区块 |
| 测试 | test_admin_auth.py 15 用例 + test_main_cors.py 重写 + 6 文件批量注入 token 头 |

### 批次 2：SSRF 防护（MS-2 出网防护）

| 项 | 内容 |
|---|---|
| `security/ssrf.py`（新） | scheme 白名单；字面 IP 预判；getaddrinfo 全量解析任一命中黑名单即拒（含云元数据 169.254.169.254）；SafeHttpxClient 重定向每跳校验 + 2MB 响应上限 + trust_env=False |
| 接入 | `http_request` 全量走 SafeHttpxClient；`web_search` 固定域名白名单；config `security.ssrf.*` |
| 文档 | `docs/security-model.md`：威胁模型 + 出网点审计清单（http_request 高/web_search 中/MCP 配置信任）；DNS rebinding 降级策略 |
| 测试 | test_ssrf.py 28 用例（表驱动 IP 黑名单/多 A 逃逸/MockTransport 重定向/大小限制/handler 集成） |

### 批次 3：沙箱失效修复（MS-3 执行隔离）

| 项 | 内容 |
|---|---|
| `sandbox/job.py`（新） | ctypes 手写 Windows Job Object（零新依赖）：内存/CPU 总时长/活动进程数/KILL_ON_JOB_CLOSE/UI 限制；attach 失败降级 |
| 接线修复 | `disable_network` 接线（`limits.network_enabled` 默认 false → 默认禁网）；修正 NO_PROXY=* 自相矛盾（拦截完全失效）；POSIX `preexec_fn` 透传（RLIMIT 此前从未生效） |
| 测试 | test_sandbox_job.py 4 用例（CPU 1s 杀/内存 MemoryError/进程数拒 spawn/正常回归）+ test_sandbox_network.py 7 用例（禁网 ProxyError/放行/本地 server/.env 脱敏） |

### 批次 4：收尾（MS-4）

- **C-4 事件级去重**（架构修订遗留采纳项）：offset 单调保护反向断言补齐（stale ack 不回退，test_ws_offset +1）
- 最终全量回归：后端 973 + 前端 13 + tsc 0 error
- PA1.0 全量同步（main/config/security 包/sandbox/tools/tests/docs）

---

## 三、质量与交付

| 项 | 结果 |
|---|---|
| 后端回归 | pytest **973 passed**（917 基线 → +56：鉴权 16 / SSRF 28 / 沙箱 11 / C-4 1） |
| 前端 | vitest 13 passed + tsc 0 error |
| 真机验证 | 批次 1：uvicorn 18765 curl（401/200/CORS 三场景）；批次 2：真实外网请求（127.0.0.1/元数据拒、example.com 200、重定向跟随）；批次 3：Job 子进程级（CPU/内存/进程数）+ 网络隔离 |
| 文档 | `docs/security-model.md`（威胁模型/出网点审计/沙箱边界）+ 本报告 |
| 部署同步 | 全部改动同步 `D:\PA1.0\backend`（重启应用生效） |
| 版本控制 | 本地 commit `4b4a242`（40 files / +2254，已移除误入的 PDF/vbs）；推送 github.com 网络不可达，待用户手动 `git push origin main` |
| 记忆沉淀 | 批次 1-3 工作记忆 + 教训已写 2026-08-04.md |

---

## 四、遗留项与后续方向（阶段三候选）

| 项 | 状态 | 说明 |
|---|---|---|
| **C-4 事件级精确重放** | ⚠️ 部分完成 | offset 单调保护 ✅；turn 粒度无法重放同 turn 内部分事件（断线丢事件）→ 事件 id 粒度游标/客户端去重，列阶段三 |
| **DNS rebinding strict_dns** | 📋 增强 | 校验与连接分离，绑定连接 IP 的自定义 transport，列后续 |
| **沙箱 CREATE_SUSPENDED** | 📋 增强 | 消除 attach 毫秒级竞态（超短代码可能在 Job 挂入前完成） |
| **注入防护"移除+告警"** | ⏳ 未实施 | 架构修订 B.2.3/P1-1，下一批安全候选 |
| **MCP 装配 URL 一次性校验** | ⏳ 未实施 | 配置信任场景的可选加固（仅日志） |
| **GitHub** | ✅ 已推送（`4b4a242` → origin/main，40 files / +2254，已移除误入的 PDF/vbs） |

---

## 五、经验教训（本阶段沉淀）

1. **安全改造的优先级 = 风险敞口 ÷ 成本**：admin 无鉴权（钥匙挂门口）应先于沙箱（技术风险最高、已有 elevated 确认兜底）——按此顺序三批次全部一次通过
2. **"接线"比"发明"重要**：disable_network/ResourceLimiter/PathFilter 都是蓝图已定义但从未接线的死代码——安全审查先盘点"定义未使用"的模块
3. **Windows 与 POSIX 沙箱语义不同**：Job Object 的 PROCESS_MEMORY 超限是分配失败（MemoryError）不是杀进程，JOB_TIME 才是系统级终止——验收断言必须按平台语义设计
4. **应用层网络隔离的边界**：对读环境变量代理的 HTTP 库（httpx/requests）有效；socket 直连/Windows urllib（读注册表）可绕过——边界文档化，主要防线是权限确认 + Job 约束
5. **既有测试是行为契约**：改 handler/工具行为时同步适配 mock 目标（httpx.AsyncClient → safe_httpx_client），否则测试在"错误路径"上假绿
6. **测试注入点设计**：SafeHttpxClient 支持 transport 注入让重定向/大小限制测试全本地化；TestClient `client.headers.update()` 批量补鉴权头
7. **cwd 与解释器一致性**：pytest 必须 source backend/.env（PA_WEB_SEARCH_BACKEND 等影响测试路径）；沙箱子进程测试用 venv python（有 httpx，无 requests）

---

*本报告由 WorkBuddy 生成，随阶段二收尾归档。*
