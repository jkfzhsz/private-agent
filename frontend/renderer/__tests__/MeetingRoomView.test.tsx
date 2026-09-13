/**
 * 0.6.0 P2(会议室) —— 会议室视图单测(设计文档 §7.2)。
 *
 * 覆盖:
 * - 房间列表渲染主持人/成员数、已归档徽标; 空态;
 * - 新建弹层默认勾选与主持人默认值;
 * - **主持人必须属于已勾选成员**(取消勾选当前主持人 → 清空并要求重选);
 * - 主题为空 / 无成员 / 无主持人 → 不可提交;
 * - 提交按 ROOM_ROLES 规范顺序传参(与后端 normalize_roles 去重保序一致),
 *   成功进入房间、失败留在弹层并提示。
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../utils/rooms", async () => {
  const actual =
    await vi.importActual<typeof import("../utils/rooms")>("../utils/rooms");
  return {
    ...actual,
    listRooms: vi.fn(),
    createRoom: vi.fn(),
  };
});

import MeetingRoomView from "../views/MeetingRoomView";
import { createRoom, listRooms } from "../utils/rooms";

const listRoomsMock = vi.mocked(listRooms);
const createRoomMock = vi.mocked(createRoom);

function room(partial: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 7,
    title: "Q3 经营分析汇报 PPT",
    status: "active",
    kind: "room",
    updated_at: "2026-09-11T15:30:12",
    last_turn: 3,
    room_meta: {
      host_role: "office",
      members: ["office", "frontend_design"],
      goal: "做一份 Q3 经营分析汇报 PPT",
      room_key: "20260911-153012-abcd",
    },
    ...partial,
  };
}

async function renderView(): Promise<{ onEnterRoom: ReturnType<typeof vi.fn> }> {
  const onEnterRoom = vi.fn();
  render(<MeetingRoomView onEnterRoom={onEnterRoom} />);
  // 等首次加载**完成**(而非仅"已发起") —— 只等 listRooms 被调用会在并行
  // 负载下与 setState 竞态(2026-09-12 实测: 断言时仍渲染"加载中…")。
  await waitFor(() =>
    expect(screen.queryByText("加载中…")).toBeNull()
  );
  return { onEnterRoom };
}

function openDialog(): void {
  fireEvent.click(screen.getByTestId("room-new-btn"));
}

function setGoal(text: string): void {
  fireEvent.change(screen.getByTestId("room-goal-input"), {
    target: { value: text },
  });
}

function submitBtn(): HTMLButtonElement {
  return screen.getByTestId("room-create-submit") as HTMLButtonElement;
}

beforeEach(() => {
  listRoomsMock.mockResolvedValue([]);
  createRoomMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("MeetingRoomView 房间列表", () => {
  it("渲染房间行: 主题 / 进行中 / 主持人 / 成员数", async () => {
    listRoomsMock.mockResolvedValue([room() as never]);
    await renderView();
    const row = screen.getByTestId("room-row-7");
    expect(row.textContent).toContain("Q3 经营分析汇报 PPT");
    expect(row.textContent).toContain("进行中");
    expect(row.textContent).toContain("主持人 子瞻");
    expect(row.textContent).toContain("2 人");
  });

  it("已归档房间显示归档徽标且仍可进入", async () => {
    listRoomsMock.mockResolvedValue([room({ status: "archived" }) as never]);
    const { onEnterRoom } = await renderView();
    const row = screen.getByTestId("room-row-7");
    expect(row.textContent).toContain("已归档");
    fireEvent.click(row);
    expect(onEnterRoom).toHaveBeenCalledWith(7);
  });

  it("无房间时显示引导空态", async () => {
    await renderView();
    expect(screen.getByTestId("room-empty")).toBeTruthy();
    expect(screen.queryByTestId("room-row-7")).toBeNull();
  });
});

describe("MeetingRoomView 新建弹层", () => {
  it("默认勾选子瞻+白圭, 主持人默认子瞻, 主题为空时不可提交", async () => {
    await renderView();
    openDialog();
    expect(
      screen.getByTestId("room-member-office").getAttribute("aria-pressed")
    ).toBe("true");
    expect(
      screen.getByTestId("room-member-data_analysis").getAttribute("aria-pressed")
    ).toBe("true");
    expect(
      screen.getByTestId("room-member-frontend_design").getAttribute("aria-pressed")
    ).toBe("false");
    expect(screen.getByTestId("room-host-office").getAttribute("aria-pressed")).toBe(
      "true"
    );
    // 主题为空 → 禁用
    expect(submitBtn().disabled).toBe(true);
  });

  it("主持人选项只包含已勾选成员", async () => {
    await renderView();
    openDialog();
    // 默认成员 = 子瞻 + 白圭 → 主持人候选只有这两个
    expect(screen.getByTestId("room-host-office")).toBeTruthy();
    expect(screen.getByTestId("room-host-data_analysis")).toBeTruthy();
    expect(screen.queryByTestId("room-host-frontend_design")).toBeNull();
    // 勾上清和 → 候选出现清和
    fireEvent.click(screen.getByTestId("room-member-frontend_design"));
    expect(screen.getByTestId("room-host-frontend_design")).toBeTruthy();
    // 取消白圭 → 候选不再含白圭
    fireEvent.click(screen.getByTestId("room-member-data_analysis"));
    expect(screen.queryByTestId("room-host-data_analysis")).toBeNull();
  });

  it("取消勾选当前主持人 → 清空主持人并提示重选, 提交被禁用", async () => {
    await renderView();
    openDialog();
    setGoal("做一份 Q3 经营分析汇报 PPT");
    expect(submitBtn().disabled).toBe(false);

    fireEvent.click(screen.getByTestId("room-member-office")); // 取消主持人子瞻
    expect(screen.getByText("请指定本次主持人")).toBeTruthy();
    expect(submitBtn().disabled).toBe(true);
    expect(screen.queryByTestId("room-host-office")).toBeNull();
  });

  it("全部取消成员 → 提示先勾选成员且不可提交", async () => {
    await renderView();
    openDialog();
    setGoal("任意主题");
    fireEvent.click(screen.getByTestId("room-member-office"));
    fireEvent.click(screen.getByTestId("room-member-data_analysis"));
    expect(screen.getByText("请先勾选参会成员")).toBeTruthy();
    expect(submitBtn().disabled).toBe(true);
  });

  it("提交按成员规范顺序传参并进入房间", async () => {
    createRoomMock.mockResolvedValue({
      ok: true,
      id: 42,
      kind: "room",
      host_role: "data_analysis",
      members: ["office", "data_analysis", "frontend_design"],
      room_dir: "D:/PA/rooms/20260911-153012-abcd",
      room_meta: {},
    } as never);
    const { onEnterRoom } = await renderView();
    openDialog();
    setGoal("  做一份 Q3 经营分析汇报 PPT  ");
    fireEvent.click(screen.getByTestId("room-member-frontend_design"));
    fireEvent.click(screen.getByTestId("room-host-data_analysis"));
    fireEvent.click(submitBtn());

    await waitFor(() => expect(createRoomMock).toHaveBeenCalledTimes(1));
    expect(createRoomMock.mock.calls[0][0]).toEqual({
      goal: "做一份 Q3 经营分析汇报 PPT", // 首尾空白已裁剪
      members: ["office", "data_analysis", "frontend_design"],
      host_role: "data_analysis",
    });
    await waitFor(() => expect(onEnterRoom).toHaveBeenCalledWith(42));
  });

  it("创建失败: 显示错误且不进入房间", async () => {
    createRoomMock.mockRejectedValue(new Error("主持人 必须是参会成员之一"));
    const { onEnterRoom } = await renderView();
    openDialog();
    setGoal("会失败的房间");
    fireEvent.click(submitBtn());

    await waitFor(() =>
      expect(screen.getByText(/创建失败/)).toBeTruthy()
    );
    expect(onEnterRoom).not.toHaveBeenCalled();
    // 弹层仍在(用户可改后重试)
    expect(screen.getByTestId("room-create-dialog")).toBeTruthy();
  });
});
