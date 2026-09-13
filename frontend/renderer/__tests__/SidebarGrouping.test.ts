/**
 * 0.6.0 P2(会议室) —— 历史树分组键单测。
 *
 * 守护点: 房间会话的 locked_skill_name 是**主持人角色**, 若 kind='room' 不优先
 * 判定, 房间会被归入"子瞻/白圭/清和"场景组 —— 历史树里看不出这是一次协同,
 * 且点进去会被当成该智能体的单会话打开。
 */
import { describe, expect, it } from "vitest";

import {
  groupHistorySessions,
  historyGroupKey,
  type SessionItem,
} from "../components/Sidebar";

function s(partial: Partial<SessionItem>): SessionItem {
  return {
    id: 1,
    title: "t",
    status: "active",
    folder: null,
    locked_skill_name: null,
    last_turn: 0,
    updated_at: null,
    ...partial,
  };
}

describe("historyGroupKey", () => {
  it("房间会话归入 room, 而不是主持人的场景组", () => {
    for (const host of ["office", "data_analysis", "frontend_design"]) {
      expect(
        historyGroupKey(s({ kind: "room", locked_skill_name: host }))
      ).toBe("room");
    }
  });

  it("monitor 会话归入 monitor", () => {
    expect(historyGroupKey(s({ kind: "monitor", locked_skill_name: null }))).toBe(
      "monitor"
    );
  });

  it("场景会话按 locked_skill_name 归组", () => {
    expect(historyGroupKey(s({ kind: "main", locked_skill_name: "office" }))).toBe(
      "office"
    );
    expect(
      historyGroupKey(s({ kind: "main", locked_skill_name: "data_analysis" }))
    ).toBe("data_analysis");
    expect(
      historyGroupKey(s({ kind: "main", locked_skill_name: "frontend_design" }))
    ).toBe("frontend_design");
  });

  it("未锁定 / 未知技能 → global", () => {
    expect(historyGroupKey(s({ locked_skill_name: null }))).toBe("global");
    expect(historyGroupKey(s({ locked_skill_name: "ghost" }))).toBe("global");
  });

  it("kind='sub' 不特殊处理(委派子会话本就由后端过滤, 此处按 skill 归组)", () => {
    expect(historyGroupKey(s({ kind: "sub", locked_skill_name: "office" }))).toBe(
      "office"
    );
  });
});

describe("groupHistorySessions", () => {
  it("预置全部分组键, 房间与场景会话互不混淆", () => {
    const map = groupHistorySessions([
      s({ id: 1, kind: "room", locked_skill_name: "office" }),
      s({ id: 2, kind: "main", locked_skill_name: "office" }),
      s({ id: 3, kind: "monitor", locked_skill_name: null }),
      s({ id: 4, locked_skill_name: null }),
    ]);
    expect(map.get("room")!.map((x) => x.id)).toEqual([1]);
    expect(map.get("office")!.map((x) => x.id)).toEqual([2]);
    expect(map.get("monitor")!.map((x) => x.id)).toEqual([3]);
    expect(map.get("global")!.map((x) => x.id)).toEqual([4]);
    // 空组存在但为空(渲染层按 length===0 跳过)
    expect(map.get("data_analysis")).toEqual([]);
    expect(map.get("frontend_design")).toEqual([]);
  });

  it("空输入不抛异常", () => {
    const map = groupHistorySessions([]);
    expect(map.get("room")).toEqual([]);
  });
});
