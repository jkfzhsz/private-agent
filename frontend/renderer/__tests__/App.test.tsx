// B2 P1-9 - App 集成测试(技能选择 → chat → skill_not_found 跳转)
//
// Source: plan/b2-remaining-features step 22-23 (修复计划 §2 P1-9)
// - AC-17: 初始渲染技能选择视图
// - AC-18: WS skill_not_found error → 自动切回技能选择
// - AC-19: chat 视图显示 locked skill 名
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
    vi.fn().mockImplementation((url: string) => {
      if (typeof url === "string" && url.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (typeof url === "string" && url.includes("/activate")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ locked_version: "1.0.0", frozen_hash: "abc" }),
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

async function activateSkill(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await screen.findByText("办公");
  await user.click(screen.getByText("办公"));
  await waitFor(() => expect(screen.queryByText("发送")).toBeTruthy());
}

describe("App 技能选择集成", () => {
  it("初始渲染技能选择视图(AC-17)", async () => {
    render(<App />);
    await screen.findByText("选择技能场景");
    expect(screen.getByText("办公")).toBeInTheDocument();
  });

  it("激活技能后进入 chat 视图并显示 skill 名(AC-19)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await activateSkill(user);

    expect(screen.getByText("skill=office")).toBeInTheDocument();
  });

  it("WS skill_not_found error 自动切回技能选择(AC-18)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await activateSkill(user);

    act(() => {
      ws().onmessage?.({
        data: JSON.stringify({ type: "error", message: "skill_not_found" }),
      });
    });

    await screen.findByText("选择技能场景");
  });

  it("普通 error 不切回技能选择视图", async () => {
    const user = userEvent.setup();
    render(<App />);
    await activateSkill(user);

    act(() => {
      ws().onmessage?.({
        data: JSON.stringify({ type: "error", message: "model provider unavailable" }),
      });
    });

    await waitFor(() => {
      expect(screen.queryByText("选择技能场景")).toBeNull();
    });
  });
});
