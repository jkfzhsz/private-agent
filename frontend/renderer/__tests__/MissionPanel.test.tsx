/**
 * 0.6.0 F1-3(2026-08-28): MissionPanel 长任务卡片单测。
 *
 * 覆盖:
 * - 空面板不渲染 / 有 mission 渲染卡片
 * - 状态徽标(planning/executing/escalated/done)各 state 渲染
 * - 里程碑进度 done/total 计数
 * - 展开显示台账尾部 + escalated 裁决按钮(批准改道/中止)回调
 * - 清除已完成按钮
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import MissionPanel, {
  createMission,
  type MissionState,
} from "../components/MissionPanel";

function makeMission(partial: Partial<MissionState>): MissionState {
  return {
    ...createMission(1, "整理 56 页幻灯片视觉缺陷清单"),
    ...partial,
  };
}

describe("MissionPanel", () => {
  it("空 missions 不渲染面板", () => {
    const { container } = render(<MissionPanel missions={{}} />);
    expect(container.querySelector('[data-testid="mission-panel"]')).toBeNull();
  });

  it("渲染 mission 卡片: 编号/目标/状态徽标", () => {
    render(
      <MissionPanel
        missions={{
          42: makeMission({ id: 42, state: "executing" }),
        }}
      />
    );
    expect(screen.getByTestId("mission-card-42")).toBeTruthy();
    expect(screen.getByTestId("mission-state-42").textContent).toContain("执行中");
    expect(screen.getByTestId("mission-card-42").textContent).toContain(
      "整理 56 页幻灯片视觉缺陷清单"
    );
  });

  it("escalated 状态显示待裁决徽标与裁决按钮", () => {
    render(
      <MissionPanel
        missions={{
          7: makeMission({ id: 7, state: "escalated" }),
        }}
      />
    );
    expect(screen.getByTestId("mission-state-7").textContent).toContain("待裁决");
    // 裁决按钮在展开区内: 先点击卡片头展开
    fireEvent.click(screen.getByTestId("mission-header-7"));
    expect(screen.getByTestId("mission-approve-7").textContent).toContain("批准改道");
    expect(screen.getByTestId("mission-abort-7").textContent).toContain("中止任务");
  });

  it("非 escalated 状态不显示裁决按钮", () => {
    render(
      <MissionPanel missions={{ 7: makeMission({ id: 7, state: "executing" }) }} />
    );
    expect(screen.queryByTestId("mission-approve-7")).toBeNull();
  });

  it("展开卡片显示里程碑与进度计数", () => {
    const m = makeMission({
      id: 5,
      state: "executing",
      milestones: [
        { id: "m1", milestone: "扫描全部页面", executor_type: "subagent", status: "done" },
        { id: "m2", milestone: "汇总缺陷 CSV", executor_type: "script", status: "pending" },
      ],
    });
    render(<MissionPanel missions={{ 5: m }} />);
    expect(screen.getByTestId("mission-card-5").textContent).toContain("1/2");
    fireEvent.click(screen.getByTestId("mission-header-5"));
    expect(screen.getByTestId("mission-card-5").textContent).toContain("扫描全部页面");
    expect(screen.getByTestId("mission-card-5").textContent).toContain("汇总缺陷 CSV");
  });

  it("escalated 裁决按钮触发回调", () => {
    const approve = vi.fn();
    const abort = vi.fn();
    render(
      <MissionPanel
        missions={{ 9: makeMission({ id: 9, state: "escalated" }) }}
        onApproveFallback={approve}
        onAbort={abort}
      />
    );
    fireEvent.click(screen.getByTestId("mission-header-9")); // 展开显示裁决按钮
    fireEvent.click(screen.getByTestId("mission-approve-9"));
    fireEvent.click(screen.getByTestId("mission-abort-9"));
    expect(approve).toHaveBeenCalledWith(9);
    expect(abort).toHaveBeenCalledWith(9);
  });

  it("清除已完成按钮仅在存在终态 mission 时出现并触发回调", () => {
    const clear = vi.fn();
    const { rerender } = render(
      <MissionPanel
        missions={{ 1: makeMission({ id: 1, state: "executing" }) }}
        onClearFinished={clear}
      />
    );
    expect(screen.queryByText("清除已完成任务")).toBeNull();
    rerender(
      <MissionPanel
        missions={{ 1: makeMission({ id: 1, state: "done" }) }}
        onClearFinished={clear}
      />
    );
    fireEvent.click(screen.getByText("清除已完成任务"));
    expect(clear).toHaveBeenCalled();
  });
});
