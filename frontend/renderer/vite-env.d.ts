// renderer 全局类型声明: preload 桥接的 window.pa API + 环境类型
/// <reference types="vite/client" />

interface Window {
  pa?: {
    platform?: string;
    versions?: {
      electron?: string;
      chrome?: string;
      node?: string;
      app?: string;
    };
    sidecar?: {
      host: string;
      port: number;
      baseUrl: string;
      wsUrl: string;
    };
    envStatus?: {
      dbPassword?: boolean;
    };
    checkForUpdates?: () => Promise<unknown>;
    // 2026-08-06: 应用内一键升级(下载进度 → 静默安装重启)
    downloadUpdate?: (asset: {
      url: string;
      name: string;
      sha256?: string;
    }) => Promise<{ path: string; size: number; sha256: string; error?: string }>;
    installUpdate?: (installerPath: string) => Promise<{ ok: boolean; error?: string }>;
    onUpdateProgress?: (
      cb: (p: { received: number; total: number; percent: number }) => void
    ) => () => void;
    // 阶段二批次 1: admin 控制面鉴权 token(Electron 主进程从 backend/.env 注入)
    adminToken?: string;
    // 2026-08-08: 工作区目录选择(原生目录选择器; 非 Electron 环境不存在)
    pickDirectory?: () => Promise<string | null>;
  };
}
