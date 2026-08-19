# 发布与升级流程（2026-08-06）

正式版（本地终端）与施工文件夹（`D:\Private agent` 开发工作区）**彻底分离**：
施工文件夹只负责开发与打包发布，正式版通过**应用内更新**升级，互不干扰。

## 数据与配置的独立线路

| 数据 | 位置 | 升级影响 |
|---|---|---|
| 用户配置/密钥/技能/MCP/LLM | `%APPDATA%\Private Agent\backend.env` + DB `config_runtime` | **不丢**（NSIS 覆盖安装不动 `%APPDATA%`） |
| PostgreSQL 数据库 | 正式版独立库（设置页配置 `PA_DB_NAME`，如 `private_agent_app`；`build_dsn` 优先读 `PA_DB_*` 环境变量） | **不丢**（库在 PG 服务器上，与安装无关） |
| mempalace 记忆 | mempalace 自身数据目录（MCP server 独立管理） | **不丢** |
| 对话/记忆/知识库 | PostgreSQL `messages`/`react_events`/`kb_chunks` 等 | **不丢** |
| 应用程序本体 | `%LOCALAPPDATA%\Programs\Private Agent`（NSIS 默认） | 升级时被覆盖替换 |

**关键点**：正式版首次配置一次（数据库卡片填密码保存），之后**每次升级都不需要重新配置** —— 密钥已持久化到 `%APPDATA%`，DB 参数走 `PA_DB_*` env。

## 发布新版本（施工文件夹操作）

```bash
# 1. 升版本号
#    修改 frontend/package.json 的 "version"（如 0.2.0 → 0.3.0）

# 2. 打包（生成 release2/Private Agent Setup X.Y.Z.exe）
#    双击 build-electron.bat

# 3. 脱敏检查 + 上传 GitHub Release（自动打 tag vX.Y.Z）
node scripts/publish-release.mjs            # 用 gh CLI（推荐，已登录）
# 或
GITHUB_TOKEN=xxx node scripts/publish-release.mjs
# 先试运行（只检查不上传）:
node scripts/publish-release.mjs --dry-run
```

发布脚本 `scripts/publish-release.mjs` 会自动：
1. 定位 `release2/Private Agent Setup {version}.exe`
2. **脱敏检查**：确认产物无 `.env`/密钥文件/测试代码/敏感关键字，有则中止
3. `git tag v{version}` + push
4. `gh release create`（或 GitHub API）上传安装包，填更新说明

## 正式版升级（本地终端操作）

设置 → **关于与更新** → 检查更新：
1. 发现新版本（GitHub Releases 对比版本号）
2. 点 **下载更新**（进度条，下载完成 sha256 校验）
3. 点 **安装并重启** → 静默安装 → 自动启动新版本

**升级保留**：数据库、记忆、密钥、技能、MCP、LLM 配置全部保留，无需重新配置。

## 更新源配置

- 默认 GitHub 仓库：`jkfzhsz/private-agent`（与 `git remote origin` 一致）
- 环境变量 `PA_UPDATE_REPO`（`owner/repo`）可覆盖（私有源/镜像场景）

## 注意事项

- 下载走 GitHub 直连，国内网络可能慢（可设 `PA_UPDATE_REPO` 指向内网/镜像仓库）
- 更新安装包只含构建产物（不含任何 `.env`/密钥），脱敏检查在上传前强制拦截
- 若 Release 已存在同名 tag，`gh release create` 会覆盖（--force）；API 方式需先删旧 Release
