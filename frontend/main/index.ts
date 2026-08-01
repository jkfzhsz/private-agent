// B2 P1-1 - Electron 主进程入口
//
// 流程:whenReady → loadSidecarConfig → SidecarManager.start(拉起 Python Sidecar) →
// waitForHealth → createWindow;退出时停止 Sidecar。
import { app, BrowserWindow } from "electron";
import { loadSidecarConfig } from "./config-loader";
import { SidecarManager } from "./sidecar";

let sidecarManager: SidecarManager | null = null;

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });
  // 开发模式加载 vite dev server,打包后加载静态文件
  win.loadURL("http://localhost:5173");
}

async function bootstrap(): Promise<void> {
  const config = loadSidecarConfig();
  sidecarManager = new SidecarManager({
    pythonCommand: config.pythonCommand,
    moduleName: config.moduleName,
    port: config.port,
    healthUrl: `http://127.0.0.1:${config.port}/health`,
  });
  await sidecarManager.start();
  createWindow();
}

app.whenReady().then(() => {
  bootstrap().catch((err: unknown) => {
    console.error("Failed to start sidecar:", err);
    app.quit();
  });
});

app.on("before-quit", () => {
  void sidecarManager?.stop();
});

app.on("window-all-closed", () => {
  app.quit();
});
