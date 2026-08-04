// 阶段二批次 1 - admin 控制面 API 客户端统一封装
//
// 所有 /admin*、/files* 请求必须经 adminFetch, 自动携带 X-Admin-Token:
// - Electron 生产: window.pa.adminToken(preload 从 backend/.env 注入)
// - 浏览器 dev(vite): localStorage "pa_admin_token"(设置页可录入)
//
// 401 时派发 pa:auth-required 事件, 供设置页弹提示引导录入 token。

const ADMIN_HEADER = "X-Admin-Token";

export function getAdminToken(): string {
  const fromBridge = window.pa?.adminToken;
  if (fromBridge) return fromBridge;
  return localStorage.getItem("pa_admin_token") ?? "";
}

export function setAdminToken(token: string): void {
  localStorage.setItem("pa_admin_token", token.trim());
}

export function isAdminTokenConfigured(): boolean {
  return getAdminToken().length > 0;
}

export async function adminFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (!headers.has(ADMIN_HEADER)) {
    const token = getAdminToken();
    if (token) headers.set(ADMIN_HEADER, token);
  }
  const resp = await fetch(input, { ...init, headers });
  if (resp.status === 401) {
    // 通知 UI(设置页监听后提示录入 token)
    window.dispatchEvent(new CustomEvent("pa:auth-required"));
  }
  return resp;
}
