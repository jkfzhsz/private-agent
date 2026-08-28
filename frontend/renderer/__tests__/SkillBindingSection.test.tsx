/**
 * 2026-08-27(skill_binding 语义重构 §2.3 验收): 智能体工具装配矩阵单测。
 *
 * 覆盖:
 * - 加载: GET /config/skill-binding → 渲染矩阵(场景行 × server 列), source 提示
 * - 通配列: binding 中的通配模式(hexin-ifind-ds-*)也作为列出现
 * - 勾选 → 保存: PUT 请求体 = 完整 skill_binding; 成功提示"已保存 · 新会话生效"
 * - 回退: source=runtime 时 DELETE → 重新 load
 * - 空 server: 显示"暂无 MCP server 配置"
 *
 * 注意: SkillBindingSection 包在 CollapsibleSection(defaultOpen=false)内,
 * 表格在展开前不在 DOM —— 每个用例先点击标题展开。
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SkillBindingSection } from "../views/SettingsView";

const SERVERS = [
  { id: "mempalace", type: "stdio", command: "x" },
  { id: "Searchpin", type: "stdio", command: "x" },
  { id: "codegraph", type: "stdio", command: "x" },
];

function mockFetchWithSkillBinding(initial: Record<string, string[]> | null) {
  const calls: { method: string; url: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const u = String(url);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ method, url: u, body: init?.body as string | undefined });
      if (method === "GET" && u.includes("/config/skill-binding")) {
        return new Response(
          JSON.stringify({
            skill_binding: initial ?? {},
            source: initial ? "runtime" : "yaml",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } }
        );
      }
      if (method === "PUT" && u.includes("/config/skill-binding")) {
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      if (method === "DELETE" && u.includes("/config/skill-binding")) {
        return new Response(JSON.stringify({ ok: true }), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200 });
    })
  );
  return calls;
}

async function expandSection(user: ReturnType<typeof userEvent.setup>) {
  const title = await screen.findByText("智能体工具装配");
  await user.click(title); // CollapsibleSection 默认折叠, 展开后表格才渲染
  await screen.findByText("子瞻 · 工作学习"); // 展开后第一个场景行出现
}

function checkboxInRow(row: HTMLElement, index: number): HTMLInputElement {
  const inputs = row.querySelectorAll("input[type='checkbox']");
  const el = inputs[index];
  if (!el) throw new Error(`row checkbox[${index}] not found`);
  return el as HTMLInputElement;
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SkillBindingSection 智能体工具装配矩阵", () => {
  it("加载后渲染场景行 × server 列, 并显示配置来源", async () => {
    mockFetchWithSkillBinding({
      office: ["mempalace"],
      monitor: ["mempalace", "Searchpin"],
    });
    const user = userEvent.setup();
    render(<SkillBindingSection mcpServers={SERVERS} />);

    // 标题与来源(binding 非空 → runtime 覆盖生效, subtitle 在折叠态也可见)
    await screen.findByText("智能体工具装配");
    await waitFor(() => {
      expect(screen.getByText(/runtime 覆盖生效/)).toBeTruthy();
    });

    await expandSection(user);

    // 场景行
    expect(screen.getByText("子瞻 · 工作学习")).toBeTruthy();
    expect(screen.getByText("白圭 · 投资理财")).toBeTruthy();
    expect(screen.getByText("清和 · 生活美学")).toBeTruthy();
    expect(screen.getByText("无涯 · 全局智能体")).toBeTruthy();

    // server 列
    expect(screen.getByText("mempalace")).toBeTruthy();
    expect(screen.getByText("Searchpin")).toBeTruthy();
    expect(screen.getByText("codegraph")).toBeTruthy();
  });

  it("binding 中的通配模式(hexin-ifind-ds-*)也作为列渲染", async () => {
    mockFetchWithSkillBinding({
      office: ["hexin-ifind-ds-*"],
      monitor: ["mempalace"],
    });
    const user = userEvent.setup();
    render(<SkillBindingSection mcpServers={SERVERS} />);

    await expandSection(user);
    await waitFor(() => {
      expect(screen.getByText("hexin-ifind-ds-*")).toBeTruthy();
    });
  });

  it("勾选并保存 → PUT 携带完整 binding, 提示新会话生效", async () => {
    const calls = mockFetchWithSkillBinding({
      office: ["mempalace"],
      monitor: [],
    });
    const user = userEvent.setup();
    render(<SkillBindingSection mcpServers={SERVERS} />);
    await expandSection(user);

    // office 行(第一个场景)的 codegraph 列(columns: mempalace/Searchpin/codegraph)
    const rows = screen.getAllByRole("row");
    const officeRow = rows.find((r) => r.textContent?.includes("子瞻 · 工作学习"));
    expect(officeRow).toBeTruthy();
    const officeCodegraph = checkboxInRow(officeRow!, 2);
    await user.click(officeCodegraph);
    await waitFor(() => {
      expect(officeCodegraph.checked).toBe(true);
    });

    // 保存
    await user.click(screen.getByText("保存"));

    await waitFor(() => {
      const putCall = calls.find((c) => c.method === "PUT");
      expect(putCall).toBeTruthy();
      const body = JSON.parse(putCall!.body ?? "{}");
      expect(body.skill_binding.office).toEqual(
        expect.arrayContaining(["mempalace", "codegraph"])
      );
    });
    // 成功提示
    await waitFor(() => {
      expect(screen.getByText("已保存 · 新会话生效")).toBeTruthy();
    });
  });

  it("回退按钮 → DELETE 请求并重新加载", async () => {
    const calls = mockFetchWithSkillBinding({
      office: ["mempalace"],
      monitor: ["mempalace"],
    });
    const user = userEvent.setup();
    render(<SkillBindingSection mcpServers={SERVERS} />);
    await screen.findByText("智能体工具装配");
    await waitFor(() => {
      expect(screen.getByText(/runtime 覆盖生效/)).toBeTruthy();
    });
    await expandSection(user);

    await user.click(screen.getByText("回退 yaml 默认"));

    await waitFor(() => {
      const delCall = calls.find((c) => c.method === "DELETE");
      expect(delCall).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getByText("已回退 config.yaml 默认")).toBeTruthy();
    });
  });

  it("无 MCP server 时显示空态提示", async () => {
    mockFetchWithSkillBinding({});
    const user = userEvent.setup();
    render(<SkillBindingSection mcpServers={[]} />);
    // 空态也在 CollapsibleSection 内, 需先展开
    const title = await screen.findByText("智能体工具装配");
    await user.click(title);
    await waitFor(() => {
      expect(screen.getByText("暂无 MCP server 配置")).toBeTruthy();
    });
  });
});
