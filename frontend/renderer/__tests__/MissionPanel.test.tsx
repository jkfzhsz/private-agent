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

// ── 0.6.0 P3: V1/V2 payload 归一化 + V4 role 徽标 ──────────────────────────

import {
  missionStateFromRow,
  normalizeJournal,
  normalizePlan,
} from "../components/MissionPanel";

describe("normalizePlan(V1/V2: mission_created/update 的 plan)", () => {
  it("接受数组形式(后端 _mission_plan_view)", () => {
    const plan = normalizePlan([
      { id: "m1", milestone: "转制 PPT", executor_type: "subagent",
        role: "frontend_design", status: "pending" },
      { id: "m2", milestone: "汇总", executor_type: "wait", status: "done" },
    ]);
    expect(plan).toHaveLength(2);
    expect(plan[0]).toEqual({
      id: "m1", milestone: "转制 PPT", executor_type: "subagent",
      role: "frontend_design", status: "pending",
    });
    expect(plan[1].role ?? null).toBeNull();
  });

  it("接受 JSONB 字符串形式(asyncpg 默认 codec)", () => {
    const plan = normalizePlan(
      '[{"id":"m1","milestone":"调研","executor_type":"subagent","status":"done"}]'
    );
    expect(plan[0].id).toBe("m1");
    expect(plan[0].status).toBe("done");
  });

  it("脏数据降级为空数组, 不抛异常", () => {
    expect(normalizePlan(null)).toEqual([]);
    expect(normalizePlan("not-json{")).toEqual([]);
    expect(normalizePlan(42)).toEqual([]);
    // 非对象元素被剔除, 其余保留
    const plan = normalizePlan([null, 7, { id: "m1", milestone: "x" }]);
    expect(plan).toHaveLength(1);
    expect(plan[0].id).toBe("m1");
  });

  it("缺字段补默认值(不因缺 role/status 崩)", () => {
    const plan = normalizePlan([{}]);
    expect(plan[0]).toEqual({
      id: "m0", milestone: "", executor_type: "subagent",
      role: null, status: undefined,
    });
  });
});

describe("normalizeJournal(V3: GET /admin/missions 兜底)", () => {
  it("字符串与数组两种形态都可解析", () => {
    const fromStr = normalizeJournal(
      '[{"kind":"milestone_done","ts":"t1","detail":"d"}]'
    );
    expect(fromStr).toEqual([{ kind: "milestone_done", ts: "t1", detail: "d" }]);
    expect(normalizeJournal([{ kind: "k", ts: "t" }])).toEqual([
      { kind: "k", ts: "t", detail: undefined },
    ]);
    expect(normalizeJournal("bad[")).toEqual([]);
  });
});

describe("missionStateFromRow(V3: DB 轮询兜底行归一化)", () => {
  const row = {
    id: 3,
    charter: '{"goal":"做一份 Q3 经营分析汇报 PPT"}',
    plan: '[{"id":"m1","milestone":"转 PPT","executor_type":"subagent","role":"frontend_design","status":"done"}]',
    journal: '[{"kind":"milestone_done","ts":"t"}]',
    state: "executing",
    error: null,
  };

  it("charter.goal → goal, plan/journal 归一化", () => {
    const m = missionStateFromRow(row);
    expect(m.id).toBe(3);
    expect(m.goal).toBe("做一份 Q3 经营分析汇报 PPT");
    expect(m.milestones).toHaveLength(1);
    expect(m.milestones[0].role).toBe("frontend_design");
    expect(m.journal).toHaveLength(1);
    expect(m.state).toBe("executing");
  });

  it("WS 已有状态优先保留(goal/detail/result/createdAt), DB 补终局字段", () => {
    const prev = {
      ...missionStateFromRow(row),
      goal: "WS 先到的目标",
      detail: "实时细节",
      result: "部分结果",
      createdAt: 12345,
    };
    const m = missionStateFromRow({ ...row, error: "boom" }, prev);
    expect(m.goal).toBe("WS 先到的目标");
    expect(m.detail).toBe("实时细节");
    expect(m.result).toBe("部分结果");
    expect(m.createdAt).toBe(12345);
    expect(m.error).toBe("boom");
    // state/里程碑以 DB 为准(DB 是终局事实)
    expect(m.state).toBe("executing");
    expect(m.milestones[0].status).toBe("done");
  });

  it("JSONB 已解析对象(charter 为 dict)同样可用", () => {
    const m = missionStateFromRow({
      ...row,
      charter: { goal: "对象形态" },
    });
    expect(m.goal).toBe("对象形态");
  });
});

describe("V4: 里程碑 role 徽标", () => {
  it("带 role 的里程碑显示角色徽标(子瞻/白圭/清和)", () => {
    const m = makeMission({
      id: 11,
      state: "executing",
      milestones: [
        { id: "m1", milestone: "转制商务 PPT", executor_type: "subagent",
          role: "frontend_design", status: "pending" },
        { id: "m2", milestone: "汇总", executor_type: "script", status: "pending" },
      ],
    });
    render(<MissionPanel missions={{ 11: m }} />);
    fireEvent.click(screen.getByTestId("mission-header-11"));
    expect(
      screen.getByTestId("mission-role-11-m1").textContent
    ).toContain("清和");
    // 无 role 的里程碑不渲染徽标(不堆图标)
    expect(screen.queryByTestId("mission-role-11-m2")).toBeNull();
  });

  it("未知角色原样展示(不崩)", () => {
    const m = makeMission({
      id: 12,
      state: "executing",
      milestones: [
        { id: "m1", milestone: "x", executor_type: "subagent", role: "ghost" },
      ],
    });
    render(<MissionPanel missions={{ 12: m }} />);
    fireEvent.click(screen.getByTestId("mission-header-12"));
    expect(screen.getByTestId("mission-role-12-m1").textContent).toContain(
      "ghost"
    );
  });
});
