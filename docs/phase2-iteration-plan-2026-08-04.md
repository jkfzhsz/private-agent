# 阶段二迭代计划：安全硬边界

> 项目：私人智能体（Private Agent）· 后端 `backend/`
> 日期：2026-08-04 · 依据：`docs/phase-closeout-2026-08-04.md`（阶段一收尾）+ `docs/architecture-revision.md`（§2.3 安全硬边界，采纳分析）
> 首批候选方向：**沙箱失效修复 · admin 鉴权加固 · SSRF 防护**
> 状态：🟢 **批次 1（admin 鉴权 + CORS）✅ 完成（933 + 13）**；🟢 **批次 2（SSRF）✅ 完成（28 用例）**；🟢 **批次 3（沙箱）✅ 完成（11 用例 + 真机验证）**；批次 4（收尾：C-4 + 回归）待实施

---

## 一、阶段一遗留问题与约束条件（提取自收尾报告）

### 1.1 遗留问题清单

| # | 遗留项 | 来源 | 归属阶段二 |
|---|---|---|---|
| S-1 | **沙箱失效**：Windows Job Object 未实现、网络拦截未接线、资源限制器未生效 | AUDIT-REPORT A.1.1 / B.1.1（架构修订 §2.3） | ✅ 本计划方向三 |
| S-2 | **admin 无鉴权 + CORS 全开**：43 个管理端点（admin 34 + eval 8 + files 1）无任何认证 | A.3.10 / B.2.1 | ✅ 本计划方向一 |
| S-3 | **SSRF 无防护**：`http_request` 工具可请求任意 URL（含内网/云元数据） | A.2.3 / B.1.3 | ✅ 本计划方向二 |
| S-4 | 注入防护"移除+告警"（B.2.3 / P1-1） | 架构修订 §2.3 | ⏳ 下一批候选（本阶段不实施） |
| S-5 | C-4 事件级去重（offset 单调保护之外的推送去重） | 架构修订 §12 未打勾 | ✅ 并入本阶段收尾批次 |

### 1.2 阶段一已确立的结论（本计划必须遵守）

1. **"审查先行，重构后置"**：安全改造同样先盘点、后动手；每批次独立提交 + 全量回归（当前基线 **pytest 917 passed / vitest 13 passed**）
2. **安全硬伤修复优先于功能**：T-1 路径服务端强制已验证该模式——**服务端强制校验，不信任 LLM 参数**
3. **Windows 普通用户环境**：无管理员权限、PostgreSQL 非服务启动、Electron 带 `--no-sandbox`——安全加固**不能依赖管理员权限**（排除 WFP 驱动级方案）
4. **浏览器 dev + Electron 双运行模式**：CORS 与鉴权改动必须同时兼容 vite dev（5173）与 Electron 生产（file:///app:// origin）
5. **配置驱动 + 开关隔离**：所有安全策略可配置、默认安全、可回退（沿用阶段一惯例）
6. **真实平台验证**：沙箱类改造必须真机验证（Windows 行为与 POSIX 差异大），不能只靠单元测试

### 1.3 代码现状核实（2026-08-04 走查）

| 方向 | 现状证据 |
|---|---|
| 沙箱 | `ResourceLimiter` 在 `SandboxService.__init__` 实例化但**从未被调用**（`service.py` 无 `get_preexec_fn` 传递）；`disable_network()` 定义于 `resource_limiter.py` 但 **service.py 未接线**（`config.yaml` 已有 `limits.network_enabled: false`）；`PathFilter` 定义未使用；`executor.py` 直接 `create_subprocess_exec`，Windows 无 Job Object / 进程树隔离；`code_execution` 工具 `safety_level="elevated"` 已有 WS 权限确认链路（60s 超时拒绝）——**这是沙箱当前唯一的实际防线** |
| admin | `admin.py:25` `APIRouter(prefix="/admin")` 34 端点 + `eval.py` 8 端点 + `files.py` 1 端点，全部无 `Depends` 鉴权；`main.py:28` `allow_origins=["*"]`；`PA_MASTER_KEY` 已存在但仅用于 AES 加密（未用于请求鉴权）；端点含 provider API key 配置、MCP Bearer token 配置、沙箱配置、wallpaper——**任何可访问 8765 的本机网页/进程可读改全部密钥** |
| SSRF | `tools/builtins/http_request.py`：任意 URL GET/POST，`httpx.AsyncClient` 无校验、无大小限制（`follow_redirects` 默认 False，暂未暴露重定向问题）；`web_search.py` URL 硬编码（duckduckgo/bocha/bing 固定域名，低风险）；MCP client URL 由配置决定（中风险，配置信任）；venv **无 pywin32/psutil**（Job Object 需 ctypes 手写或新增依赖） |

---

## 二、三方向评估矩阵

评分口径：**风险敞口**（当前危害 × 被利用概率）、**影响范围**（波及代码面/运行模式）、**实现成本**（含测试与验证）、**技术风险**（平台限制/兼容性）。

| 方向 | 风险敞口 | 影响范围 | 实现成本 | 技术风险 | 优先级建议 |
|---|---|---|---|---|---|
| **方向一：admin 鉴权 + CORS 收窄** | 🔴 **最大**：任意本机网页经 CORS `*` 可读改全部密钥（provider key / MCP token / 沙箱配置） | 中：main.py + 43 端点 + 前端 api client + Electron preload + vite dev | 中（约 1-1.5 工作日） | 低-中：双运行模式 origin 适配 | **P0，最先做** |
| **方向二：SSRF 防护** | 🔴 大：任意内网探测、云元数据窃取（169.254.169.254）、内网服务横向调用 | 小：`security/ssrf.py` 新模块 + `http_request` + 出网点审计 | **低**（约 0.5-1 工作日） | 低：纯新增模块，测试友好 | **P0，次之**（成本最低、见效最快） |
| **方向三：沙箱失效修复** | 🔴 大：任意代码执行无资源/网络/进程约束 | 中：sandbox 包 5 文件 + 新 `job.py` | **高**（约 2-3 工作日，含真机验证） | 高：Job Object 需 ctypes 手写、子进程级测试不稳定、Windows 网络拦截仅应用层 | **P1，最后做**（已有 elevated 权限确认兜底，敞口部分缓解） |

### 建议实施顺序

```
方向一(admin 鉴权+CORS) → 方向二(SSRF) → 方向三(沙箱) → 收尾(C-4 + 回归)
```

**理由**：
1. **方向一先行的依据**：方向一修复的是"钥匙挂在门上的问题"——不先堵住 admin 裸奔，方向二/三的任何成果（如新加的 URL 校验、沙箱配置）仍可被任意本机网页经 CORS `*` 篡改/绕过。且方向一不依赖任何其他改造，可立即开展。
2. **方向二优于方向三**：SSRF 是纯新增模块、单工具接入、表驱动测试，半天见效；沙箱修复技术风险最高（Windows 平台限制），需要更长的实现与真机验证周期，且其"网络拦截"子任务与方向二的出网校验思路同源（可复用 URL 分类逻辑，但模块边界保持独立）。
3. **方向三最后但必须做**：代码执行是系统最高权限工具，Job Object + 网络接线是审查点 A.1.1 的直接要求；其低垂果实（`disable_network` / `preexec_fn` 接线）可与方向二同批次先行落地。

> ⚠️ 若用户坚持按"沙箱 → admin → SSRF"的原始顺序，可将方向三的**接线子任务（T3.1，低垂果实）**提前至批次一，完整 Job Object 仍排在 admin 之后——保证每个批次都有独立、可验收的安全增量。

---

## 三、方向一：admin 鉴权加固 + CORS 收窄

### 3.1 技术改造范围

**新增 `backend/private_agent/security/` 包（auth.py）**：
- `PA_ADMIN_TOKEN` 管理：优先读环境变量；缺失时生成 64 hex 并持久化到 `backend/.env`（复用 admin.py 已有 `_ensure_master_key` 的"自动生成 + 追加 .env"模式，独立 token 不与 PA_MASTER_KEY 混用）
- `require_admin` FastAPI 依赖：读取 `X-Admin-Token` / `Authorization: Bearer` 头，`hmac.compare_digest` 常量时间比较，失败抛 401（带 `WWW-Authenticate`）
- 暴露 `get_admin_token()` 供 Electron preload / 前端读取（仅本机回环可读，返回脱敏提示）

**改造 `main.py`**：
- `app.include_router(admin.router, dependencies=[Depends(require_admin)])`（eval/files 同样挂载）——router 级统一挂载，单点生效
- CORS：`allow_origins` 从 `["*"]` 改为白名单，来源 `config.yaml security.cors.allow_origins`，默认值：
  ```yaml
  security:
    cors:
      allow_origins: ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4173", "app://."]
  ```
  - vite dev 任意端口访问场景：若检测到 dev 模式（`PA_ENV=dev`）可放宽为 `localhost`/`127.0.0.1` 任意端口（正则匹配），生产严格白名单
  - `allow_methods`/`allow_headers` 收窄为实际使用的集合
- `/health`、`/`、`/ws` **不鉴权**（健康检查与聊天主链路；WS 鉴权列为可选增强，本阶段默认保持开放——本地 sidecar 场景，聊天链路已由 Electron 独占端口通信）

**前端适配（`frontend/`）**：
- api client 统一注入 `X-Admin-Token` 头
- Electron 生产：preload 从 `backend/.env` 读取 token 注入（`contextBridge` 暴露只读接口）
- vite dev：设置页提供 token 输入框（带"从 backend/.env 自动读取"按钮，走文件读取接口或手动粘贴）

**文档**：`docs/security-model.md` 记录 token 存储、双模式适配、威胁模型

### 3.2 验收标准

| # | 验收项 | 判定 |
|---|---|---|
| AC-1 | `GET /admin/settings` 无 token | **401** + `WWW-Authenticate` |
| AC-2 | 错误 token（含长度攻击样本） | **401**，响应时间与正确 token 一致（常量时间） |
| AC-3 | 正确 token 访问 admin/eval/files 全部端点 | 200，全功能（provider/MCP/沙箱/wallpaper 读写） |
| AC-4 | `GET /health`、`GET /`、WS `/ws` 无 token | 200 / 正常连接（不受影响） |
| AC-5 | Origin 白名单外（如 `http://evil.com`）请求 | 响应**不含** `access-control-allow-origin` 头（浏览器拦截） |
| AC-6 | Origin 白名单内（5173）请求 | 正常 CORS 头 + 业务响应 |
| AC-7 | Electron 生产模式全流程 | 设置页/技能/聊天可用（preload 注入） |
| AC-8 | 回归 | 后端 pytest 全量 + 前端 vitest 全绿（`test_main_cors.py` 断言同步更新） |

### 3.3 任务分解（WBS）

| 任务 | 内容 | 依赖 |
|---|---|---|
| T1.1 | `security/auth.py`：token 管理 + `require_admin` 依赖 + 常量时间比较 | 无 |
| T1.2 | `main.py`：router 级挂依赖 + CORS 白名单收窄 + `PA_ENV` dev 放宽 | T1.1 |
| T1.3 | `config/config.yaml`：`security.cors.allow_origins` 配置项 + loader 校验 | T1.2 |
| T1.4 | 前端 api client 注入头 + Electron preload + vite dev 设置页 token 入口 | T1.1 |
| T1.5 | 测试：`tests/test_admin_auth.py`（AC-1~4）+ `test_main_cors.py` 更新（AC-5/6）+ 既有 34+8+1 admin 测试补 token fixture | T1.2 |
| T1.6 | 真机验证：Electron + vite dev 双模式走查（AC-7） | T1.4/T1.5 |

---

## 四、方向二：SSRF 防护

### 4.1 技术改造范围

**新增 `backend/private_agent/security/ssrf.py`**：
- `validate_outbound_url(url, allow_private=False) -> str`（返回规范化 URL 或抛 `SSRFBlockedError`）：
  - scheme 白名单：仅 `http` / `https`（拒绝 `file://`、`gopher://`、`ftp://` 等）
  - 主机名解析：`socket.getaddrinfo` 全量解析（IPv4+IPv6），**任一结果命中黑名单即拒绝**（防"多 A 记录逃逸"）
  - 黑名单 CIDR（默认全拒）：`127.0.0.0/8`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`169.254.0.0/16`（**含云元数据 169.254.169.254**）、`0.0.0.0/8`、`100.64.0.0/10`（CGNAT）、`::1`、`fc00::/7`、`fe80::/10`、IPv4-mapped IPv6
  - 选项 `allow_private`（默认 false）：局域网场景（内网 NAS/自建服务）可显式开启
- `safe_httpx_client()` 工厂：
  - `follow_redirects=True` 但**每跳调用 `validate_outbound_url`**（防 302 → 127.0.0.1）
  - 响应体大小上限（默认 2MB，与 MCP 2MB 行上限口径一致）+ 超时
- DNS rebinding 缓解（第一版）：校验与连接分离场景下，解析后**直接连接解析出的 IP**（httpx 不支持原生，实现为"预解析 + 替换 host"）或双解析比对；若实现成本过高则降级为"校验 + 日志告警"，标注为已知边界（评估后决策）

**接入点**：
- `tools/builtins/http_request.py`：改用 `safe_httpx_client`（主风险面，必改）
- `tools/builtins/web_search.py`：后端 URL 为固定域名，改为**显式域名白名单校验**（防御式，防止未来参数化引入漏洞）
- `tools/mcp_client.py`：server 注册时对 `url` 配置做一次性校验（可选，配置信任场景默认跳过，仅日志）
- 其他出网点盘点（embedding_service / knowledge / eval）：写入 `docs/security-model.md` 出网点清单，逐项标注风险等级

**配置**：
```yaml
security:
  ssrf:
    enabled: true
    allow_private: false   # 局域网场景可开启
    max_response_bytes: 2097152
    deny_cidrs: []          # 额外黑名单（可扩展）
```

### 4.2 验收标准

| # | 验收项 | 判定 |
|---|---|---|
| AC-1 | `http_request` 请求 `http://127.0.0.1:8765/admin/settings` | 拒绝，错误信息明确（含被拒原因） |
| AC-2 | `http://192.168.1.1/`、`http://10.0.0.1/`、`http://172.16.0.1/` | 拒绝 |
| AC-3 | `http://169.254.169.254/latest/meta-data/`（云元数据） | 拒绝 |
| AC-4 | `http://example.com` | 正常返回 |
| AC-5 | 公网 URL 302 重定向到 `http://127.0.0.1/` | 重定向目标被拒，请求中止 |
| AC-6 | `file:///etc/passwd`、`gopher://` | scheme 拒绝 |
| AC-7 | `allow_private=true` 时内网请求放行（配置开关生效） | 通过 |
| AC-8 | 响应体 > 2MB | 截断/拒绝，不整包拉取 |
| AC-9 | 回归 | pytest 全量 + 前端 vitest 全绿 |

### 4.3 任务分解

| 任务 | 内容 | 依赖 |
|---|---|---|
| T2.1 | `security/ssrf.py`：CIDR 黑名单 + `validate_outbound_url` + `safe_httpx_client` | 无 |
| T2.2 | `http_request` 接入 + `web_search` 域名白名单 | T2.1 |
| T2.3 | 出网点审计清单（docs/security-model.md）+ MCP URL 一次性校验 | T2.2 |
| T2.4 | 配置项 `security.ssrf.*` + loader 校验 | T2.1 |
| T2.5 | 测试：`tests/test_ssrf.py`（IP 分类表驱动单测 + http_request 集成用例，含重定向/scheme/大小用例） | T2.2 |
| T2.6 | DNS rebinding 缓解实现或降级决策 + 文档标注 | T2.5 |

---

## 五、方向三：沙箱失效修复

### 5.1 技术改造范围

**T3.1 接线修复（低垂果实，先行）**：
- `sandbox/service.py`：第 4 步改为 `safe_env = disable_network(env_sanitizer.sanitize(...)) if not network_enabled else env_sanitizer.sanitize(...)`——`config.yaml` 已有 `limits.network_enabled: false`，**接线即生效**
- `sandbox/executor.py`：`create_subprocess_exec` 支持透传 `preexec_fn`（POSIX 分支，Windows 自动忽略）→ `ResourceLimiter.get_preexec_fn()` 真正生效（RLIMIT_AS / RLIMIT_CPU）
- `SandboxService.execute` 删除未使用的 `ResourceLimiter` 直连引用，改为经 executor 传递

**T3.2 Windows Job Object（核心新增）`sandbox/job.py`**：
- **依赖策略**：venv 无 pywin32 → 用 `ctypes` 直接调 `kernel32`（`CreateJobObjectW` / `SetInformationJobObject` / `AssignProcessToJobObject`），零新依赖；若实现受阻（版本差异）则评估新增 pywin32（约 5MB，标注于 README）
- 限制集合（`JOBOBJECT_EXTENDED_LIMIT_INFORMATION` + `JOBOBJECT_BASIC_UI_RESTRICTIONS`）：
  - `JOB_OBJECT_LIMIT_PROCESS_MEMORY`（512MB，取自 `config sandbox.limits.memory_limit_mb`）
  - `JOB_OBJECT_LIMIT_JOB_TIME`（CPU 总时长，默认 300s，超时由 Job 强制终止 + asyncio 超时双保险）
  - `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`（活动进程数上限，如 4，防进程树爆炸）
  - `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`（句柄释放即杀全树）
  - UI 限制：禁窗口/剪贴板/桌面访问（`JOB_OBJECT_UILIMIT_*`）
- 生命周期：`KeepAliveJob` 持有 Job 句柄，进程退出后显式关闭（否则 KILL_ON_JOB_CLOSE 提前生效）

**T3.3 executor 集成**：
- Python/Node 子进程 spawn 成功后立即 `AssignProcessToJobObject`（**必须在子进程首次执行代码前**；注意：若父进程自身在 Job 内需 `BreakAwayOk`，本场景父进程为 sidecar 不在 Job 内，无需处理）
- POSIX 分支：`preexec_fn` RLIMIT 保持（T3.1 已接）
- `SandboxResult` 增加可选字段：`job_stats`（内存峰值/进程数，尽力而为）

**T3.4 PathFilter 与工作区约束（务实边界）**：
- **现实约束**：子进程内文件访问无法静态拦截（PathFilter 在 service 层不可见子进程 syscall）——明确写入安全模型：沙箱为"**约束 + 审计 + 确认**"三层，非强隔离（无 Docker/WSL2）
- 落地动作：工作目录注入 `session_id` 隔离（已有 WorkspaceManager）；`EnvSanitizer` 保证 `.env`/密钥不可读（已有，补测试）；文档标注"文件系统越界靠 `file_*` 工具路径强制（阶段一 T-1）而非沙箱"

**T3.5 测试**（子进程级，真实平台）：
- `tests/test_sandbox_job.py`（`@pytest.mark.skipif(os.name != "nt")`）：内存炸弹（`[0]*10**9`）→ Job 终止、进程树 spawn → 进程数限制、`while True` → Job 时间上限
- `tests/test_sandbox_network.py`：默认 `network_enabled=false` 下 `urllib.request.urlopen("http://127.0.0.1:8765")` / `httpx` 请求 → 失败；`network_enabled=true` 时放行（配置开关验证）
- `tests/test_sandbox_security.py` 扩展：`.env` 内容不可见断言

### 5.2 验收标准

| # | 验收项 | 判定 |
|---|---|---|
| AC-1 | `x = [0] * 10**9`（超 512MB） | 进程被 Job 终止，exit_code 非 0，无内存耗尽拖垮 sidecar |
| AC-2 | `subprocess.Popen("python -c 'import time;time.sleep(999)'")` + 无限 fork | 活动进程数受限，全树可被杀 |
| AC-3 | `while True: pass` | asyncio 超时 + Job 时间上限双保险终止 |
| AC-4 | 默认禁网：沙箱内 `urlopen("http://127.0.0.1:8765")` / `https://example.com` | 失败（应用层代理阻断） |
| AC-5 | `network_enabled=true`（显式开启） | 网络可用（开关生效） |
| AC-6 | 沙箱内 `open(".env")` / `os.environ` | 敏感键不可见（EnvSanitizer） |
| AC-7 | 正常代码（数据分析/绘图）执行 | 不受影响（回归 test_code_execution_tool 等） |
| AC-8 | POSIX 平台 | RLIMIT 生效（preexec_fn），Job 相关测试 skip |
| AC-9 | 回归 | pytest 全量 + 前端 vitest 全绿 |

### 5.3 任务分解

| 任务 | 内容 | 依赖 |
|---|---|---|
| T3.1 | 接线修复：`disable_network` + `preexec_fn` 透传 + 删除死引用 | 无（可与 T1 并行） |
| T3.2 | `sandbox/job.py`：ctypes Job Object + KeepAlive + 限制集合 | 无 |
| T3.3 | executor 集成 AssignProcessToJobObject + result.job_stats | T3.2 |
| T3.4 | PathFilter 边界文档化 + 沙箱安全模型章节（security-model.md） | T3.1 |
| T3.5 | 测试：test_sandbox_job.py / test_sandbox_network.py / security 扩展 | T3.1~T3.3 |
| T3.6 | 真机验证（Windows）：AC-1~AC-7 逐项 | T3.5 |

---

## 六、阶段二总体迭代计划

### 6.1 目标

1. **安全硬边界**：管理面（admin）鉴权 + 出网面（SSRF）校验 + 执行面（沙箱）约束，三面封堵审查报告致命项（A.1.1 / A.3.10 / A.2.3）
2. **零功能回归**：917 pytest + 13 vitest 基线全绿，Electron 与 vite dev 双模式可用
3. **可测试安全**：每个验收项有自动化用例（含真实平台子进程级测试），安全策略全部配置驱动

### 6.2 分步任务总览（WBS + 依赖图）

```
批次 0（准备）                    T0  基线回归 + 出网点盘点 + security/ 包骨架
                                    │
批次 1（安全地基，P0）◄────────────┼── 依赖无，最先开展
  T1.1 auth.py → T1.2 main.py/CORS → T1.3 config → T1.4 前端 → T1.5 测试 → T1.6 真机
                                    │
批次 2（出网防护，P0）◄────────────┼── 与批次 1 并行（模块独立）
  T2.1 ssrf.py → T2.2 接入 → T2.3 审计 → T2.4 配置 → T2.5 测试 → T2.6 rebinding 决策
  T3.1 沙箱接线（低垂果实）◄────────┘   （可与 T2 同批提交）
                                    │
批次 3（执行隔离，P1）◄────────────┼── 依赖 T3.1
  T3.2 job.py → T3.3 executor → T3.4 边界文档 → T3.5 测试 → T3.6 真机
                                    │
批次 4（收尾）                    T4  C-4 事件级去重 + 全量回归 + 双模式真机 + 部署同步 + 收尾报告
```

### 6.3 依赖关系（显式）

| 依赖 | 说明 |
|---|---|
| T1.2 ← T1.1 | 鉴权依赖先于挂载 |
| T1.4/T1.5 ← T1.2 | 前端与测试依赖服务端改造 |
| T2.2 ← T2.1 | 接入依赖校验模块 |
| T3.3 ← T3.2 | Job 模块先于集成 |
| T3.5/T3.6 ← T3.1~T3.3 | 测试依赖接线与 Job |
| T4 ← 全部 | 收尾批次依赖所有方向完成 |
| 批次 1/2/3 | **无强依赖**，可按人力并行；每批次独立 git 提交 + 独立回归 |

### 6.4 里程碑

| 里程碑 | 判定标准 | 预计 |
|---|---|---|
| **MS-1 安全地基** | 43 端点全部 401 保护（AC-1~4）；CORS 白名单生效（AC-5/6）；Electron+vite 双模式可用（AC-7）；pytest 全绿 | ✅ 批次 1 完成（933 passed） |
| **MS-2 出网防护** | SSRF 全类用例通过（AC-1~8）；http_request/web_search 已接入；出网点审计清单落文档 | ✅ 批次 2 完成（28 用例 + 真机验证） |
| **MS-3 执行隔离** | Job Object 子进程级测试通过（AC-1~3）；默认禁网生效（AC-4/5）；沙箱安全模型文档化（AC-6）；真机验证完成 | ✅ 批次 3 完成（Job 4 + 网络 7 用例） |
| **MS-4 阶段收尾** | C-4 事件级去重落地；全量回归（917+新增）全绿；阶段二收尾报告归档；PA1.0 同步 | 批次 4 完成 |

### 6.5 提交与协作约定（沿用阶段一）

- 每批次一个 git 提交（`docs/architecture-revision.md` §12 采纳状态同步打勾）
- 每批次完成先小范围真机验证（一个会话/一次浏览器操作），再全量回归
- 测试基线：后端 `pytest tests/ --ignore=test_eval_full_cycle.py`（加载 backend/.env）+ 前端 `vitest`
- 安全策略默认值：**默认安全、显式开启**（allow_private / network_enabled 均默认关闭）

---

## 七、风险与兼容

| 风险 | 等级 | 缓解 |
|---|---|---|
| CORS 收窄破坏 vite dev / Electron 生产 | 中 | `security.cors.allow_origins` 可配置；默认白名单含 5173/4173/app://；`PA_ENV=dev` 放宽 localhost 任意端口；T1.6 双模式真机 |
| admin token 在浏览器 dev 模式的传递 | 中 | preload 注入（Electron）/ 设置页输入 + 自动读取（dev）；token 仅本机回环通信 |
| Job Object ctypes 版本差异 | 高 | 先做最小子集（内存/时间/进程数，Win7+ 均支持）；失败则评估 pywin32 依赖 |
| 沙箱网络拦截仅应用层（HTTP_PROXY），不防 socket 直连 | 中（已知边界） | 文档明确"约束+审计+确认"非强隔离；code_execution 保持 elevated 权限确认；网络默认关闭 |
| DNS rebinding 绕过 | 中 | 校验时全 A/AAAA 记录遍历拒绝；连接复用解析 IP（或双解析）；降级方案=日志告警（T2.6 决策） |
| 沙箱测试在 CI/本机不稳定（子进程真实执行） | 中 | win32 用例 skipif 平台标记；内存炸弹用小规模参数；真机验证人工走查兜底 |
| 既有 43 端点测试需补 token fixture | 低 | 集中 fixture（`tests/conftest.py` 注入有效 token），批量更新 |
| 与"WS /ws 不鉴权"的边界 | 低 | 明确威胁模型：/ws 为本机聊天链路，Electron 独占；如后续暴露需在 WS query 加 token（可选增强） |

---

*本计划由 WorkBuddy 生成，随阶段二实施跟踪更新。*
