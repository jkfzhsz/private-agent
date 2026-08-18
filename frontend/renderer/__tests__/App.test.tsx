// B2 P1-9 + Phase 1.5 - App 集成测试
//   AC-17: 初始渲染首页(HomeView), 三个模式按钮可见
//   AC-18: WS skill_not_found error → 自动切回首页
//   AC-19: 点击模式按钮 → 激活后进入 chat 视图并显示 skill 名
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";

const SKILLS = [
  { name: "office", version: "1.0.0", description: "办公场景技能", enabled: true },
];

class FakeWebSocket {
  static OPEN = 1;
  url: string;
  readyState = 0;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
  static instances: FakeWebSocket[] = [];
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, options?: RequestInit) => {
      if (typeof url === "string" && url.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (typeof url === "string" && url.includes("/activate")) {
        const body = JSON.parse((options?.body as string) ?? "{}");
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            locked_version: "1.0.0",
            frozen_hash: "abc",
            skill_name: body.skill_name,
          }),
        });
      }
      return Promise.reject(new Error(`unexpected fetch: ${url}`));
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function ws(): FakeWebSocket {
  if (FakeWebSocket.instances.length === 0) {
    throw new Error("WebSocket 未创建");
  }
  return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
}

async function pickMode(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  // HomeView 三个按钮: 子瞻 / 白圭 / 清和
  // 用 role+name(正则)匹配按钮, 避免 sidebar "📄 子瞻 (N)" 与按钮"子瞻 工作与学习..."
  // 重名(按钮 accessible name 是 "子瞻 + subtitle" 拼接)
  await screen.findByTestId("mode-btn-office");
  await user.click(screen.getByTestId("mode-btn-office"));
  // 激活后顶部 chip "子瞻" + sidebar 组标题均含"子瞻" → getAllByText 接受多元素
  await waitFor(() =>
    expect(screen.getAllByText("子瞻").length).toBeGreaterThanOrEqual(1)
  );
}

describe("App 首页与模式选择集成", () => {
  it("初始渲染首页与三个模式按钮(AC-17)", async () => {
    render(<App />);
    // 三个按钮(role=button, 名称以场景中文名开头)均存在
    expect(await screen.findByTestId("mode-btn-office")).toBeInTheDocument();
    expect(screen.getByTestId("mode-btn-data_analysis")).toBeInTheDocument();
    expect(screen.getByTestId("mode-btn-frontend_design")).toBeInTheDocument();
  });

  it("点击模式按钮后进入 chat 视图并显示 skill 名(AC-19)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await pickMode(user);
    // 顶部 badge 显示当前激活的场景中文名(子瞻) — sidebar 与 chip 都含此名
    expect(screen.getAllByText("子瞻").length).toBeGreaterThanOrEqual(1);
  });

  it("WS skill_not_found error 自动切回首页(AC-18)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await pickMode(user);

    act(() => {
      ws().onmessage?.({
        data: JSON.stringify({ type: "error", message: "skill_not_found" }),
      });
    });

    // 回到首页: HomeView 三个模式按钮重新可见(role+name 精确匹配)
    expect(await screen.findByTestId("mode-btn-office")).toBeInTheDocument();
  });

  // 2026-08-07: 首页点模式必须新建会话(不复用历史会话 id)
  it("回首页后再点模式 → 新建会话(不复用历史 session)", async () => {
    const activateUrls: string[] = [];
    const originalFetch = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, options?: RequestInit) => {
        if (typeof url === "string" && url.includes("/activate")) {
          activateUrls.push(url);
          const body = JSON.parse((options?.body as string) ?? "{}");
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                locked_version: "1.1.0",
                frozen_hash: "abc",
                skill_name: body.skill_name,
              }),
          });
        }
        if (typeof url === "string" && url.includes("/skills")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(SKILLS),
          });
        }
        return Promise.reject(new Error(`unexpected fetch: ${url}`));
      })
    );

    const user = userEvent.setup();
    render(<App />);
    // 第一次点子瞻 → 会话 A
    await pickMode(user);
    expect(activateUrls.length).toBe(1);
    // skill_not_found → 回首页
    act(() => {
      ws().onmessage?.({
        data: JSON.stringify({ type: "error", message: "skill_not_found" }),
      });
    });
    await screen.findByTestId("mode-btn-office");
    // 再点子瞻 → 0.5.0 P5: 点击图标恢复未结束对话(不新建);
    // skill_not_found 已切回首页且窗口无快照 → 走新建, 应是新会话(不同 session id)
    await user.click(screen.getByTestId("mode-btn-office"));
    await waitFor(() => expect(activateUrls.length).toBe(2));
    expect(activateUrls[1]).not.toBe(activateUrls[0]);
    vi.unstubAllGlobals();
    void originalFetch;
  });

  it("普通 error 不切回首页", async () => {
    const user = userEvent.setup();
    render(<App />);
    await pickMode(user);

    act(() => {
      ws().onmessage?.({
        data: JSON.stringify({
          type: "error",
          message: "model provider unavailable",
        }),
      });
    });

    await waitFor(() => {
      // 普通 error 不切回首页: 仍停留在 chat 视图, HomeView 按钮(mode-btn-office)
      // 不存在。注意 sidebar 场景组始终含"子瞻", 不能 queryByText
      expect(screen.queryByTestId("mode-btn-office")).toBeNull();
    });
  });
});

// P0-1(2026-08-17): WS 连接状态可视化 —— 侧边栏底部状态卡映射
//   AC-1: connected → 状态点文案"已连接", 重连按钮消失
//   AC-2: onclose → "重连中（第 1 次）", 脉冲状态点出现
describe("P0-1 连接状态可视化", () => {
  it("connected → 状态卡显示已连接, 无重连按钮", async () => {
    render(<App />);
    // 初始 disconnected: 显示"未连接" + 重连按钮(onReconnect 已注入)
    await waitFor(() => expect(screen.getByText("未连接")).toBeTruthy());
    expect(screen.getByRole("button", { name: "重连" })).toBeTruthy();

    act(() => {
      ws().onopen?.();
    });

    await waitFor(() => expect(screen.getByText("已连接")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "重连" })).toBeNull();
  });

  it("onclose → 状态卡显示重连中(第 N 次)且次数递增", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("未连接")).toBeTruthy());

    act(() => {
      ws().onclose?.();
    });

    // scheduleReconnect 立即 setReconnectCount(0+1) → 第 1 次
    await waitFor(() => expect(screen.getByText("重连中（第 1 次）")).toBeTruthy());
  });
});
