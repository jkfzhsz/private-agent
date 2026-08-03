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
      deepseekKey?: boolean;
      glmKey?: boolean;
      kimiKey?: boolean;
    };
    checkForUpdates?: () => Promise<unknown>;
  };
}
