// Phase 1 Task 13 - Electron 主进程入口(蓝图 §2.15)
//
// 流程:whenReady → 加载 backend/.env(可选) → loadSidecarConfig →
// SidecarManager.start(拉起 Python Sidecar) → waitForHealth → createWindow;
// 退出时停止 Sidecar。
import { app, BrowserWindow, dialog } from "electron";
import { existsSync, readFileSync } from "fs";
import { join } from "path";
import { loadSidecarConfig } from "./config-loader";
import { SidecarManager } from "./sidecar";
import { createWindow } from "./window";

let sidecarManager: SidecarManager | null = null;

// 无 GPU/远程桌面环境兜底: 禁用硬件加速与 GPU 进程, 避免崩溃导致窗口无法创建
// (本地桌面应用此设置无感知, 渲染走软件合成)
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-gpu");

/** 轻量 .env 解析: 将 backend/.env 的 KEY=VALUE 并入 process.env(已存在的优先保留)。 */
function loadDotEnv(filePath: string): void {
  if (!existsSync(filePath)) return;
  const content = readFileSync(filePath, "utf-8");
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const idx = line.indexOf("=");
    if (idx <= 0) continue;
    const key = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim();
    if (key && !(key in process.env)) {
      process.env[key] = value;
    }
  }
}

async function bootstrap(): Promise<void> {
  // 1) 加载 backend/.env(PA_DB_PASSWORD / PA_*_API_KEY 等, 供 Sidecar 继承)
  loadDotEnv(join(__dirname, "..", "..", "backend", ".env"));

  // 2) 读取 Sidecar 配置并启动后端
  const config = loadSidecarConfig();
  sidecarManager = new SidecarManager({
    pythonCommand: config.pythonCommand,
    moduleName: config.moduleName,
    port: config.port,
    healthUrl: `http://127.0.0.1:${config.port}/health`,
  });
  await sidecarManager.start();

  // 3) 关键配置缺失时提示(不阻塞启动, 聊天时会给出明确错误)
  const missing: string[] = [];
  if (!process.env.PA_DB_PASSWORD) missing.push("PA_DB_PASSWORD");
  if (!process.env.PA_DEEPSEEK_API_KEY && !process.env.PA_GLM_API_KEY && !process.env.PA_KIMI_API_KEY) {
    missing.push("PA_*_API_KEY(任一)");
  }
  if (missing.length > 0) {
    dialog.showMessageBoxSync({
      type: "warning",
      title: "Private Agent - 配置提示",
      message: `缺少环境配置: ${missing.join(", ")}`,
      detail:
        "可将配置写入 backend/.env(KEY=VALUE, 每行一个), 或在本终端设置环境变量后重新启动。\n" +
        "未配置时聊天会返回模型不可用提示。",
    });
  }

  createWindow();
}

app.whenReady().then(() => {
  bootstrap().catch((err: unknown) => {
    console.error("Failed to start sidecar:", err);
    dialog.showMessageBoxSync({
      type: "error",
      title: "Private Agent - 启动失败",
      message: "后端 Sidecar 启动失败",
      detail: String(err),
    });
    app.quit();
  });
});

app.on("before-quit", () => {
  void sidecarManager?.stop();
});

app.on("window-all-closed", () => {
  app.quit();
});

// 不退出托盘: 全部窗口关闭即退出(本地单人应用)
