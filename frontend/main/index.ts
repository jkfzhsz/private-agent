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
import { checkForUpdates, downloadUpdate, installUpdate } from "./updater";

// 2026-08-06: 显式统一 Electron userData 路径(%APPDATA%\Private Agent)。
// package.json "name" 与 electron-builder "productName" 不一致 → Electron
// 默认 userData = %APPDATA%\private-agent-frontend, 与后端 _user_env_path()
// 的 %APPDATA%\Private Agent\backend.env 不一致 → 后端能找到 token, 前端
// preload 取不到 → 401。setPath 必须在 app.ready 之前调用(此处在文件顶层)。
if (!process.env.PA_USER_DATA_PATH_OVERRIDE) {
  app.setPath("userData", join(app.getPath("appData"), "Private Agent"));
}

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

/** 窗口创建后异步弹出配置提示(不阻塞主进程, 窗口先出来再弹)。
 *
 * 2026-08-06: 仅检查 PA_DB_PASSWORD(数据库连接必需, 缺失时后端连不上库)。
 * provider API Key 不再检查 —— V1.4 去预置化后 Key 存 DB(config_runtime,
 * AES 加密), 由后端启动时 _restore_keys_from_runtime() 恢复, main 进程
 * 从不加载 PA_*_API_KEY, 检查必然误报。配置引导指向设置页数据库卡片。
 */
function notifyMissingEnvAsync(): void {
  if (process.env.PA_DB_PASSWORD) return;
  // 异步弹窗: 不阻塞 createWindow 后流程, 窗口先正常显示
  setTimeout(() => {
    void dialog.showMessageBox({
      type: "warning",
      title: "私人智能体 - 首次配置提示",
      message: "尚未配置数据库连接(PA_DB_PASSWORD)",
      detail:
        "请打开 设置 → 🗄️ 数据库, 填写 PostgreSQL 密码并保存, 重启应用生效。\n" +
        "配置持久化于 %APPDATA%\\Private Agent\\backend.env, 升级/重装不丢。\n" +
        "LLM API Key 请在 设置 → 模型提供商 中配置(加密存库)。",
    });
  }, 500);
}

async function bootstrap(): Promise<void> {
  console.log("[main] bootstrap start");
  // 1) 加载环境配置(PA_DB_PASSWORD / PA_*_API_KEY 等, 供 Sidecar 继承)
  //    V1.5 项-6 打包收敛(方案 A): 打包版 backend 内置在
  //    resourcesPath/backend(extraResources, 自包含, 不含 .env);
  //    开发模式在项目根 backend/。
  //    用户可写配置优先: %APPDATA%/Private Agent/backend.env(打包版
  //    resourcesPath 只读, 配置请写这里) > backend/.env(项目/开发)。
  const packaged = app.isPackaged;
  const backendDir = packaged
    ? join(process.resourcesPath, "backend")
    : join(__dirname, "..", "..", "backend");
  // 用户可写配置(打包版必选路径; 开发模式可选)
  const userEnv = join(app.getPath("userData"), "backend.env");
  if (existsSync(userEnv)) {
    loadDotEnv(userEnv); // 先加载, 后加载的 backend/.env 不会覆盖已存在 key
  }
  if (!existsSync(backendDir)) {
    console.error(`[main] backend dir not found: ${backendDir}`);
  }
  loadDotEnv(join(backendDir, ".env"));

  // 2) 读取 Sidecar 配置并启动后端
  const config = loadSidecarConfig();
  console.log(`[main] Sidecar config: python=${config.pythonCommand} port=${config.port}`);
  // cwd 必须指向 backend 目录: config.yaml 的 skills.storage.dev_dir="./skills"
  // 等相对路径基于 cwd 解析, 否则技能加载为空(skill not found)。
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
  // PA_ADMIN_TOKEN(写入用户配置 backend.env / backend/.env)。loadDotEnv
  // 发生在此前, 首启时 token 尚未生成; sidecar start 后重新读取并注入
  // preload 可访问的 env。2026-08-06: 优先读用户配置 userEnv(打包版
  // backend/.env 只读, token 持久化在用户配置), 回退 backend/.env(dev)。
  if (!process.env.PA_ADMIN_TOKEN) {
    try {
      const envText = readFileSync(userEnv, "utf-8");
      const match = envText.match(/^PA_ADMIN_TOKEN=(.+)$/m);
      if (match) process.env.PA_ADMIN_TOKEN = match[1].trim();
    } catch {
      // 用户配置不可读时, 回退读 backend/.env(dev 场景)
      try {
        const envText = readFileSync(join(backendDir, ".env"), "utf-8");
        const match = envText.match(/^PA_ADMIN_TOKEN=(.+)$/m);
        if (match) process.env.PA_ADMIN_TOKEN = match[1].trim();
      } catch {
        // .env 不可读时跳过(token 由后端校验逻辑返回 401)
      }
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

  // 2026-08-06: 应用内一键升级 —— 下载安装器(进度推送) → 静默安装
  ipcMain.handle("app:download-update", async (event, asset) => {
    try {
      const onProgress = (received: number, total: number, percent: number): void => {
        event.sender.send("update:progress", { received, total, percent });
      };
      const result = await downloadUpdate(asset, onProgress);
      return { ...result, error: undefined };
    } catch (e) {
      return { path: "", size: 0, sha256: "", error: String(e) };
    }
  });

  ipcMain.handle("app:install-update", async (_event, installerPath: string) => {
    try {
      installUpdate(installerPath);
      return { ok: true };
    } catch (e) {
      return { ok: false, error: String(e) };
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
