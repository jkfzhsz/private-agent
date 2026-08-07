// V1.1 技能库视图(2026-08-05 方向修正: 技能就是技能, 不做"智能体"包装)
// 功能: 列表(名称/简介/启停) + 调用(开启对话) + 删除
// 已移除: 克隆 / 编辑(meta 抽屉) —— 用户明确不需要
import { useCallback, useEffect, useState } from "react";

import { adminFetch } from "../utils/apiClient";

const API_BASE = "http://127.0.0.1:8765/admin";

interface SkillItem {
  name: string;
  version: string;
  description: string;
  enabled: boolean;
  permissions: {
    allow_file_write: boolean;
    allow_network: boolean;
    sandbox_enabled: boolean;
    max_file_size_mb: number;
  };
}

const GRAVATAR_COLORS = [
  "linear-gradient(135deg, #818cf8, #6366f1)",
  "linear-gradient(135deg, #c084fc, #a855f7)",
  "linear-gradient(135deg, #f472b6, #ec4899)",
  "linear-gradient(135deg, #34d399, #10b981)",
  "linear-gradient(135deg, #fbbf24, #f59e0b)",
  "linear-gradient(135deg, #38bdf8, #0284c7)",
];

function colorFor(name: string): string {
  let h = 0;
  for (let i = 0; i < name.length; i += 1) h = (h * 31 + name.charCodeAt(i)) % 997;
  return GRAVATAR_COLORS[h % GRAVATAR_COLORS.length];
}

export default function AgentLibraryView({
  onActivate,
}: {
  onActivate: (skillName: string) => void;
}): JSX.Element {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError("");
    try {
      const resp = await adminFetch(`${API_BASE}/skills`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setSkills(await resp.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleEnabled = async (s: SkillItem): Promise<void> => {
    setMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/skills/${s.name}/meta`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !s.enabled }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      void load();
    } catch (e) {
      setMsg({ ok: false, text: `启停失败: ${String(e)}` });
    }
  };

  const removeSkill = async (s: SkillItem): Promise<void> => {
    if (!window.confirm(`删除技能 "${s.name}"？\n该技能配置将被移除，正在使用它的会话可能无法继续。`)) return;
    try {
      const resp = await adminFetch(`${API_BASE}/skills/${s.name}`, { method: "DELETE" });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.detail ?? data.error ?? `HTTP ${resp.status}`);
      }
      setMsg({ ok: true, text: `已删除 ${s.name}` });
      void load();
    } catch (e) {
      setMsg({ ok: false, text: `删除失败: ${String(e)}` });
    }
  };

  // 2026-08-07: 技能健康测试(加载/工具白名单/system_prompt 一键校验)
  const [testing, setTesting] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{
    name: string;
    ok: boolean;
    checks: { name: string; ok: boolean; detail: string }[];
  } | null>(null);

  const testSkill = async (s: SkillItem): Promise<void> => {
    setTesting(s.name);
    setTestResult(null);
    try {
      const resp = await adminFetch(`${API_BASE}/skills/${s.name}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await resp.json().catch(() => ({}));
      setTestResult({
        name: s.name,
        ok: Boolean(data.ok),
        checks: Array.isArray(data.checks) ? data.checks : [],
      });
    } catch (e) {
      setTestResult({ name: s.name, ok: false, checks: [{ name: "请求", ok: false, detail: String(e) }] });
    } finally {
      setTesting(null);
    }
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16, overflowY: "auto", paddingRight: 4 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <div style={{ fontSize: 16, fontWeight: 700, letterSpacing: "-0.02em" }}>技能库</div>
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 2 }}>
            每个技能对应一种能力场景，点击"调用"即可开始对话
          </div>
        </div>
        <button className="btn-primary" onClick={() => void load()} disabled={loading}>
          {loading ? "…" : "刷新"}
        </button>
      </div>

      {error && <div style={{ fontSize: 12, color: "var(--danger-text)" }}>{error}</div>}
      {msg && (
        <div style={{ fontSize: 12, color: msg.ok ? "var(--success-text)" : "var(--danger-text)" }}>
          {msg.text}
        </div>
      )}

      {!loading && skills.length === 0 && !error && (
        <div className="glass-panel" style={{ padding: "40px 24px", textAlign: "center", fontSize: 13, color: "var(--text-tertiary)" }}>
          暂无技能。到设置页上传技能包，或从首页选择一个模式开始。
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
        {skills.map((s) => (
          <div
            key={s.name}
            className="glass-panel animate-in"
            style={{
              padding: "16px 18px",
              display: "flex",
              flexDirection: "column",
              gap: 10,
              opacity: s.enabled ? 1 : 0.6,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: 10,
                  background: colorFor(s.name),
                  color: "#fff",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontWeight: 700,
                  fontSize: 16,
                  flexShrink: 0,
                }}
              >
                {s.name.slice(0, 1).toUpperCase()}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {s.name}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                  v{s.version} · {s.enabled ? "已启用" : "已停用"}
                </div>
              </div>
              <label title="启用/停用" style={{ flexShrink: 0, display: "flex", alignItems: "center", cursor: "pointer" }}>
                <input type="checkbox" checked={s.enabled} onChange={() => void toggleEnabled(s)} />
              </label>
            </div>
            <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, minHeight: 36 }}>
              {s.description || "暂无简介"}
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 2 }}>
              <button
                className="btn-primary"
                style={{ flex: 1, padding: "6px 0", fontSize: 12 }}
                disabled={!s.enabled}
                title={s.enabled ? `调用 ${s.name} 开启对话` : "已停用"}
                onClick={() => onActivate(s.name)}
              >
                ▶ 调用
              </button>
              <button
                style={{
                  flexShrink: 0,
                  padding: "6px 12px",
                  fontSize: 12,
                  border: "1px solid rgba(148,163,184,0.4)",
                  borderRadius: 6,
                  background: "#fff",
                  color: "var(--text-secondary)",
                  cursor: "pointer",
                }}
                title="一键验证技能能否加载/工具是否齐全"
                onClick={() => void testSkill(s)}
                disabled={testing === s.name}
              >
                {testing === s.name ? "测试中…" : "🔍 测试"}
              </button>
              <button
                style={{
                  flexShrink: 0,
                  padding: "6px 12px",
                  fontSize: 12,
                  border: "1px solid rgba(220,38,38,0.3)",
                  borderRadius: 6,
                  background: "rgba(254,226,226,0.4)",
                  color: "#dc2626",
                  cursor: "pointer",
                }}
                title="删除技能"
                onClick={() => void removeSkill(s)}
              >
                🗑 删除
              </button>
            </div>
            {testResult && testResult.name === s.name && (
              <div
                style={{
                  fontSize: 11, padding: "8px 10px", borderRadius: 8,
                  background: testResult.ok ? "rgba(209,250,229,0.5)" : "rgba(254,226,226,0.5)",
                  color: testResult.ok ? "#047857" : "#b91c1c",
                  lineHeight: 1.6,
                }}
              >
                {testResult.ok ? "✅ 技能正常" : "❌ 技能存在问题"} · 工具数 {testResult.checks.length ? "" : "—"}
                {testResult.checks.map((c) => (
                  <div key={c.name}>
                    {c.ok ? "✓" : "✗"} {c.name}: {c.detail}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
