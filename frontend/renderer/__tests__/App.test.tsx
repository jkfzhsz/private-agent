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
  // HomeView 三个按钮: 工作模式 / 分析模式 / 设计模式
  await screen.findByText("工作模式");
  await user.click(screen.getByText("工作模式"));
  // 激活后顶部应显示 skill 名(office)
  await waitFor(() => expect(screen.getByText("office")).toBeInTheDocument());
}

describe("App 首页与模式选择集成", () => {
  it("初始渲染首页与三个模式按钮(AC-17)", async () => {
    render(<App />);
    expect(await screen.findByText("工作模式")).toBeInTheDocument();
    expect(screen.getByText("分析模式")).toBeInTheDocument();
    expect(screen.getByText("设计模式")).toBeInTheDocument();
  });

  it("点击模式按钮后进入 chat 视图并显示 skill 名(AC-19)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await pickMode(user);
    // 顶部 badge 显示当前激活的 skill
    expect(screen.getByText("office")).toBeInTheDocument();
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

    // 回到首页: 三个模式按钮重新可见
    expect(await screen.findByText("工作模式")).toBeInTheDocument();
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
      // chat 视图不应包含首页的"工作模式"按钮
      expect(screen.queryByText("工作模式")).toBeNull();
    });
  });
});
