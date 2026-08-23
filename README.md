# 私人智能体 (Private Agent)

个人桌面 AI Agent —— Electron + React + FastAPI + PostgreSQL 的全栈本地优先应用。

对话式 Agent 客户端，内置三种工作模式（办公 / 数据分析 / 设计），支持技能（Skill）加载、MCP 工具扩展、记忆系统、上下文压缩、权限确认、对话文档上传、生成中断等完整能力。

## 功能特性

- **三种工作模式**：办公（文档处理 · 数据分析 · 网页研究）、数据分析（数据可视化 · 统计检验 · 报告生成）、设计（HTML/React 生成 · 设计系统）
- **开放式 LLM 接入**：不预置任何 Provider，设置页动态注册任意 OpenAI 兼容模型（支持 fallback 链 + 会话级模型锁定）
- **MCP 工具扩展**：前端管理 MCP Server（stdio/SSE/HTTP），协议自动协商降级，同轮工具并行执行
- **技能系统（Skill）**：PG + 文件双源加载、Frozen Zone 锁定、会话级激活、设置页上传新技能
- **记忆系统**：对话记忆提取 + 记忆宫殿（MemPalace）语义检索接入
- **上下文工程**：ReAct 循环、压缩存档（滑动窗口 + 摘要）、Stable Zone 合并、KB 片段注入
- **对话体验**：文档上传（≤15MB）、生成中打断/停止、实时流式输出、代码沙箱执行（权限确认）
- **安全检查**：elevated 工具 WS 确认、60s 超时拒绝、画地为牢工作区
- **界面**：全中文、液体动效背景、自定义壁纸/视频背景

## 技术栈

| 层 | 技术 |
|---|---|
| 桌面壳 | Electron 30（主进程 + preload 桥接） |
| 前端 | React 18 + Vite 5 + TypeScript |
| 后端 | Python 3.10 + FastAPI + asyncpg |
| 数据库 | PostgreSQL 16 |
| Agent 框架 | ReAct 循环 + 工具注册 + Context Manager |
| 打包 | electron-builder（NSIS 安装版） |

## 目录结构

```
private-agent/
├── backend/                 # Python 后端(FastAPI + Sidecar)
│   ├── private_agent/       # 核心代码(路由/工具/记忆/上下文)
│   ├── config/              # config.yaml 配置
│   ├── skills/              # 技能文件(office/data_analysis/frontend_design)
│   ├── tests/               # pytest 测试(906 个)
│   └── .venv/               # Python 虚拟环境(本地, 不入库)
├── frontend/                # Electron + React 前端
│   ├── main/                # Electron 主进程(Sidecar 管理/窗口/更新检查)
│   ├── renderer/            # React 界面
│   ├── scripts/             # 开发启动脚本(start-dev.mjs)
│   └── package.json         # 依赖 + electron-builder 配置
├── start-desktop.bat        # 一键启动(开发模式)
├── build-electron.bat       # 打包 exe
└── private-agent-blueprint.md  # 蓝图设计文档
```

## 开发环境搭建

### 前置要求

- Node.js ≥ 18（推荐 22）
- Python 3.10
- PostgreSQL 16（端口 5432，库 `private_agent`）

### 安装与配置

```bash
# 1. 后端依赖
cd backend
python -m venv .venv
.venv/Scripts/pip install -e .
# config.yaml 已随仓库提供, 按需修改(注意 workspace_root 引用 ${WORKSPACE} 环境变量)

# 2. 环境变量(backend/.env, 不入库)
# PA_DB_PASSWORD=你的数据库密码
# PA_MASTER_KEY=你的主密钥
# PA_DEEPSEEK_API_KEY=你的 LLM Key

# 3. 前端依赖
cd ../frontend
npm install
```

### 启动（开发模式）

双击 `start-desktop.bat`，或手动：

```bash
cd frontend
npm run dev        # 仅 vite
npm start          # 完整桌面启动(node scripts/start-dev.mjs)
```

后端 Sidecar 由 Electron 主进程自动拉起（cwd 指向 backend，自动加载 .env）。

### 测试

```bash
# 后端(pytest 需加载 backend/.env)
cd backend && set -a && . ./.env && set +a && .venv/Scripts/python -m pytest tests/ --ignore=tests/test_eval_full_cycle.py

# 前端
cd frontend && npx vitest run
```

## 打包 exe

```bash
# 在普通 CMD 中运行(WorkBuddy 等监控环境可能拦截文件删除)
build-electron.bat
```

产物：`frontend/release3/Private Agent Setup <version>.exe`（NSIS 安装版）。

**轻度打包设计**：exe 只含 Electron 壳，后端复用外部目录（探测顺序 `D:\PA1.0\backend` > 项目根 `backend` > 打包资源），后端迭代无需重新打包。

## 配置说明

- **LLM Provider**：设置页 → 模型提供商（动态注册，key 存环境变量/加密存储）
- **MCP Server**：设置页 → MCP 服务（stdio/SSE/URL）
- **技能**：设置页 → 技能管理（上传 skill.yaml + system_prompt）
- **检查更新**：设置页 → 关于与更新（GitHub Releases，仓库名通过环境变量 `PA_UPDATE_REPO` 配置）

## 常见问题

- **打包版双击没反应**：Chromium sandbox 在部分 Windows 环境启动即崩，主进程已内置 `no-sandbox` 处理
- **白屏**：vite 需 `base: "./"`（已配置），否则 file:// 下资源 404
- **技能加载为空**：后端进程 cwd 必须指向 backend 目录（相对路径 `./skills` 依赖）

## License

MIT
