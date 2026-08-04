// Phase 1 Task 13 - Electron 主进程入口(蓝图 §2.15)
//
// 流程:whenReady → 加载 backend/.env(可选) → loadSidecarConfig →
// SidecarManager.start(拉起 Python Sidecar) → waitForHealth → createWindow;
// 退出时停止 Sidecar。
import { app, BrowserWindow, dialog, ipcMain } from "electron";
import { existsSync, readFileSync } from "fs";
import { join } from "path";
import { loadSidecarConfig } from "./config-loader";
import { SidecarManager } from "./sidecar";
import { createWindow } from "./window";
import { checkForUpdates } from "./updater";

let sidecarManager: SidecarManager | null = null;
// 保存主窗口引用: Electron BrowserWindow 若无强引用会被 V8 GC 回收,
// 导致窗口被销毁并触发 window-all-closed → app.quit
let mainWindow: BrowserWindow | null = null;

// GPU 策略: 默认启用硬件加速(桌面流畅运行液体动效/玻璃模糊的关键)。
// 仅当显式设置 PA_DISABLE_GPU=1 时禁用(无 GPU/远程桌面/沙箱验证场景)。
if (process.env.PA_DISABLE_GPU === "1") {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu");
}

// Windows 普通用户/受限 token 环境下 Chromium 进程 sandbox 启动即崩
// (退出码 2147483651, STATUS_BREAKPOINT)。本地单机个人应用, 安全模型
// 靠工具权限确认而非进程 sandbox, 故默认禁用(打包版 exe 也必须带上)。
app.commandLine.appendSwitch("no-sandbox");

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

/** 窗口创建后异步弹出配置提示(不阻塞主进程, 窗口先出来再弹)。 */
function notifyMissingEnvAsync(): void {
  const missing: string[] = [];
  if (!process.env.PA_DB_PASSWORD) missing.push("PA_DB_PASSWORD");
  if (
    !process.env.PA_DEEPSEEK_API_KEY &&
    !process.env.PA_GLM_API_KEY &&
    !process.env.PA_KIMI_API_KEY
  ) {
    missing.push("PA_*_API_KEY(任一)");
  }
  if (missing.length === 0) return;
  // 异步弹窗: 不阻塞 createWindow 后流程, 窗口先正常显示
  setTimeout(() => {
    void dialog.showMessageBox({
      type: "warning",
      title: "私人智能体 - 配置提示",
      message: `缺少环境配置: ${missing.join(", ")}`,
      detail:
        "可将配置写入 backend/.env(KEY=VALUE, 每行一个), 或在本终端设置环境变量后重新启动。\n" +
        "未配置时聊天会返回模型不可用提示。",
    });
  }, 500);
}

async function bootstrap(): Promise<void> {
  console.log("[main] bootstrap start");
  // 1) 加载 backend/.env(PA_DB_PASSWORD / PA_*_API_KEY 等, 供 Sidecar 继承)
  //    打包后 backend 在 resourcesPath/backend(只读, 仅读配置);
  //    开发模式在项目根 backend/
  const packaged = app.isPackaged;
  // 轻度打包: exe 只含 Electron 壳, 后端复用 backend/ 目录
  // 探测顺序: D:\PA1.0\backend(部署自包含) > D:\Private agent\backend(项目) > resourcesPath
  const devBackend = join(__dirname, "..", "..", "backend");
  const deployBackend = "D:\\PA1.0\\backend";
  const projBackend = "D:\\Private agent\\backend";
  const backendDir = existsSync(deployBackend)
    ? deployBackend
    : existsSync(projBackend)
      ? projBackend
      : packaged
        ? join(process.resourcesPath, "backend")
        : devBackend;
  if (!existsSync(backendDir)) {
    console.error(`[main] backend dir not found: ${backendDir}`);
  }
  loadDotEnv(join(backendDir, ".env"));

  // 2) 读取 Sidecar 配置并启动后端
  const config = loadSidecarConfig();
  console.log(`[main] Sidecar config: python=${config.pythonCommand} port=${config.port}`);
  // cwd 必须指向 backend 目录: config.yaml 的 skills.storage.dev_dir="./skills"
  // 等相对路径基于 cwd 解析, 否则技能加载为空(skill not found)。
  // 轻度打包后端在外部可写目录(D:\PA1.0\backend / D:\Private agent\backend),
  // cwd 与 WORKSPACE 都指向 backendDir, outputs/logs/skills 均正常落盘。
  console.log(`[main] backend dir: ${backendDir}`);
  sidecarManager = new SidecarManager({
    pythonCommand: config.pythonCommand,
    moduleName: config.moduleName,
    port: config.port,
    healthUrl: `http://127.0.0.1:${config.port}/health`,
    // 注入 WORKSPACE: 后端 config.yaml 的 workspace_root=${WORKSPACE},
    // 缺失会导致日志/产物目录错位、DB 连接异常
    env: { WORKSPACE: backendDir },
    // 工作目录 = backend 目录(相对路径 ./skills ./outputs 据此解析)
    cwd: backendDir,
  });
  await sidecarManager.start();
  console.log("[main] Sidecar health OK");

  // 阶段二批次 1: 补读 sidecar 首次启动时 ensure_admin_token 生成的
  // PA_ADMIN_TOKEN(写入 backend/.env)。loadDotEnv 发生在此前, 首启时
  // token 尚未生成; sidecar start 后重新读取并注入 preload 可访问的 env。
  if (!process.env.PA_ADMIN_TOKEN) {
    try {
      const envText = readFileSync(join(backendDir, ".env"), "utf-8");
      const match = envText.match(/^PA_ADMIN_TOKEN=(.+)$/m);
      if (match) process.env.PA_ADMIN_TOKEN = match[1].trim();
    } catch {
      // .env 不可读时跳过(token 由后端校验逻辑返回 401)
    }
  }

  // 3) 创建主窗口(优先于弹窗, 确保窗口先稳定显示)
  console.log("[main] creating window ...");
  mainWindow = createWindow();
  mainWindow.on("ready-to-show", () => {
    console.log("[main] window ready-to-show");
    mainWindow?.show();
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
  console.log("[main] window created");

  // 4) 关键配置缺失时异步提示(不阻塞主进程)
  notifyMissingEnvAsync();
}

app.whenReady().then(() => {
  // 检查更新 IPC(渲染进程"设置-检查更新"调用)
  ipcMain.handle("app:check-updates", async () => {
    try {
      return await checkForUpdates();
    } catch (e) {
      return { hasUpdate: false, currentVersion: "", latestVersion: "", releaseUrl: "", notes: String(e), failed: true };
    }
  });

  bootstrap().catch((err: unknown) => {
    console.error("[main] Failed to start sidecar:", err);
    void dialog.showMessageBox({
      type: "error",
      title: "私人智能体 - 启动失败",
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
  // 所有窗口关闭时退出(本地单人应用, 不留托盘)
  if (process.env.PA_KEEP_RUNNING === "1") return; // 可选: 调试时保活
  app.quit();
});
