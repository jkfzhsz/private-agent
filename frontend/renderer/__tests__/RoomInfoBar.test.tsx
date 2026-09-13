/**
 * 0.6.0 P2(会议室) —— 房间信息条单测(设计文档 §7.3)。
 *
 * 覆盖:
 * - 主持人 / 成员 / 产物·交接计数 / 房间目录渲染;
 * - 「打开目录」经 Electron 桥调用 openPath(失败回调 onNotify(false));
 * - 浏览器 dev 无桥时降级为复制路径(不让按钮变死键);
 * - 返回房间列表按钮回调。
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import RoomInfoBar from "../components/RoomInfoBar";
import type { RoomInfo } from "../utils/rooms";

const ROOM_DIR = "D:\\PA\\rooms\\20260911-153012-abcd";

function info(partial: Partial<RoomInfo> = {}): RoomInfo {
  return {
    ok: true,
    session_id: 7,
    title: "Q3 经营分析汇报 PPT",
    status: "active",
    host_role: "office",
    members: ["office", "data_analysis", "frontend_design"],
    goal: "做一份 Q3 经营分析汇报 PPT",
    room_dir: ROOM_DIR,
    exists: true,
    artifacts: [{ name: "report.md", size: 12, mtime: 0 }],
    notes: [
      { name: "handoff.md", size: 8, mtime: 0 },
      { name: "todo.md", size: 3, mtime: 0 },
    ],
    ...partial,
  };
}

afterEach(() => {
  delete (window as unknown as { pa?: unknown }).pa;
  vi.restoreAllMocks();
});

describe("RoomInfoBar", () => {
  it("渲染主持人中文名 / 成员 / 产物与交接计数 / 房间目录", () => {
    render(<RoomInfoBar info={info()} onExit={() => undefined} />);
    const bar = screen.getByTestId("room-info-bar");
    expect(bar.textContent).toContain("主持人");
    expect(bar.textContent).toContain("子瞻");
    expect(screen.getByTestId("room-info-members").textContent).toContain(
      "📄 子瞻"
    );
    expect(screen.getByTestId("room-info-members").textContent).toContain(
      "🎨 清和"
    );
    // 产物 1 · 交接 2
    expect(bar.textContent).toContain("产物 1 · 交接 2");
    expect(screen.getByTestId("room-info-dir").textContent).toBe(ROOM_DIR);
    expect(screen.getByTestId("room-info-dir").getAttribute("title")).toBe(
      ROOM_DIR
    );
  });

  it("点击返回房间列表触发 onExit", () => {
    const onExit = vi.fn();
    render(<RoomInfoBar info={info()} onExit={onExit} />);
    fireEvent.click(screen.getByTestId("room-exit-btn"));
    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it("打开目录: 经 Electron 桥调用 openPath 并传房间目录", async () => {
    const openPath = vi.fn().mockResolvedValue({ ok: true });
    (window as unknown as { pa: unknown }).pa = { openPath };
    const onNotify = vi.fn();
    render(
      <RoomInfoBar info={info()} onExit={() => undefined} onNotify={onNotify} />
    );
    fireEvent.click(screen.getByTestId("room-open-dir-btn"));
    await waitFor(() => expect(openPath).toHaveBeenCalledWith(ROOM_DIR));
    expect(onNotify).not.toHaveBeenCalled();
  });

  it("打开目录失败: 回调 onNotify(..., false)", async () => {
    const openPath = vi.fn().mockResolvedValue({ ok: false, error: "not_found" });
    (window as unknown as { pa: unknown }).pa = { openPath };
    const onNotify = vi.fn();
    render(
      <RoomInfoBar info={info()} onExit={() => undefined} onNotify={onNotify} />
    );
    fireEvent.click(screen.getByTestId("room-open-dir-btn"));
    await waitFor(() =>
      expect(onNotify).toHaveBeenCalledWith(
        expect.stringContaining("not_found"),
        false
      )
    );
  });

  it("无 Electron 桥(浏览器 dev): 降级为复制路径而非死键", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const onNotify = vi.fn();
    render(
      <RoomInfoBar info={info()} onExit={() => undefined} onNotify={onNotify} />
    );
    fireEvent.click(screen.getByTestId("room-open-dir-btn"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(ROOM_DIR));
    expect(onNotify).toHaveBeenCalledWith("房间目录已复制", true);
  });

  it("点击路径本身也复制(便于手动粘贴定位)", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    const onNotify = vi.fn();
    render(
      <RoomInfoBar info={info()} onExit={() => undefined} onNotify={onNotify} />
    );
    fireEvent.click(screen.getByTestId("room-info-dir"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(ROOM_DIR));
  });
});
