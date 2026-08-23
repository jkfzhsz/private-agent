# 去 Electron 化方案：pywebview + WebView2 替换 Electron 壳

> 版本：设计草案 v1（待蒋先生评审拍板，未实施）
> 日期：2026-08-21
> 对标依据：`D:\xingyao`（Y-CODE，PyInstaller + pywebview + 系统 WebView2 单文件应用）
> 代码证据：已实地核查 `frontend/main/*`、`frontend/renderer/*`、`frontend/package.json`

---

## 0. 可行性结论（已验证）

**结论：可行，且改动面有界、风险低。** 关键证据：

1. PA 的"4 智能体并发"是 **React 应用内面板**，`createWindow()` 只建 **1 个** BrowserWindow（`frontend/main/window.ts`）——不存在多 OS 窗口，pywebview 单窗口即可覆盖。
2. 渲染进程**零 `require('electron')`**（`renderer/` 全量 grep 仅命中 `vite-env.d.ts` 类型声明与 `main.tsx` 注释）。
3. 渲染进程已内置**浏览器模式回退**：`apiClient.ts` 在 `window.pa.adminToken` 缺失时退 `localStorage`；dev 模式本就跑 Vite + 浏览器。**前端本质就是"连本机 :8765 的 Web 应用"**。
4. 仅 **4 个源文件**用到 `window.pa` 桥（`apiClient.ts`、`App.tsx`、`SettingsView.tsx`、`vite-env.d.ts` 类型）。桥契约极小且已定义。
5. 后端（FastAPI @ :8765，含 `/health`、`/ws`、`/admin`）完全不变。

→ 迁移 = 用 Python 单进程替换"Electron(Node)+独立后端"的双进程结构，**前端源码与后端近乎零改动**。

---

## 1. 架构对比

```
【当前】                                    【目标】
Electron(Node)  ──spawn──▶ Python 后端         Python 启动器(launcher.py)
   │  (BrowserWindow 渲染 React)              ├─ spawn Python 后端(:8765，同现状)
   │                                          ├─ FastAPI 增挂 StaticFiles(/ → dist)
   └── 渲染 http://localhost:5173/dist ─┐      └─ pywebview(WebView2) → http://127.0.0.1:8765
                                        │              │
                              React 应用 ◀┘              └── React 应用(同一份，仅桥调用改)
                              (window.pa 桥)                  (window.pywebview.api 桥)

依赖面：Electron + Node + Vite 运行时 + Chromium(~150MB) + PG 服务
   ↓ 替换后
依赖面：Python(冻结) + WebView2(系统自带) + PG 服务
```

**最大附带收益**：当前 `safe-delete` shim（NODE_OPTIONS 注入 `genie-safe-delete.cjs`）专为拦截 vite/electron-builder 删 `dist/release2` 而生。去 Electron 后**该 shim 整个作废**，构建卡死的一类根因消失。

---

## 2. 迁移范围（精确清单）

| 类别 | 文件 | 改动 |
|---|---|---|
| 新增 | `launcher.py`（项目根或 `frontend/`） | Python 启动器，替代 `main/index.ts`+`window.ts`+`preload.ts` |
| 新增 | `launcher.spec`（PyInstaller） | 替代 `electron-builder` 配置 |
| 新增 | `build-pa.bat` | 替代 `build-electron.bat` 的打包步骤 |
| 修改 | `frontend/renderer/utils/apiClient.ts` | `window.pa` → 统一桥抽象（兼容 `pywebview.api`） |
| 修改 | `frontend/renderer/App.tsx` | 更新检查 / `pickDirectory` 调用改走新桥 |
| 修改 | `frontend/renderer/views/SettingsView.tsx` | 更新 UI / `envStatus` 改走新桥 |
| 修改 | `frontend/renderer/vite-env.d.ts` | 桥类型定义更新 |
| 修改 | 后端 `main.py` / 新增 `webui.py` router | 挂载 `frontend/dist` 静态文件到 `/` |
| 删除 | `frontend/main/*`、`frontend/preload/*` | Electron 主进程/预加载（职责移入 `launcher.py`） |
| 删除 | `package.json` 的 `build`(electron-builder)、`electron`/`electron-builder` devDeps | 不再需要 |
| 删除 | `start-dev.mjs` 中 Electron 相关步骤；`NODE_OPTIONS` safe-delete 注入 | 由 `launcher.py --dev` 替代 |
| 保留 | React 全部源码、FastAPI 全部源码、config.yaml、PG/DB/MCP | 不动 |

**改动量评估**：前端 1 个桥抽象 + 3 处调用点；后端 +1 个静态挂载 router；新增 1 个 Python 启动器 + 1 个 PyInstaller spec。属**中小工作量、低回归面**。

---

## 3. 新启动器设计（`launcher.py`）

职责一一对应现有 Electron 主进程：

1. **定位后端目录 + config.yaml**：移植 `config-loader.ts` 逻辑（读 `server.http.port`，默认 8765；探测 venv/python）。
2. **userData 路径**：沿用 `%APPDATA%\Private Agent`（与现有 `backend.env`/日志/上传/沙箱一致，避免数据错位）。
3. **拉起后端**：subprocess 启动 `python -m private_agent.main`，注入 `WORKSPACE`、`PA_USER_DATA`，cwd=backend，轮询 `/health`（等同现有 `SidecarManager`）。
4. **静态托管**：后端新增 router 将 `frontend/dist` 挂到 `/`（API 仍走 `/api`、`/ws`、`/admin`，无冲突）。
5. **开窗口**：`webview.create_window("私人智能体", url="http://127.0.0.1:8765", ...)`，WebView2(Edge) 渲染。
6. **桥暴露**：`class Api:` 提供 `get_runtime()`→`{baseUrl, wsUrl, adminToken, dbPasswordStatus, appVersion, platform}`、`pick_directory()`、`check_updates()`（可选）、`install_update()`（可选）。`adminToken` 由启动器读 `backend.env` 的 `PA_ADMIN_TOKEN` 注入（等同现有 preload 补读逻辑）。
7. **退出清理**：窗口关闭 → 终止后端 subprocess → 退出。
8. **WebView2 可用性兜底**：创建前检测运行时；缺失则弹系统对话框/打开官方下载页（Win11 默认已装，低风险）。

---

## 4. 桥契约迁移（`window.pa` → `window.pywebview.api`）

新增 `bridge.ts` 统一层，调用方无感：

```ts
// 统一桥：生产与 dev 共用，底层自动选 pywebview.api 或旧 pa
export async function paGetRuntime(): Promise<PaRuntime> {
  if ((window as any).pywebview?.api?.get_runtime) {
    return await (window as any).pywebview.api.get_runtime();
  }
  return fallbackFromLocalStorage(); // dev 浏览器模式兼容
}
```

- `sidecar.baseUrl/wsUrl`：固定 `http://127.0.0.1:8765` / `ws://127.0.0.1:8765/ws`（可硬编码常量，降低桥依赖）。
- `pickDirectory`：优先用 **HTML `<input webkitdirectory>`**（WebView2 原生支持文件选择），无需原生对话框；保留桥版本作兜底。
- `adminToken`：沿用现有 `localStorage` 回退，桥仅做"有则注入"。

---

## 5. 打包替换（PyInstaller 替代 electron-builder）

- `launcher.spec`：one-file 冻结 `launcher.py` + 后端 Python + 依赖（同 ycode 形态）；`frontend/dist` 作为 datas 收集进包。
- **不再需要 `venv.zip` 解压**（冻结解释器自包含，省去首启 30–60s 解压）。
- 输出：便携 `PrivateAgent.exe`（或目录式）。`build-pa.bat` 调 `pyinstaller launcher.spec`。
- **删除 NODE_OPTIONS safe-delete shim**（不再有 vite/electron-builder 删 dist 动作）。

---

## 6. 分阶段实施（建议 checkpoint 驱动）

- **Phase 0 — 可行性 Spike（~0.5 天）**：手起后端 :8765，`launcher.py` 仅用 pywebview 打开该地址，验证渲染/流式/玻璃特效/WS 正常。产出：可行性签字。
- **Phase 1 — 后端静态托管 + 桥抽象（~1 天）**：后端加 `webui` router；`bridge.ts` 统一层 + 改 3 处调用点。Electron 仍可在，A/B 并存。
- **Phase 2 — 启动器接管（~1 天）**：`launcher.py` 完整实现；dev 模式 `launcher.py --dev` 指向 Vite(5173)。删 Electron 主进程源码。
- **Phase 3 — 打包替换（~1 天）**：PyInstaller spec + `build-pa.bat`；删 `safe-delete` shim、electron-builder 配置与依赖。
- **Phase 4 — 收尾回归（~0.5 天）**：删 `frontend/main`、`release2`；跑 tsc 0 错 + vitest 全过 + 手动启动回归。

---

## 7. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| WebView2 运行时缺失（Win10/纯净机） | 低（Win11 默认） | 高（打不开） | 启动器检测 + 缺失时引导下载/启动 evergreen bootstrapper |
| JS 桥 async 改造回归 | 中 | 中 | 单一 bridge 抽象 + 对 4 调用点加单测 |
| 静态挂载与 API 路由冲突 | 低 | 中 | UI 挂 `/`，API 走 `/api`·`/ws`·`/admin`（FastAPI 已知前缀，无重叠） |
| 玻璃/液态动效表现差异 | 低 | 低 | WebView2=Chromium，`backdrop-filter` 原生支持；QA 比对 |
| 失去 NSIS 一键更新 | — | 中 | 见下方"待决策" |
| 打包体积（PyInstaller one-file） | — | — | 预估 30–50MB（Python+依赖），远低于 Electron(~150MB+ Chromium) |

---

## 8. 量化收益（预期）

- **安装体积**：Electron(~150MB Chromium)+app → PyInstaller(~30–50MB)，↓ 约 70%。
- **冷启动**：无 Chromium bootstrap + 无独立 Node + **免 venv.zip 解压（冻结自包含）** → 首屏更快。
- **故障面**：Electron+Node+Chromium+sandbox(`--no-sandbox` 已禁用)+PG 服务 → 仅 Python+WebView2+PG 服务。sandbox 崩溃类彻底消失。
- **能力无损**：托盘（本就不用）、多窗口（本就单窗口）、原生截图（主进程未用）均不影响。

---

## 9. 决策项（已拍板）

1. **更新机制**：✅ **(C) 彻底去掉应用内更新**，回归手动分发（GitHub Release / 直接给 exe）。`updater.ts` 及相关 IPC、preload 更新桥、`SettingsView` 更新 UI 一并删除。
2. **打包形态**：✅ **(A) PyInstaller one-file 单 exe**（便携分发，与 ycode 同形态）。
3. **dev 是否保留 Electron**：✅ **不保留**，统一 `launcher.py --dev` 指向 Vite(5173)，删除 Electron dev 路径。
4. **WebView2 缺失兜底**：✅ **(A) 仅提示下载页**（Win11 默认已装，近乎不触发；未来大范围分发再考虑 (B)）。

---

## 11. 第 4 项白话解释（WebView2 缺失兜底）

**背景**：pywebview 在 Windows 上不是自己带浏览器，而是借用了系统里的 **WebView2（Edge 内核）** 来渲染界面。它和 Edge 浏览器用的是同一套引擎。

**好消息**：你用的是 **Windows 11**，系统出厂就预装了 WebView2，所以正常情况下 100% 可用，不需要操心。

**问题只在一种场景**：如果你把打包好的 exe 发给一台 **没装/被精简掉 WebView2 的机器**（典型是干净的 Windows 10 老机），pywebview 会"开不出窗口"，程序启动即失败。

**所谓"兜底"就是：万一遇到这种机器，程序该怎么办？**
- **(A) 仅提示下载页**（推荐）：启动时先检测 WebView2 在不在；不在就弹个提示"请安装 WebView2 运行库"并给官方下载链接，用户点一下装好即可。代价：exe 不增重，代码极简。
- **(B) 内嵌自动安装器**：把微软那个 ~1.5MB 的 WebView2 引导安装包打进 exe，启动时发现没有就**静默自动装好**。代价：exe 体积略增、安装逻辑更复杂、首次启动多一步。

**为什么对你推荐 (A)**：你的使用环境固定是 Win11（WebView2 必在），此兜底基本永不触发；且你走便携 exe 自己用/小范围分发，真遇到老机器手动装一次即可。为"几乎不会发生的事"把 exe 弄复杂、增体积，不划算。

> 结论：第 4 项建议默认 **(A)**。若你未来打算大范围分发到陌生 Windows 10 环境，再升级到 (B)。

## 12. 局限声明

- 后端 `main.py` 静态挂载细节未逐行核实（仅据已知 FastAPI 结构提议），Phase 1 实施时需确认 `/` 与现有 `/health`·`/ws`·`/admin` 无冲突。
- PyInstaller 冻结后端 Python 的实操体积与依赖收集，需 Phase 0/3 实测。
- 本方案未触及"对话流轻量化"（方案 A），仅解决 GUI 壳。两者正交，可并行。
