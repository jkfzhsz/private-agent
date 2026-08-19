// Phase 1 Task 13 - Electron preload 桥接(蓝图 §2.15 B2.1)
//
// contextIsolation 下通过 contextBridge 向渲染进程暴露最小安全 API:
// - 平台/版本信息
// - 后端 Sidecar 地址(与 config.yaml 一致)
// - 关键环境变量的"是否已配置"状态(不暴露 key 值本身)
import { contextBridge, ipcRenderer } from "electron";

const sidecarPort = Number(process.env.PA_SIDECAR_PORT) || 8765;
const sidecarHost = "127.0.0.1";

const api = {
  platform: process.platform,
  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
    app: process.env.PA_APP_VERSION ?? "",
  },
  sidecar: {
    host: sidecarHost,
    port: sidecarPort,
    baseUrl: `http://${sidecarHost}:${sidecarPort}`,
    wsUrl: `ws://${sidecarHost}:${sidecarPort}/ws`,
  },
  // 阶段二批次 1: admin 控制面鉴权 token(主进程从 backend/.env 补读注入;
  // 渲染进程经 adminFetch 自动携带 X-Admin-Token 头)
  adminToken: process.env.PA_ADMIN_TOKEN ?? "",
  // 2026-08-06: provider API Key 存 DB(config_runtime)由后端恢复,
  // main 进程不加载, 仅保留 DB 密码状态
  envStatus: {
    dbPassword: Boolean(process.env.PA_DB_PASSWORD),
  },
  // 检查更新(主进程实现, 渲染进程只拿结果)
  checkForUpdates: (): Promise<unknown> => ipcRenderer.invoke("app:check-updates"),
  // 2026-08-06: 应用内一键升级 —— 下载安装器(进度回调) → 静默安装并重启
  downloadUpdate: (asset: {
    url: string;
    name: string;
    sha256?: string;
  }): Promise<{ path: string; size: number; sha256: string; error?: string }> =>
    ipcRenderer.invoke("app:download-update", asset),
  installUpdate: (installerPath: string): Promise<{ ok: boolean; error?: string }> =>
    ipcRenderer.invoke("app:install-update", installerPath),
  onUpdateProgress: (cb: (p: { received: number; total: number; percent: number }) => void): (() => void) => {
    const listener = (_e: unknown, p: { received: number; total: number; percent: number }): void => cb(p);
    ipcRenderer.on("update:progress", listener);
    return () => ipcRenderer.removeListener("update:progress", listener);
  },
  // 2026-08-08: 工作区目录选择(渲染进程调起原生目录选择器)
  pickDirectory: (): Promise<string | null> => ipcRenderer.invoke("app:pick-directory"),
};

contextBridge.exposeInMainWorld("pa", api);

export type PaApi = typeof api;
