# 安全模型与出网点审计

> 项目：私人智能体（Private Agent）· 阶段二批次 1-2（2026-08-04）
> 依据：AUDIT-REPORT + 深度审查报告安全条目（A.1.1/A.3.10/A.2.3/B.1.1/B.1.3）
> 状态：批次 1（鉴权+CORS）✅、批次 2（SSRF）✅；沙箱（批次 3）实施中

---

## 一、威胁模型（本机 Sidecar）

| 威胁源 | 能力 | 防护 |
|---|---|---|
| 任意本机网页（浏览器） | 经 CORS 跨域读取/改写 8765 管理面 | 批次 1：CORS 白名单 + admin token |
| 任意本机进程 | 直接 HTTP 请求 8765 | 批次 1：admin token 鉴权（43 端点） |
| LLM（工具调用诱导） | 触发 http_request / code_execution 等工具 | 批次 2：SSRF 校验；批次 3：沙箱约束 |
| 恶意代码（沙箱内） | 出网 / 读密钥 / 拖垮资源 | 批次 3：Job Object + 禁网 + env 脱敏 |

**边界声明**：本应用为本地桌面 Sidecar（无管理员权限），沙箱为"约束 + 审计 + 确认"三层，**非强隔离**（无 Docker/WSL2）。对外暴露面为 8765 端口（本机回环），管理面已鉴权。

---

## 二、控制面鉴权（批次 1，已落地）

- **Token**：`PA_ADMIN_TOKEN`（独立于 `PA_MASTER_KEY`；后者仅用于 AES 加密）
- **优先级**：环境变量 > `backend/.env`（首次 `run_sidecar` 由 `ensure_admin_token()` 生成 64 hex 持久化，幂等）
- **校验**：`require_admin` FastAPI 依赖，读取 `X-Admin-Token` 头，`hmac.compare_digest` 常量时间比较；失败 401 + `WWW-Authenticate: Bearer`
- **挂载**：`main.py` 对 admin/eval/files 三个 router 级挂载（43 端点全保护）；`/`、`/health`、`/ws` 豁免（健康检查与聊天链路）
- **CORS**：白名单 `security.cors.allow_origins`（默认 5173/4173/`app://.`）；`dev_wildcard` 或 `PA_ENV=dev` 时放宽 localhost/127.0.0.1 任意端口（vite 端口占用切换）；`allow_methods` 收窄为 GET/POST/PUT/DELETE，`allow_headers` 收窄
- **前端**：Electron preload 注入（主进程 sidecar 启动后补读 .env）；浏览器 dev 模式经设置页-安全管理录入（localStorage）；`adminFetch` 统一携带 token，401 派发 `pa:auth-required`

## 三、出网点审计清单（批次 2，已落地/已标注）

| 出网点 | 输入来源 | 风险级 | 现状与措施 |
|---|---|---|---|
| `http_request` 工具 | LLM（任意 URL） | 🔴 高 | ✅ `security/ssrf.py` 全量校验（见下） |
| `web_search` 工具 | LLM（query） | 🟡 中 | ✅ 固定域名白名单（duckduckgo/bocha/bing）；URL 本身硬编码 |
| MCP server 注册 URL | 配置（设置页） | 🟡 中 | ⚠️ 配置信任：MCP 工具 URL 由管理员录入，默认不做 SSRF 拦截（可能自建内网服务）；后续可加"装配时 URL 校验 + 日志"（可选增强） |
| MCP 工具调用 | 配置 URL + LLM 参数 | 🟡 中 | ⚠️ 同配置信任；协议层已有 2MB 行上限 + jsonrpc 校验（阶段一 T-4） |
| embedding_service / knowledge | 配置 URL | 🟢 低 | ⚠️ 配置信任；无 LLM 任意 URL 输入 |
| eval 子系统 | 配置 | 🟢 低 | 无外呼/仅本地跑批 |

## 四、SSRF 防护设计（批次 2，已落地）

实现：`backend/private_agent/security/ssrf.py`

1. **scheme 白名单**：仅 http/https（file/gopher/ftp/javascript 拒绝）
2. **主机字面 IP 预判**：`localhost`、回环/私网/链路本地/保留字面 IP 直接拒绝（免解析）
3. **全量解析校验**：`getaddrinfo` 解析全部 A/AAAA 记录，**任一**命中黑名单 CIDR 即拒绝（防多 A 记录逃逸）
   - 黑名单：`0.0.0.0/8`、`10/8`、`100.64/10`（CGNAT）、`127/8`、`169.254/16`（**含云元数据 169.254.169.254**）、`172.16/12`、`192.168/16`、TEST-NET、组播/保留、`::1`、`fc00::/7`、`fe80::/10`、`ff00::/8`（ipaddress 自动处理 v4-mapped）
4. **重定向每跳校验**：`SafeHttpxClient` follow_redirects=False 手动跟随，每跳先 `validate` 再请求（302→内网被拒），最多 5 跳
5. **响应体大小上限**：流式累计读取，超 `max_response_bytes`（默认 2MB）即终止
6. **超时与代理**：统一 30s 超时；`trust_env=False` 不读系统代理（Windows 系统代理劫持教训）
7. **配置**：`security.ssrf.enabled` / `allow_private`（局域网显式开启）/ `max_response_bytes`；`http_request` 支持 `_ssrf_config` 测试注入

**已知边界（DNS rebinding）**：校验与连接是两次 DNS 解析，攻击者可在间隙切换解析结果。第一版策略：校验时全量记录解析 IP（日志审计）；**严格绑定连接 IP 的自定义 transport 列为后续增强**（启用 `ssrf.strict_dns=true` 时实现，暂未实施）。本地 Sidecar 场景利用门槛高（需攻击者控制域名解析），风险可接受。

---

## 五、沙箱安全模型（批次 3，已落地）

- 代码执行工具 `code_execution`：`safety_level="elevated"` → WS 权限确认（60s 超时拒绝 + 会话级缓存）
- 环境变量脱敏 `EnvSanitizer`：过滤 KEY/SECRET/TOKEN/PASSWORD 等（`.env`/密钥不可读，已有端到端断言）
- 代码预扫描 `CodeScanner`：危险模式正则告警（不阻断，记录 react_events）
- **Windows Job Object（新增 `sandbox/job.py`，ctypes 零依赖）**：
  - 内存上限（`JOB_OBJECT_LIMIT_PROCESS_MEMORY`）：超限分配失败
  - CPU 总时长（`JOB_OBJECT_LIMIT_JOB_TIME`）：超时系统终止全部进程
  - 活动进程数（`JOB_OBJECT_LIMIT_ACTIVE_PROCESS`，默认 4）：防进程树爆炸
  - `KILL_ON_JOB_CLOSE`：句柄在子进程结束后才释放
  - UI 限制：禁剪贴板/系统参数/显示设置/桌面/退出窗口
  - 失败降级：父进程在其他 Job 无 BREAKAWAY（ERROR_ACCESS_DENIED）→ attach 失败仅警告，不阻断执行
- **网络隔离（`disable_network` 接线）**：config `limits.network_enabled`（默认 false → **默认禁网**）；注入无效代理（`http://127.0.0.1:9`，移除 NO_PROXY——修正了原实现 NO_PROXY=* 使拦截失效的矛盾）
- **POSIX RLIMIT**：`preexec_fn` 透传 `ResourceLimiter.get_preexec_fn()`（RLIMIT_AS/RLIMIT_CPU，此前实例化后从未调用）
- **边界（文档化）**：
  - 子进程内文件访问无法静态拦截（PathFilter 在 service 层不可见 syscall）——文件系统越界由 `file_*` 工具服务端路径强制（阶段一 T-1）兜底
  - 网络隔离仅对读环境变量代理的 HTTP 库（httpx/requests）有效；socket 直连、Windows 内置 urllib（读注册表代理）可绕过——主要防线是 elevated 权限确认 + Job 资源约束
  - `create_subprocess_exec` 返回后 attach 存在毫秒级竞态（超短代码可能提前完成）——严格 CREATE_SUSPENDED 方案列为后续增强
  - 沙箱为"约束 + 审计 + 确认"三层，**非强隔离**（无 Docker/WSL2，普通用户无管理员权限）
