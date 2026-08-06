# MCP 预置模板（V1.5 项-2 连接器"开箱即用"）

> 设置 → MCP Server → "从模板添加"下拉，选中即填充表单，用户只需补充
> 凭证/URL/目录等个性化字段后保存（重启后端生效）。模板为**纯配置，
> 不含任何凭证**。

## 模板清单（后端 `api/admin.py` `_MCP_TEMPLATES`）

| id | 名称 | 类型 | 命令/URL | 需补充 |
|---|---|---|---|---|
| `fetch` | Fetch · 网页抓取 | stdio | `npx -y @modelcontextprotocol/server-fetch` | 无（配合知识库网页入库） |
| `time` | Time · 时间/时区 | stdio | `npx -y @modelcontextprotocol/server-time` | 无 |
| `filesystem` | Filesystem · 文件系统 | stdio | `npx -y @modelcontextprotocol/server-filesystem <dir>` | args 中目录改为实际路径 |
| `memory` | Memory · 持久记忆 | stdio | `npx -y @modelcontextprotocol/server-memory` | 无 |
| `sequential-thinking` | Sequential Thinking | stdio | `npx -y @modelcontextprotocol/server-sequential-thinking` | 无 |
| `github` | GitHub · 仓库/Issue/PR | stdio | `npx -y @modelcontextprotocol/server-github` | env `GITHUB_TOKEN` |
| `postgres` | PostgreSQL · 数据库查询 | stdio | `npx -y @modelcontextprotocol/server-postgres <dsn>` | args 中连接串（谨慎评估暴露范围） |
| `mempalace` | Mempalace · 本地语义记忆 | stdio | 本机 `mempalace-mcp.exe` 路径 | command 改为实际安装路径 |
| `ifind` | iFind · 金融数据(Bearer 示例) | http | `https://your-ifind-mcp-endpoint/mcp` | url + Bearer token |

## 维护方式

1. 新增模板：在 `backend/private_agent/api/admin.py` 的 `_MCP_TEMPLATES` 列表
   追加条目（字段与上表一致），并同步本文件。
2. 模板字段与 `POST /admin/settings/mcp` 的 `McpServerRequest` 对应：
   `id→name`、`type`、`command`、`args`、`url`、`env`、`timeout_sec`、
   `protocol_version`。`requires` 是前端提示文案（用户需补充的字段），
   不进请求体。
3. 凭证策略：**模板永不携带 token**。`auth_token` / `env` 中的密钥由
   用户添加时填写，后端 AES-256-GCM 加密存 `auth_token_encrypted`，
   `env` 明文存 config_runtime（stdio 子进程注入用，请勿放高敏密钥）。

## 一键实例化流程（前端）

1. 设置 → 连接器 → "+ 添加 MCP Server" → "表单填写"
2. "从模板"下拉选择目标模板 → 表单自动填充
3. 按黄色提示补填凭证/路径 → 添加 → 重启后端生效

> 注：前端填充方案复用现有 `POST /admin/settings/mcp`（无需改协议）；
> 若后续需要纯 API 侧实例化，可加 `POST /admin/mcp/servers {from_template}`。
