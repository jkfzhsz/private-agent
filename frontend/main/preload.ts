// Phase 1 Task 13 - Electron preload 桥接(蓝图 §2.15 B2.1)
//
// contextIsolation 下通过 contextBridge 向渲染进程暴露最小安全 API:
// - 平台/版本信息
// - 后端 Sidecar 地址(与 config.yaml 一致)
// - 关键环境变量的"是否已配置"状态(不暴露 key 值本身)
import { contextBridge } from "electron";

const sidecarPort = Number(process.env.PA_SIDECAR_PORT) || 8765;
const sidecarHost = "127.0.0.1";

const api = {
  platform: process.platform,
  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
  },
  sidecar: {
    host: sidecarHost,
    port: sidecarPort,
    baseUrl: `http://${sidecarHost}:${sidecarPort}`,
    wsUrl: `ws://${sidecarHost}:${sidecarPort}/ws`,
  },
  envStatus: {
    dbPassword: Boolean(process.env.PA_DB_PASSWORD),
    deepseekKey: Boolean(process.env.PA_DEEPSEEK_API_KEY),
    glmKey: Boolean(process.env.PA_GLM_API_KEY),
    kimiKey: Boolean(process.env.PA_KIMI_API_KEY),
  },
};

contextBridge.exposeInMainWorld("pa", api);

export type PaApi = typeof api;
