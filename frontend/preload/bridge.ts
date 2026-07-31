// M1 Phase 5 - WS API 封装 (蓝图 §2.15 contextBridge)
//
// 双环境支持:
// 1. Electron preload 环境:通过 contextBridge 暴露到 window.electronAPI
// 2. 浏览器环境:直接使用 WebSocket,挂载到 window.electronAPI 作 fallback
//
// 暴露 API:
// - wsConnect(url: string): void          建立连接
// - wsSend(msg: object): void             发送消息
// - wsOnMessage(cb: (msg) => void): void  注册消息回调
// - wsOnStatus(cb: (status) => void): void 注册状态回调
// - wsClose(): void                       关闭连接

type MessageCallback = (msg: Record<string, unknown>) => void;
type StatusCallback = (status: "connected" | "disconnected" | "reconnecting") => void;

interface ElectronAPI {
  wsConnect: (url: string) => void;
  wsSend: (msg: Record<string, unknown>) => void;
  wsOnMessage: (cb: MessageCallback) => void;
  wsOnStatus: (cb: StatusCallback) => void;
  wsClose: () => void;
}

// ──────────────────────────────────────────────────────────────────────────────
// WSClient:浏览器环境 WebSocket 封装(含指数退避重连)
// ──────────────────────────────────────────────────────────────────────────────

const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000];
const MAX_RECONNECT_DELAY = 16000;

class WSClient {
  private ws: WebSocket | null = null;
  private url: string = "";
  private messageCallbacks: MessageCallback[] = [];
  private statusCallbacks: StatusCallback[] = [];
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private manualClose = false;

  connect(url: string): void {
    this.url = url;
    this.manualClose = false;
    this.reconnectAttempt = 0;
    this._doConnect();
  }

  private _doConnect(): void {
    if (this.manualClose) return;
    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.reconnectAttempt = 0;
        this._notifyStatus("connected");
      };

      this.ws.onmessage = (ev: MessageEvent) => {
        try {
          const msg = JSON.parse(ev.data);
          this.messageCallbacks.forEach((cb) => cb(msg));
        } catch {
          // 忽略非 JSON
        }
      };

      this.ws.onerror = () => {
        // onclose 会触发重连
      };

      this.ws.onclose = () => {
        this.ws = null;
        if (this.manualClose) {
          this._notifyStatus("disconnected");
          return;
        }
        this._notifyStatus("reconnecting");
        this._scheduleReconnect();
      };
    } catch {
      this._notifyStatus("reconnecting");
      this._scheduleReconnect();
    }
  }

  private _scheduleReconnect(): void {
    if (this.manualClose) return;
    const attempt = this.reconnectAttempt;
    const delay = RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)];
    const actualDelay = attempt >= RECONNECT_DELAYS.length ? MAX_RECONNECT_DELAY : delay;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectAttempt += 1;
      this._doConnect();
    }, actualDelay);
  }

  send(msg: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  onMessage(cb: MessageCallback): void {
    this.messageCallbacks.push(cb);
  }

  onStatus(cb: StatusCallback): void {
    this.statusCallbacks.push(cb);
  }

  private _notifyStatus(status: "connected" | "disconnected" | "reconnecting"): void {
    this.statusCallbacks.forEach((cb) => cb(status));
  }

  close(): void {
    this.manualClose = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this._notifyStatus("disconnected");
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// 浏览器环境 fallback:创建单例并挂载到 window
// ──────────────────────────────────────────────────────────────────────────────

function createBrowserAPI(): ElectronAPI {
  const client = new WSClient();
  return {
    wsConnect: (url: string) => client.connect(url),
    wsSend: (msg: Record<string, unknown>) => client.send(msg),
    wsOnMessage: (cb: MessageCallback) => client.onMessage(cb),
    wsOnStatus: (cb: StatusCallback) => client.onStatus(cb),
    wsClose: () => client.close(),
  };
}

// ──────────────────────────────────────────────────────────────────────────────
// Electron preload:contextBridge 暴露 / 浏览器 fallback
// ──────────────────────────────────────────────────────────────────────────────

declare global {
  interface Window {
    electronAPI?: ElectronAPI;
  }
}

// 检测是否在 Electron preload 环境
const isElectronPreload =
  typeof process !== "undefined" &&
  process.versions != null &&
  typeof process.versions.electron === "string";

if (isElectronPreload) {
  // Electron preload 环境:通过 contextBridge 暴露
  // 动态 require 避免浏览器环境报错
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { contextBridge } = require("electron");
  const browserAPI = createBrowserAPI();
  contextBridge.exposeInMainWorld("electronAPI", browserAPI);
} else {
  // 浏览器环境:直接挂载到 window
  if (typeof window !== "undefined" && !window.electronAPI) {
    window.electronAPI = createBrowserAPI();
  }
}

export {};
