/**
 * 0.5.0 P5: 四窗口并发 —— 状态圆点 + 恢复未结束对话 + 关闭对话。
 * 0.5.0 P6(2026-08-09): 单 WS 复用 + 主智能体统一渲染 ——
 * - 切换会话不重建 WS(单连接), 输入框立即可用(不依赖重连)
 * - 主智能体对话渲染完整功能区(切换技能/设置/任务/关闭对话/输入卡片)
 * - WS 事件按会话归属过滤(后台窗口增量不串入当前窗口)
 *
 * 验证(2026-08-08 蒋先生方案: 圆点替代 tab 标签):
 * - PA 图标/场景按钮旁状态圆点(绿=对话中/红=无对话)
 * - 点击场景图标恢复未结束对话(不新建)
 * - 关闭对话按钮 → 归档 PUT 调用
 * - 切出对话页面不打断(状态缓存保留)
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../App";

// ── WS mock ──────────────────────────────────────────────────────────────
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

function lastWs(): FakeWebSocket {
  return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
}

function emitWs(ws: FakeWebSocket, data: unknown): void {
  act(() => {
    ws.onmessage?.({ data: JSON.stringify(data) });
  });
}

function wsEvent(ev: Record<string, unknown>, sessionId = 1): unknown {
  return {
    type: "react_event",
    session_id: sessionId,
    turn: 1,
    event_type: ev.event_type,
    payload: ev.payload ?? {},
  };
}

beforeAll(() => {
  vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
});

beforeEach(() => {
  FakeWebSocket.instances = [];
  // afterEach 会 unstubAllGlobals 撤销全局 stub, 故 beforeEach 重新打桩
  vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const SKILLS = [
  { name: "office", display_name: "子瞻", scene_name: "子瞻" },
  { name: "data_analysis", display_name: "白圭", scene_name: "白圭" },
  { name: "frontend_design", display_name: "清和", scene_name: "清和" },
];

describe("App 四窗口并发集成", () => {
  it("主智能体对话入口: 侧边栏 PA 图标 → 进入主智能体对话(P4-4)", async () => {
    // 后端创建 monitor 会话 + 会话列表
    const fetchMock = vi.fn((url: string, options?: RequestInit) => {
      const u = String(url);
      if (u.includes("/sessions") && options?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, id: 42, kind: "monitor" }),
        });
      }
      if (u.includes("/sessions?")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (u.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (u.includes("/agent-profile")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (u.includes("/settings/providers")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const user = userEvent.setup();
    render(<App />);
    // 点击侧边栏 PA 图标(智能体标识按钮) → 进入主智能体对话
    // 注: PA 按钮 title 含"点击进入主智能体对话", 状态圆点 title 是"主智能体对话中",
    // 用按钮 role+title 精确匹配主智能体对话入口按钮
    const paBtn = await screen.findByTitle(/点击进入主智能体对话/);
    await user.click(paBtn);
    // 进入 chat 视图且显示主智能体对话提示
    await waitFor(() => {
      expect(screen.getByText(/负责系统监控与优化/)).toBeInTheDocument();
    });
  });

  it("场景窗口状态隔离(P2-2 保留): 子瞻草稿不串到白圭", async () => {
    // 进入子瞻窗口并发送一条消息(模拟激活会话)
    const fetchMock = vi.fn((url: string) => {
      const u = String(url);
      if (u.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (u.includes("/agent-profile")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (u.includes("/activate")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ skill_name: "office", locked_version: "1.1.0", frozen_hash: "abc" }),
        });
      }
      if (u.includes("/settings/providers")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const user = userEvent.setup();
    render(<App />);
    // 点子瞻按钮进入 chat
    const officeBtn = await screen.findByTestId("mode-btn-office");
    await user.click(officeBtn);
    // 发送消息 → 输入框产生内容
    const textbox = await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    await user.type(textbox, "子瞻窗口的草稿");
    // 侧边栏"首页"按钮回首页 → 激活白圭 → 输入框应为空(状态隔离)
    const homeBtn = screen.getByRole("button", { name: /首页/ });
    await user.click(homeBtn);
    const baiguiBtn = await screen.findByTestId("mode-btn-data_analysis");
    await user.click(baiguiBtn);
    const input2 = await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    expect((input2 as HTMLInputElement).value).not.toBe("子瞻窗口的草稿");
  });

  it("切出对话页不打断 + 点击图标恢复未结束对话(P5-2/P5-3)", async () => {
    const fetchMock = vi.fn((url: string) => {
      const u = String(url);
      if (u.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (u.includes("/agent-profile")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (u.includes("/activate")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ skill_name: "office", locked_version: "1.1.0", frozen_hash: "abc" }),
        });
      }
      if (u.includes("/settings/providers")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const user = userEvent.setup();
    render(<App />);
    // 进入子瞻对话, 输入草稿
    const officeBtn = await screen.findByTestId("mode-btn-office");
    await user.click(officeBtn);
    const textbox = await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    await user.type(textbox, "未发送的草稿");
    // 回首页(切出对话页, 不关闭对话)
    await user.click(screen.getByRole("button", { name: /首页/ }));
    // 子瞻按钮状态圆点应为绿色(对话中)
    // 再点子瞻图标 → 恢复未结束对话(草稿保留)
    await user.click(screen.getByTestId("mode-btn-office"));
    const restored = await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    expect((restored as HTMLInputElement).value).toBe("未发送的草稿");
  });

  it("关闭对话 → 归档 PUT(status=archived)(P5-4)", async () => {
    const putCalls: { url: string; body: string }[] = [];
    const fetchMock = vi.fn((url: string, options?: RequestInit) => {
      const u = String(url);
      if (u.includes("/sessions/") && options?.method === "PUT") {
        putCalls.push({ url: u, body: String(options.body) });
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (u.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (u.includes("/agent-profile")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (u.includes("/activate")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ skill_name: "office", locked_version: "1.1.0", frozen_hash: "abc" }),
        });
      }
      if (u.includes("/settings/providers")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const user = userEvent.setup();
    render(<App />);
    const officeBtn = await screen.findByTestId("mode-btn-office");
    await user.click(officeBtn);
    // 点击「⋯ 更多」展开 → 关闭对话按钮 → 玻璃确认弹层 → 确认 → 归档 PUT
    // P0-3(2026-08-17): window.confirm 已替换为 ConfirmDialog, 需在弹层内点确认
    // P1-4(2026-08-17): 关闭对话收进「⋯」下拉, 先展开
    await user.click(screen.getByTitle("更多操作"));
    const closeBtn = screen.getByTitle("关闭对话(归档至历史任务)");
    await user.click(closeBtn);
    const dialog = await screen.findByRole("dialog", { name: "关闭当前对话" });
    await user.click(within(dialog).getByRole("button", { name: "关闭" }));
    expect(putCalls.length).toBeGreaterThan(0);
    const last = putCalls[putCalls.length - 1];
    expect(last.body).toContain("archived");
  });

  it("单 WS 复用: 切换会话不新建连接 + 输入框立即可用(P6-1)", async () => {
    const fetchMock = vi.fn((url: string, options?: RequestInit) => {
      const u = String(url);
      if (u.includes("/sessions") && options?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, id: 42, kind: "monitor" }),
        });
      }
      if (u.includes("/sessions?")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (u.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (u.includes("/agent-profile")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (u.includes("/activate")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ skill_name: "office", locked_version: "1.1.0", frozen_hash: "abc" }),
        });
      }
      if (u.includes("/settings/providers")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const user = userEvent.setup();
    render(<App />);
    // 初始挂载建 1 个 WS
    await waitFor(() => {
      expect(FakeWebSocket.instances.length).toBeGreaterThan(0);
    });
    const wsCountAfterMount = FakeWebSocket.instances.length;
    // 进入子瞻 → 切白圭 → 回子瞻: 全程不应新增 WS 连接
    const officeBtn = await screen.findByTestId("mode-btn-office");
    await user.click(officeBtn);
    await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    await user.click(screen.getByRole("button", { name: /首页/ }));
    const baiguiBtn = await screen.findByTestId("mode-btn-data_analysis");
    await user.click(baiguiBtn);
    await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    await user.click(screen.getByRole("button", { name: /首页/ }));
    await user.click(screen.getByTestId("mode-btn-office"));
    await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    // 单 WS 复用: 窗口切换不应新建连接(原实现每次切换 close+connect)
    expect(FakeWebSocket.instances.length).toBe(wsCountAfterMount);
  });

  it("主智能体统一渲染: 功能区完整 + 无切换技能按钮 + 流式 delta 可见(P6-2)", async () => {
    const fetchMock = vi.fn((url: string, options?: RequestInit) => {
      const u = String(url);
      if (u.includes("/sessions") && options?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, id: 42, kind: "monitor" }),
        });
      }
      if (u.includes("/sessions?")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (u.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (u.includes("/agent-profile")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (u.includes("/settings/providers")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const user = userEvent.setup();
    render(<App />);
    // 进入主智能体对话
    const paBtn = await screen.findByTitle(/点击进入主智能体对话/);
    await user.click(paBtn);
    await waitFor(() => {
      expect(screen.getByText(/负责系统监控与优化/)).toBeInTheDocument();
    });
    // 功能区(P1-4 收纳进「⋯ 更多」): 点开下拉 → 设置 + 任务 + 关闭对话 齐全
    await user.click(screen.getByTitle("更多操作"));
    expect(screen.getByTitle("会话设置(记忆/截断/系统提示词)")).toBeInTheDocument();
    expect(screen.getByTitle("任务执行状态")).toBeInTheDocument();
    expect(screen.getByTitle("关闭对话(归档至历史任务)")).toBeInTheDocument();
    // 关闭下拉
    await user.click(screen.getByTitle("更多操作"));
    // 主智能体无 skill: 不显示"切换技能"按钮
    expect(screen.queryByText("🔄 切换技能")).not.toBeInTheDocument();
    // 完整输入卡片: 工作区 + 更多(+) + 模型选择
    expect(screen.getByTitle(/工作区:/)).toBeInTheDocument();
    expect(screen.getByTitle("更多")).toBeInTheDocument();
    expect(screen.getByText("🤖 自动(fallback 链)")).toBeInTheDocument();
    // 流式 delta 渲染(与其他智能体一致): 先 user 事件再 delta → 可见文本
    // (消息块按 turn 组织, 渲染依赖 user 事件存在)
    // 主智能体会话 id = 42(前端 POST /sessions 创建), 事件需带该 session_id
    const ws = lastWs();
    emitWs(ws, wsEvent({ event_type: "user", payload: { content: "查看系统性能" } }, 42));
    emitWs(ws, wsEvent({ event_type: "delta", payload: { turn: 1, content: "正在流式输出" } }, 42));
    await waitFor(() => {
      expect(screen.getByText(/正在流式输出/)).toBeInTheDocument();
    });
  });

  it("后台会话事件写入快照, 切回后展示(P6-3)", async () => {
    const fetchMock = vi.fn((url: string, options?: RequestInit) => {
      const u = String(url);
      if (u.includes("/sessions") && options?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, id: 42, kind: "monitor" }),
        });
      }
      if (u.includes("/sessions?")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (u.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (u.includes("/agent-profile")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (u.includes("/activate")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ skill_name: "office", locked_version: "1.1.0", frozen_hash: "abc" }),
        });
      }
      if (u.includes("/settings/providers")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const user = userEvent.setup();
    render(<App />);
    // 进入子瞻会话(快照 sessionId 挂载为随机 sid)
    const officeBtn = await screen.findByTestId("mode-btn-office");
    await user.click(officeBtn);
    await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    // 子瞻会话确认真实 id=7(懒创建回传)
    const ws = lastWs();
    emitWs(ws, wsEvent({ event_type: "user", payload: { content: "子瞻问题" } }, 7));
    await waitFor(() => {
      expect(screen.getByText(/子瞻问题/)).toBeInTheDocument();
    });
    // 切到白圭(新会话)
    await user.click(screen.getByRole("button", { name: /首页/ }));
    const baiguiBtn = await screen.findByTestId("mode-btn-data_analysis");
    await user.click(baiguiBtn);
    await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    // 白圭当前视图不渲染子瞻(7)的后台增量
    emitWs(ws, wsEvent({ event_type: "delta", payload: { turn: 1, content: "子瞻后台的增量" } }, 7));
    expect(screen.queryByText(/子瞻后台的增量/)).not.toBeInTheDocument();
    // 白圭自己的事件正常渲染
    emitWs(ws, wsEvent({ event_type: "user", payload: { content: "白圭问题" } }, 9));
    emitWs(ws, wsEvent({ event_type: "delta", payload: { turn: 1, content: "白圭当前增量" } }, 9));
    await waitFor(() => {
      expect(screen.getByText(/白圭当前增量/)).toBeInTheDocument();
    });
    // 切回子瞻 → 后台累积的增量应展示(快照已写入, 未丢失)
    await user.click(screen.getByRole("button", { name: /首页/ }));
    await user.click(screen.getByTestId("mode-btn-office"));
    await waitFor(() => {
      expect(screen.getByText(/子瞻后台的增量/)).toBeInTheDocument();
    });
  });

  it("并发快速来回切换: A 会话输出不渲染到 B 窗口(P6-4)", async () => {
    const fetchMock = vi.fn((url: string, options?: RequestInit) => {
      const u = String(url);
      if (u.includes("/sessions") && options?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, id: 42, kind: "monitor" }),
        });
      }
      if (u.includes("/sessions?")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (u.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (u.includes("/agent-profile")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (u.includes("/activate")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ skill_name: "office", locked_version: "1.1.0", frozen_hash: "abc" }),
        });
      }
      if (u.includes("/settings/providers")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const user = userEvent.setup();
    render(<App />);
    const ws = lastWs();
    // 子瞻(id=7)与白圭(id=9)两个会话的并发事件交错到达
    // 当前视图=子瞻: 白圭(id=9)的增量不应显示
    const officeBtn = await screen.findByTestId("mode-btn-office");
    await user.click(officeBtn);
    await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    emitWs(ws, wsEvent({ event_type: "user", payload: { content: "子瞻消息" } }, 7));
    await waitFor(() => expect(screen.getByText(/子瞻消息/)).toBeInTheDocument());
    // 交错: 白圭的增量先到(此时仍显示子瞻)
    emitWs(ws, wsEvent({ event_type: "delta", payload: { turn: 1, content: "白圭并发输出A" } }, 9));
    expect(screen.queryByText(/白圭并发输出A/)).not.toBeInTheDocument();
    // 切白圭: 白圭的 user + 增量出现; 子瞻后台增量不出现
    await user.click(screen.getByRole("button", { name: /首页/ }));
    await user.click(screen.getByTestId("mode-btn-data_analysis"));
    await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    emitWs(ws, wsEvent({ event_type: "user", payload: { content: "白圭消息" } }, 9));
    await waitFor(() => expect(screen.getByText(/白圭消息/)).toBeInTheDocument());
    emitWs(ws, wsEvent({ event_type: "delta", payload: { turn: 1, content: "白圭并发输出B" } }, 9));
    await waitFor(() => expect(screen.getByText(/白圭并发输出B/)).toBeInTheDocument());
    // 子瞻后台的增量到达 → 白圭窗口不应显示
    emitWs(ws, wsEvent({ event_type: "delta", payload: { turn: 1, content: "子瞻后台输出" } }, 7));
    expect(screen.queryByText(/子瞻后台输出/)).not.toBeInTheDocument();
  });

  it("网络抖动重连: 重连后恢复当前会话状态, 不串台(P6-5)", async () => {
    const fetchMock = vi.fn((url: string, options?: RequestInit) => {
      const u = String(url);
      if (u.includes("/sessions") && options?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ok: true, id: 42, kind: "monitor" }),
        });
      }
      if (u.includes("/sessions?")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (u.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (u.includes("/agent-profile")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (u.includes("/activate")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ skill_name: "office", locked_version: "1.1.0", frozen_hash: "abc" }),
        });
      }
      if (u.includes("/settings/providers")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const user = userEvent.setup();
    render(<App />);
    // 进入白圭会话
    const baiguiBtn = await screen.findByTestId("mode-btn-data_analysis");
    await user.click(baiguiBtn);
    await screen.findByPlaceholderText(/输入消息|输入|发送/i);
    const ws = lastWs();
    emitWs(ws, wsEvent({ event_type: "user", payload: { content: "白圭问题" } }, 9));
    await waitFor(() => expect(screen.getByText(/白圭问题/)).toBeInTheDocument());
    // 模拟断线: 关闭当前 WS → App 自动重连(新实例, 首次退避 1s)
    const first = ws;
    act(() => first.close());
    // 等待重连产生新 WS
    await waitFor(
      () => {
        expect(FakeWebSocket.instances.length).toBeGreaterThan(1);
      },
      { timeout: 5000 }
    );
    const ws2 = lastWs();
    // 重连后后端 replay 当前会话历史(id=9) → 恢复展示
    emitWs(ws2, wsEvent({ event_type: "delta", payload: { turn: 1, content: "重连后的恢复内容" } }, 9));
    await waitFor(() => {
      expect(screen.getByText(/重连后的恢复内容/)).toBeInTheDocument();
    });
    // 其他会话(id=7)事件不串入
    emitWs(ws2, wsEvent({ event_type: "delta", payload: { turn: 1, content: "重连后别台输出" } }, 7));
    expect(screen.queryByText(/重连后别台输出/)).not.toBeInTheDocument();
  });

  it("历史树点击 monitor 会话 → 进 chat 而非 home(2026-08-16 修复)", async () => {
    // 会话列表含 monitor 会话(locked_skill_name=NULL, kind=monitor)
    const fetchMock = vi.fn((url: string) => {
      const u = String(url);
      if (u.includes("/sessions?")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([
            {
              id: 56, title: "系统监控", status: "active", kind: "monitor",
              locked_skill_name: null, model_id: null, last_turn: 3, folder: null,
            },
          ]),
        });
      }
      if (u.includes("/skills")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
      }
      if (u.includes("/agent-profile")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      if (u.includes("/sessions/56/resume")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ resumable: false }) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    });
    vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

    const user = userEvent.setup();
    render(<App />);
    // 展开历史任务 → monitor 组默认收起 → 展开 monitor 组 → 点击会话
    await user.click(screen.getByRole("button", { name: /历史任务/ }));
    // monitor 组按钮 title: "{agentName}场景会话(N)"(agentName 未配置 → 主智能体)
    const monitorGroup = await screen.findByTitle(/主智能体场景会话/);
    await user.click(monitorGroup);
    const sessionRow = await screen.findByText(/系统监控/);
    await user.click(sessionRow);
    // 修复前: 跳主页; 修复后: 进 chat 对话界面(输入框出现)
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/输入消息|输入|发送/i)).toBeInTheDocument();
    });
  });
});
