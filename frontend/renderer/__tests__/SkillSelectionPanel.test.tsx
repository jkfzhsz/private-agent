// B2 P1-9 - SkillSelectionPanel 组件测试
//
// Source: plan/b2-remaining-features step 21-23 (修复计划 §2 P1-9)
// - 渲染 GET /admin/skills 返回的技能列表
// - 点击卡片触发 POST /admin/sessions/{session_id}/activate
// - 激活失败(404 skill_not_found)显示错误
// - 激活成功回调 onActivated
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SkillSelectionPanel from "../SkillSelectionPanel";

const API_BASE = "http://localhost:8765/admin";

const SKILLS = [
  { name: "office", version: "1.0.0", description: "办公场景技能", enabled: true },
  { name: "data_analysis", version: "1.0.0", description: "数据分析技能", enabled: true },
  { name: "frontend_design", version: "1.0.0", description: "前端设计技能", enabled: true },
];

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === "string" && url.includes("/skills")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(SKILLS),
        });
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

describe("SkillSelectionPanel", () => {
  it("渲染 GET /admin/skills 返回的技能列表", async () => {
    render(<SkillSelectionPanel sessionId={1} onActivated={() => {}} />);
    await screen.findByText("办公");
    expect(screen.getByText("数据分析")).toBeInTheDocument();
    expect(screen.getByText("前端设计")).toBeInTheDocument();
  });

  it("点击技能卡片触发 POST activate", async () => {
    const user = userEvent.setup();
    render(<SkillSelectionPanel sessionId={42} onActivated={() => {}} />);
    await screen.findByText("办公");

    await user.click(screen.getByText("办公"));

    await waitFor(() => {
      const calls = vi.mocked(fetch).mock.calls.filter(
        ([url]) => typeof url === "string" && url.includes("/activate"),
      );
      expect(calls.length).toBe(1);
      const [url, init] = calls[0] as [string, RequestInit];
      expect(url).toBe(`${API_BASE}/sessions/42/activate`);
      expect(init.body).toContain("office");
    });
  });

  it("激活成功回调 onActivated(技能名)", async () => {
    const user = userEvent.setup();
    const onActivated = vi.fn();
    render(<SkillSelectionPanel sessionId={1} onActivated={onActivated} />);
    await screen.findByText("办公");

    await user.click(screen.getByText("办公"));

    await waitFor(() => expect(onActivated).toHaveBeenCalledWith("office"));
  });

  it("activate 404 skill_not_found 显示错误", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (typeof url === "string" && url.includes("/skills")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve(SKILLS) });
        }
        return Promise.resolve({
          ok: false,
          json: () => Promise.resolve({ detail: "skill_not_found" }),
        });
      }),
    );

    render(<SkillSelectionPanel sessionId={1} onActivated={() => {}} />);
    await screen.findByText("办公");
    await user.click(screen.getByText("办公"));

    await screen.findByText(/技能不存在/);
  });
});
