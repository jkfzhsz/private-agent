# MCP 2026-07-28 规范更新影响分析

> 分析日期：2026-07-30
> 分析目标：评估 MCP 协议史上最大更新对 Private Agent 项目的影响与架构调整建议

---

## 一、更新概要

2026 年 7 月 28 日，Anthropic 正式发布 MCP `2026-07-28` 规范（第 5 版），这是该协议**自 2024 年 11 月面世以来规模最大、最系统性的颠覆式修订**。核心变化概括为：

| 维度 | 旧规范 (2025-11-25) | 新规范 (2026-07-28) |
|---|---|---|
| 协议状态 | **有状态**（Stateful） | **完全无状态**（Stateless） |
| 连接握手 | 强制 `initialize` / `initialized` 握手 | 彻底移除握手，版本信息随请求携带 |
| 会话管理 | `Mcp-Session-Id` 固定会话，需粘性路由 | 无会话，每请求自包含 |
| 扩展机制 | 无正式框架 | 版本化扩展框架（MCP Apps / Tasks / EMA） |
| 授权 | 基础 OAuth 支持 | 强化 OAuth 2.0 / OIDC / CIMD |
| 工具 Schema | 受限 JSON Schema | 完整 JSON Schema 2020-12 |
| SDK | v1.x stable / v2 beta | v2.0.0 正式版 + v1.x 维护线 |

---

## 二、详细变更清单

### 2.1 协议核心：有状态 → 无状态

**变更内容（SEP-2575 / SEP-2567）：**

- 移除 `initialize` / `initialized` / `notifications/initialized` 握手过程
- 移除 HTTP 层的 `Mcp-Session-Id` 请求头
- 协议版本、客户端信息、能力声明改为在每次请求的 `_meta` 中携带
- HTTP 层新增 `MCP-Protocol-Version` 请求头
- 新增可选 `server/discover` 方法用于预先探索服务器能力

**旧规范流程：**
```
POST /mcp HTTP/1.1
Content-Type: application/json
{"jsonrpc":"2.0","id":1,"method":"initialize",
 "params":{"protocolVersion":"2025-11-25","capabilities":{},
  "clientInfo":{"name":"my-app","version":"1.0"}}}

→ 返回 Mcp-Session-Id，后续请求必须携带该 ID
```

**新规范流程：**
```
POST /mcp HTTP/1.1
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: search
Content-Type: application/json
{"jsonrpc":"2.0","id":1,"method":"tools/call",
 "params":{"name":"search","arguments":{"q":"..."},
  "_meta":{"io.modelcontextprotocol/clientInfo":{...}}}}
```

**对基础架构的影响：** 请求可被任意网关/实例分发，不再需要粘性会话。

### 2.2 可路由性与缓存增强

**变更内容（SEP-2243 / SEP-2549）：**

- Streamable HTTP POST 请求**必须**携带 `Mcp-Method` 请求头；涉及具名目标（如 tools、resources）还需 `Mcp-Name`
- 服务器拒绝请求头与请求体不一致的请求
- 列表查询和资源读取结果新增 `ttlMs`（有效期毫秒）和 `cacheScope`（`public` / `private`）字段
- 建议 `tools/list` 返回顺序保持**确定性**，以利上游 Prompt Cache 复用
- W3C Trace Context 随 `_meta` 传播，支持分布式追踪

### 2.3 多轮往返请求（MRTR）

**变更内容（SEP-2322 / SEP-2260）：**

服务器需要中间输入确认时，不再依赖 SSE 长连接推送，改为返回 `InputRequiredResult`：

```json
{
  "resultType": "inputRequired",
  "inputRequests": {
    "confirm": {
      "type": "elicitation",
      "message": "确定删除 3 个文件？",
      "schema": { "type": "boolean" }
    }
  },
  "requestState": "eyJzdGVwIjoxLCJmaWxlcyI6WyJhIiwiYiIsImMiXX0="
}
```

- 客户端收集输入后，携带 `inputResponses` 和 `requestState` 重新发送原始请求
- `requestState` 包含恢复所需的所有状态，重发可路由到任意实例

### 2.4 显式句柄模式（Explicit-Handle Pattern）

**变更内容（SEP-2567）：**

协议层不再管理应用状态。需要跨调用携带状态的场景（购物篮、浏览器会话、长流程），由工具返回标识符（如 `basket_id`、`browser_id`），模型将其作为普通参数传入后续调用。

- 状态标识符对模型**可见**，可在工具间组合和传递
- 在无认证的服务器上，标识符至少需要 **128 位加密安全随机数据** + 有限过期时间
- SEP-2567 要求已验证服务器在每次调用中校验「句柄 + 认证主体」组合

### 2.5 授权与安全强化

**变更内容：**

- 授权响应中的 `iss`（issuer）**必须**与记录的授权服务器匹配，防止 OAuth mix-up 攻击
- 动态客户端注册（DCR）明确 `application_type`，防止桌面/CLI 客户端被误判为 `web`
- **DCR 已被弃用**，优先使用 Client ID Metadata Documents（CIMD）
- 存储的客户端凭据必须绑定其 issuer，不可跨授权服务器复用
- 企业托管授权（EMA）扩展允许企业 IdP 集中管理权限

### 2.6 扩展框架

**变更内容：**

正式引入**版本化扩展框架**，扩展 ID 以 `io.modelcontextprotocol` 开头，通过 capability 协商启用：

| 扩展 | 功能 |
|---|---|
| **MCP Apps** | 在对话中渲染图表、表单、视频等交互式 UI（sandboxed iframe） |
| **Tasks** | 持久化长时间运行任务（分钟~小时级），`tasks/get` 获取状态，`tasks/cancel` 取消 |
| **EMA** | 企业托管授权，IdP 集中管理员工权限 |

> **注意**：新 Tasks 扩展**不向后兼容**旧的实验性 Tasks 实现。旧版 `tasks/result` / `tasks/list` 已移除，改用轮询 `tasks/get`。`tasks/list` 因无会话环境无法确定安全列表范围而被移除。

### 2.7 已弃用功能（12 个月宽限期）

| 功能 | 替代方案 |
|---|---|
| Roots | 工具参数、资源 URI 或服务器配置 |
| Sampling | LLM 提供商 API 直连 |
| Logging | stderr（stdio 传输）或 OpenTelemetry |
| 旧 HTTP+SSE 传输 | Streamable HTTP |
| DCR（动态客户端注册） | CIMD（Client ID Metadata Documents） |
| `-32002` 错误码 | `-32602`（JSON-RPC 标准 Invalid Params）|

> 弃用不代表立即移除。这些功能至少在未来 12 个月内继续可用，实际移除需单独提案。

### 2.8 其他技术变更

| 变更 | 详情 |
|---|---|
| 工具 Schema | 升级为完整 JSON Schema 2020-12，支持 `oneOf`/`anyOf`/`$ref`/条件式（SEP-2106）|
| `resultType` | 所有结果**必须**包含 `resultType` 字段 |
| 版本协商 | 自动协商模式：`server/discover` 探测 → 如服务器仅支持旧版则降级回 `initialize` |
| Python SDK | v2.0.0rc1 发布，API 清理 + 协议对齐；`pip install mcp` 仍解析到 v1.x stable |
| 订阅机制 | 变更通知统一走 `subscriptions/listen` 通道 |
| 生命周期 | 新功能生命周期：Active → Deprecated（≥12个月）→ Removed |

---

## 三、对 Private Agent 项目的影响分析

### 3.1 影响总评

| 蓝图章节 | 模块 | 影响程度 | 说明 |
|---|---|---|---|
| 2.3 | 通信协议 | 🔴 **高** | MCP 协议底层变更，需双协议支持 |
| 5.1-5.4 | MCP Client + 双轨工具架构 | 🔴 **高** | MCP Client 核心实现需重写 |
| 5.4 | MCP 双探活（stdio/HTTP） | 🟡 中 | 探活逻辑变化，不再有 initialize 握手 |
| 5.5 | 会话锁定工具集 | 🟢 低 | 基本不受影响，锁定逻辑在本项目侧 |
| 5.12 | 权限确认与缓存 | 🟡 中 | 可受益于新 OAuth 2.0/OIDC 和 EMA 扩展 |
| 5.13 | 工具调用入 react_events | 🟢 低 | 事件记录不变 |
| 5.14 | 异步事件 | 🟡 中 | Tasks 扩展可替代自定义异步机制 |
| 5.15 | Artifact 机制 | 🟢 低 | 显式句柄模式天然对齐现有 artifact 设计 |
| 5.16 | 安全与资源限额 | 🟢 低 | 影响不大 |
| 5.17 | 工具描述规范 | 🟡 中 | JSON Schema 2020-12 升级影响 ToolDef 定义 |
| 7.1-7.15 | 场景 Skills | 🟢 低 | Skill 层基本不受 MCP 传输层变更影响 |
| 8.1-8.16 | 评估闭环 | 🟢 低 | 不直接影响评估 |

### 3.2 核心模块影响详细分析

#### 3.2.1 MCP Client（蓝图 5.3）— 🔴 高影响

蓝图目前假定 MCP 协议仍为 **有状态 + initialize 握手**。新规范下：

**旧设计（蓝图原案）：**
```
class MCPClient:
    async def connect(self, transport):
        # 发送 initialize 握手
        # 接收并保存 Mcp-Session-Id
        # 后续请求携带 Session-Id
        
    async def call_tool(self, name, args):
        # 在请求中注入会话 ID
        # 依赖粘性路由
```

**新设计（需改为）：**
```
class MCPClient:
    async def discover(self, transport):
        # 可选：发送 server/discover 获取能力
        # 根据响应选择协议版本
        
    async def call_tool(self, name, args):
        # 每次请求携带协议版本 + 能力信息（_meta）
        # 无会话 ID，请求可路由到任意实例
        # 依赖 Mcp-Method / Mcp-Name HTTP 头
        
    # 新增：MRTR 支持
    async def handle_input_required(self, request_state, input_responses):
        # 处理 InputRequiredResult
        # 重新发送原始请求
```

**具体需要改变的内容：**

1. **移除** `initialize()` / `initialized()` 握手代码
2. **移除** `Mcp-Session-Id` 管理与存储逻辑
3. **新增** 每次请求注入 `_meta`（协议版本 + 客户端信息 + 能力声明）
4. **新增** HTTP 传输层 `MCP-Protocol-Version`、`Mcp-Method`、`Mcp-Name` 请求头支持
5. **新增** `server/discover` 可选探测方法
6. **新增** MRTR 多轮交互处理：检测 `resultType: "inputRequired"` → 收集输入 → 重新发送
7. **新增** 版本协商：`auto` 模式先尝试 2026-07-28，失败时降级回 2025-11-25
8. **修改** 超时与重试策略：新协议下重试可发送到不同实例

#### 3.2.2 双探测活机制（蓝图 5.4）— 🟡 中影响

**旧设计：**
```
# 发送 initialize，如果成功回复则确认存活
# 使用 Mcp-Session-Id 保持连接状态
```

**新设计：**
```
# 方法1：发送 server/discover（如服务器支持新协议）
# 方法2：直接发送一个轻量查询（如服务器仅支持旧协议，fallback）
# 每次探测独立，不维护连接状态
```

#### 3.2.3 异步事件与长任务（蓝图 5.14）— 🟡 中影响

蓝图当前设计为自定义异步任务机制。MCP 新 **Tasks 扩展** 提供了官方方案：

- `io.modelcontextprotocol/tasks` 扩展
- `tasks/get` 获取状态（轮询）
- `tasks/cancel` 取消任务
- 不再有 `tasks/result` / `tasks/list`

**建议：**
- MVP 阶段可继续使用自定义异步任务（避免依赖尚未稳定的新 Tasks 扩展）
- V2 阶段考虑迁移到官方 Tasks 扩展
- 在 V2 预留接口中规划 `core/tasks/` 目录占位

#### 3.2.4 工具 Schema 定义（蓝图 5.17 / 3.8）— 🟡 中影响

**变更：** Schema 升级到 JSON Schema 2020-12，支持 `oneOf`/`anyOf`/`$ref`

**影响：**
- `ToolDef` 模型的 `input_schema` 字段定义需放宽限制，支持更丰富的 Schema 结构
- Schema 校验器需升级以支持 2020-12 语法
- **MVP 阶段可按需支持**，2020-12 是向后兼容的超集

#### 3.2.5 权限确认（蓝图 5.12）— 🟡 中影响

**机遇：**
- 新规范的 OAuth 2.0 / OIDC 强化授权，可直接对接 Entra / Okta 等企业 IdP
- EMA 扩展（V2 规划）可能简化企业管理权限需求
- `iss` 验证机制可直接与蓝图中的 API Key 加密体系结合

**建议：**
- MVP 暂不接入 OAuth 2.0，保持现有 API Key + `config_runtime` 加密方案
- V2 阶段利用新协议的原生 OAuth 支持，替代自定义鉴权

#### 3.2.6 模型适配与版本协商 — 🟢 低影响

新协议的版本协商机制与蓝图的 ManualRouter 不冲突。MCP 传输层的变更对模型适配器透明。

---

## 四、架构调整建议

### 4.1 调整原则

1. **MVP 不做大规模重构**：MCP Client 的协议版本协商设计为向后兼容，MVP 可优先实现 2025-11-25 兼容，再扩展到 2026-07-28
2. **协议版本协商是新增而非替换**：旧版 MCP 服务器至少在 12 个月内仍然可用
3. **渐进采用**：优先吸收对架构影响最小的变更（如 HTTP 头路由、缓存字段），推迟对依赖未稳定扩展（Tasks/EMA）的采用

### 4.2 具体调整项

#### 调整一：MCP Client 双协议版本支持

**当前蓝图设计（5.3）：** `core/mcp_client.py`

**调整为：**

```
class MCPClient:
    # 协议版本协商
    async def negotiate_version(self, transport) -> ProtocolVersion:
        # auto 模式：尝试 server/discover
        # 成功 → 2026-07-28（现代化模式）
        # 失败 → initialize（回退旧模式）
        # 或固定模式直接指定
    
    # 统一调用接口，内部按版本分发
    async def call_tool(self, name, args, context) -> ToolResult:
        # 根据 negotiated_version 选择：
        #   - old: 注入 Mcp-Session-Id + 走旧流程
        #   - new: 注入 _meta + Mcp-Method 头 + 无会话
    
    # MRTR 处理（仅新协议）
    async def _handle_mrtr(self, input_required: InputRequiredResult) -> ToolResult:
        # 暂停 → 推送 WS 事件 → 等待用户输入 → 重新发送
```

**影响范围：** `core/mcp_client.py`、`core/transport/`、`core/context.py`（携带 _meta 信息）

#### 调整二：传输层适配

**新增 `core/transport/streamable_http.py`：**

- 实现无会话的 Streamable HTTP 传输
- 支持 `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name` 请求头
- 实现 `ttlMs` / `cacheScope` 缓存语义
- 支持 `subscriptions/listen` 变更通知
- 兼容 W3C Trace Context 传播

**修改 `core/transport/stdio.py`：**

- Stdio 传输不受会话影响，但版本协商逻辑需对齐
- 新增 `server/discover` 支持（stdio 可通过子进程探测）

#### 调整三：工具描述规范升级

**修改 `core/schema/tool_def.py`：**

```python
@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict  # 升级为支持 JSON Schema 2020-12
    output_schema: dict | None = None  # 新增：输出 Schema（JSON Schema 2020-12）
```

#### 调整四：权限模块预留 EMA 接口

**新增 `core/auth/` 目录占位（V2 预留）：**

- 定义 `AuthProtocol` 抽象基类
- 预留 OAuth 2.0 / OIDC 适配器接口
- MVP 阶段保持现有 `config_runtime` API Key 加密方案

#### 调整五：异步任务模块预留 Tasks 扩展兼容层

**修改 `core/async_tasks.py`：**

- MVP 保持现有自定义实现
- 新增 `TasksExtensionAdapter` 抽象层
- V2 可替换为 MCP Tasks 扩展实现

#### 调整六：config.yaml 配置段调整

在 `config.yaml` 的工具层配置段（9.13 / 第 5 段）新增 MCP 版本协商相关配置：

```yaml
mcp:
  protocol_version: "auto"          # "auto" | "2026-07-28" | "2025-11-25" [runtime]
  cache_ttl_ms: 30000               # 工具列表缓存 TTL（新协议 ttlMs）[runtime]
  enable_server_discover: true      # 是否启用 server/discover [runtime]
```

---

## 五、分阶段执行建议

### 5.1 MVP 阶段（M0-M4）

| 子项 | 优先级 | 建议 |
|---|---|---|
| MCP Client 双协议 | **P0** | M0 优先实现 2025-11-25 兼容，确保可用；M2 能力层阶段加入 2026-07-28 支持 |
| 传输层 HTTP 头 | **P1** | M2 工具层阶段加入 `Mcp-Method` / `Mcp-Name` 支持 |
| 工具 Schema 升级 | **P2** | M2 阶段按需支持 JSON Schema 2020-12，先兼容后增强 |
| MRTR 支持 | **P2** | M2 阶段实现，替代蓝图原有中间输入确认机制 |
| 缓存字段 | **P3** | 低优先级，M4 评估阶段按需加入 |
| Tasks 扩展 | **V2** | MVP 不实现，使用现有自定义方案 |

### 5.2 V2 阶段建议

| 子项 | 优先级 | 建议 |
|---|---|---|
| 完全切换 2026-07-28 | P1 | 待 MCP v2 SDK 稳定后，废弃旧协议支持 |
| EMA 企业授权 | P2 | 如有多用户需求，替代当前 API Key 方案 |
| MCP Apps 扩展 | P2 | 替代自定义 UI 渲染方案 |
| Tasks 扩展迁移 | P3 | 替代自定义异步任务 |
| CIMD 迁移 | P3 | 替代 DCR 注册流程 |

---

## 六、参考资源

- [MCP 2026-07-28 官方发布公告](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)
- [MCP 规范仓库 - GitHub](https://github.com/modelcontextprotocol/modelcontextprotocol)
- [Python SDK v2.0.0rc1 迁移指南](https://github.com/modelcontextprotocol/python-sdk)
- [TypeScript SDK v2 迁移指南](https://ts.sdk.modelcontextprotocol.io/v2/migration/support-2026-07-28.html)
- [SEP-2575: 无状态协议核心](https://modelcontextprotocol.io/community/sep-guidelines)
- [XenoSpectrum 深度分析](https://xenospectrum.com/en/mcp-2026-stateless-release/)

---

## 附录：蓝图章节变更索引

| 蓝图文件章节 | 变更类型 | 调整建议编号 |
|---|---|---|
| `2.3 前后端通信协议` | 信息补充 | 仅在文档中注明 MCP 协议版本变更 |
| `5.1 MCP 统一接口` | 代码修改 | 调整一：双协议版本支持 |
| `5.2 双轨工具架构` | 无变更 | — |
| `5.3 MCP Client` | **代码重写** | 调整一 |
| `5.4 MCP 双探活` | 代码修改 | 调整二 |
| `5.5 会话锁定工具集` | 无变更 | — |
| `5.6 9 类通用工具` | 无变更 | — |
| `5.7 Web 搜索工具` | 无变更 | — |
| `5.8 Calculator` | 无变更 | — |
| `5.9 Datetime` | 无变更 | — |
| `5.10 HTTP Request` | 无变更 | — |
| `5.11 code_execution` | 无变更 | — |
| `5.12 权限确认` | 信息补充 | 调整四 |
| `5.13 超时重试` | 代码修改 | `retry` 在新协议下可路由到不同实例 |
| `5.14 异步事件` | 信息补充 | 调整五 |
| `5.15 Artifact` | 无变更 | 显式句柄模式天然对齐 |
| `5.16 安全与限额` | 无变更 | — |
| `5.17 工具描述规范` | 代码修改 | 调整三 |
| `3.8 工具描述规范` | 代码修改 | 调整三 |
| `9.13 config.yaml` | 配置新增 | 调整六 |

---

*本文档应随 MCP SDK 正式版发布和项目进展持续更新。*
