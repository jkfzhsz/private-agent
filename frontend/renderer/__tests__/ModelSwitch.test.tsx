/**
 * 2026-08-16 bug 复现: 切换 LLM 后对话框锁定无法输入。
 * 假设根因: changeSessionModel 使用 http://localhost:8765(IPv6 解析问题)
 * 或切换模型后发送消息触发异常 → isGenerating 卡 true。
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";

const SKILLS = [
  { name: "office", display_name: "子瞻", scene_name: "子瞻" },
  { name: "data_analysis", display_name: "白圭", scene_name: "白圭" },
  { name: "frontend_design", display_name: "清和", scene_name: "清和" },
];

class FakeWebSocket {
  static OPEN = 1;
  readyState = 0;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();
  constructor(_url: string) {
    FakeWebSocket.instances.push(this);
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.();
    }, 0);
  }
  static instances: FakeWebSocket[] = [];
}

function lastWs(): FakeWebSocket {
  return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
}

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  const fetchMock = vi.fn((url: string, options?: RequestInit) => {
    const u = String(url);
    if (u.includes("/skills")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
    }
    if (u.includes("/activate")) {
      const body = JSON.parse((options?.body as string) ?? "{}");
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ locked_version: "1.1.0", frozen_hash: "abc", skill_name: body.skill_name }),
      });
    }
    if (u.includes("/settings/providers")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ providers: [{ name: "deepseek-flash", enabled: true }, { name: "glm-vision", enabled: true }] }),
      });
    }
    if (u.includes("/model")) {
      const body = JSON.parse((options?.body as string) ?? "{}");
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ model_id: body.model_id }) });
    }
    if (u.includes("/agent-profile")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    }
    if (u.includes("/sessions/")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  });
  vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("切换 LLM 后输入框可用性", () => {
  it("进入对话 → 切换模型 → 输入框仍可输入且发送按钮可用", async () => {
    const user = userEvent.setup();
    render(<App />);
    // 进入子瞻对话
    await screen.findByTestId("mode-btn-office");
    await user.click(screen.getByTestId("mode-btn-office"));
    const textbox = await screen.findByPlaceholderText(/输入消息|输入|发送/i);

    // 切换模型为 deepseek-flash
    const select = screen.getByTitle(/选择本会话使用的模型/);
    await user.selectOptions(select, "deepseek-flash");
    // 等待模型切换完成(select 值更新)
    await waitFor(() => {
      expect((select as HTMLSelectElement).value).toBe("deepseek-flash");
    });

    // 输入框仍可输入
    await user.type(textbox, "测试消息");
    expect((textbox as HTMLTextAreaElement).value).toBe("测试消息");

    // 发送按钮可用(connected + 有输入)
    const sendBtn = screen.getByRole("button", { name: /发送/ });
    expect((sendBtn as HTMLButtonElement).disabled).toBe(false);
  });
});
