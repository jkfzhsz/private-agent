/**
 * 0.6.0 P4(会议室收尾) —— 房间关闭/归档语义集成测试。
 *
 * 语义区分(设计文档 §4.6):
 * - 「← 房间列表」= 仅离开, 房间保留(不产生任何 PUT);
 * - 「⋯ 更多 → 关闭对话」在房间内 = **关闭房间** → PUT status=archived +
 *   回到会议室列表(可从历史树「🏛 会议室」组恢复, 产物保留在共享目录)。
 *
 * 同时回归守门: 房间不占四窗口槽位(activeSlot=-1), 关闭房间**不得**走
 * closeWindow 的窗口切换逻辑。
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  sent: string[] = [];
  static OPEN = 1;
  constructor(_url: string) {
    FakeWebSocket.instances.push(this);
    setTimeout(() => {
      this.readyState = 1;
      this.onopen?.();
    }, 0);
  }
  send(data: string): void {
    this.sent.push(data);
  }
  close(): void {
    this.readyState = 3;
    this.onclose?.();
  }
}

const ROOM_INFO = {
  ok: true,
  session_id: 77,
  title: "做一份 Q3 经营分析汇报 PPT",
  status: "active",
  host_role: "office",
  members: ["office", "frontend_design"],
  goal: "做一份 Q3 经营分析汇报 PPT",
  room_dir: "D:/PA/rooms/20260912-110000-aabb",
  exists: true,
  artifacts: [{ name: "report.md", size: 12, mtime: 0 }],
  notes: [],
};

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function roomFetchMock(putCalls: { url: string; body: string }[]) {
  return vi.fn((url: string, options?: RequestInit) => {
    const u = String(url);
    const method = options?.method ?? "GET";
    if (u.endsWith("/admin/rooms") && method === "POST") {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            ok: true,
            id: 77,
            kind: "room",
            host_role: "office",
            members: ["office", "frontend_design"],
            room_dir: ROOM_INFO.room_dir,
            room_meta: ROOM_INFO,
          }),
      });
    }
    if (u.includes("/admin/rooms/77")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(ROOM_INFO) });
    }
    if (u.includes("/admin/sessions/77") && method === "PUT") {
      putCalls.push({ url: u, body: String(options?.body) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    }
    if (u.includes("/skills")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    }
    // 其余(sessions 列表/missions/subagents/resume/memory 等)一律空数组
    return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
  });
}

async function enterRoom(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  // 侧边栏「会议室」导航项
  await user.click(await screen.findByRole("button", { name: /会议室/ }));
  // 新建弹层: 主题默认成员已勾选(子瞻+白圭, 主持人子瞻)
  await user.click(await screen.findByTestId("room-new-btn"));
  await user.type(
    await screen.findByTestId("room-goal-input"),
    "做一份 Q3 经营分析汇报 PPT"
  );
  await user.click(screen.getByTestId("room-create-submit"));
  // 创建成功 → 进入房间(信息条可见)
  await waitFor(() =>
    expect(screen.getByTestId("room-info-bar")).toBeInTheDocument()
  );
}

describe("P4: 房间关闭/归档语义", () => {
  it("「← 房间列表」仅离开, 不产生归档 PUT", async () => {
    const putCalls: { url: string; body: string }[] = [];
    vi.stubGlobal("fetch", roomFetchMock(putCalls) as unknown as typeof fetch);
    const user = userEvent.setup();
    render(<App />);
    await enterRoom(user);

    await user.click(screen.getByTestId("room-exit-btn"));
    await waitFor(() =>
      expect(screen.getByTestId("room-new-btn")).toBeInTheDocument()
    );
    expect(putCalls).toEqual([]);
  });

  it("房间内「关闭对话」→ PUT(status=archived) + 回到会议室列表", async () => {
    const putCalls: { url: string; body: string }[] = [];
    vi.stubGlobal("fetch", roomFetchMock(putCalls) as unknown as typeof fetch);
    const user = userEvent.setup();
    render(<App />);
    await enterRoom(user);

    // ⋯ 更多 → 关闭对话
    await user.click(await screen.findByTitle("更多操作"));
    await user.click(await screen.findByTitle(/关闭对话\(归档至历史任务\)/));
    // 房间语义的确认弹层
    expect(await screen.findByText("关闭会议室")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "关闭房间" }));

    await waitFor(() => expect(putCalls).toHaveLength(1));
    expect(putCalls[0].url).toContain("/admin/sessions/77");
    expect(putCalls[0].body).toBe('{"status":"archived"}');
    // 关闭后回到会议室列表(而非某个单智能体窗口)
    await waitFor(() =>
      expect(screen.getByTestId("room-new-btn")).toBeInTheDocument()
    );
    expect(screen.queryByTestId("room-info-bar")).toBeNull();
  });

  it("普通会话的关闭对话框保持原文案(零回归)", async () => {
    vi.stubGlobal("fetch", roomFetchMock([]) as unknown as typeof fetch);
    const user = userEvent.setup();
    render(<App />);
    // 进入子瞻(普通场景会话, 非房间)
    await user.click(await screen.findByTestId("mode-btn-office"));
    await screen.findByPlaceholderText(/输入消息|输入|发送/i);

    await user.click(await screen.findByTitle("更多操作"));
    await user.click(await screen.findByTitle(/关闭对话\(归档至历史任务\)/));
    expect(await screen.findByText("关闭当前对话")).toBeInTheDocument();
    expect(screen.queryByText("关闭会议室")).toBeNull();
    expect(screen.getByRole("button", { name: "关闭" })).toBeTruthy();
  });
});
