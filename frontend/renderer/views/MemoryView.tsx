// Phase 1 Task 12 - 记忆视图
// 记忆列表(接 /admin/memories) + 手动提取(接 /admin/sessions/{id}/extract_memory)
import { useCallback, useEffect, useState } from "react";

import { adminFetch } from "../utils/apiClient";

const API_BASE = "http://localhost:8765/admin";

interface MemoryItem {
  id: number;
  type: "preference" | "fact" | "todo" | "decision" | string;
  content: string;
  importance: number;
  source_session_id: number | null;
  created_at: string | null;
  last_accessed_at: string | null;
  access_count: number;
}

const TYPE_META: Record<string, { label: string; color: string }> = {
  preference: { label: "偏好", color: "#7c3aed" },
  fact: { label: "事实", color: "#059669" },
  todo: { label: "待办", color: "#d97706" },
  decision: { label: "决策", color: "#2563eb" },
};

export default function MemoryView({
  sessionId,
}: {
  sessionId: number;
}): JSX.Element {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [typeFilter, setTypeFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [extractSession, setExtractSession] = useState(String(sessionId));
  const [extracting, setExtracting] = useState(false);
  const [extractMsg, setExtractMsg] = useState<string | null>(null);

  const loadMemories = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (typeFilter) params.set("type", typeFilter);
      const resp = await adminFetch(`${API_BASE}/memories?${params.toString()}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setMemories(await resp.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [typeFilter]);

  useEffect(() => {
    void loadMemories();
  }, [loadMemories]);

  const doExtract = async (): Promise<void> => {
    const sid = Number(extractSession);
    if (!Number.isInteger(sid) || sid <= 0) {
      setExtractMsg("请输入有效的会话 ID");
      return;
    }
    setExtracting(true);
    setExtractMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/sessions/${sid}/extract_memory`, {
        method: "POST",
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setExtractMsg(
        `提取完成: ${data.count} 条记忆 (${(data.types ?? []).join(", ") || "无"})`
      );
      void loadMemories();
    } catch (e) {
      setExtractMsg(`提取失败: ${String(e)}`);
    } finally {
      setExtracting(false);
    }
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16, overflowY: "auto", paddingRight: 4 }}>
      <div className="stat-card animate-in delay-1">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <input
            className="flow-input"
            style={{ width: 220 }}
            placeholder="会话 ID"
            value={extractSession}
            onChange={(e) => setExtractSession(e.target.value)}
          />
          <button className="btn-primary" onClick={() => void doExtract()} disabled={extracting}>
            {extracting ? "提取中…" : "手动提取记忆"}
          </button>
          {extractMsg && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{extractMsg}</span>}
        </div>
      </div>

      <div className="glass-panel animate-in delay-2" style={{ padding: "20px 24px", flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em" }}>记忆列表</span>
          <div style={{ display: "flex", gap: 6 }}>
            {[
              { v: "", l: "全部" },
              { v: "preference", l: "偏好" },
              { v: "fact", l: "事实" },
              { v: "todo", l: "待办" },
              { v: "decision", l: "决策" },
            ].map((f) => (
              <button
                key={f.v}
                className={`nav-item${typeFilter === f.v ? " active" : ""}`}
                style={{ width: "auto", padding: "6px 12px", fontSize: 12 }}
                onClick={() => setTypeFilter(f.v)}
              >
                {f.l}
              </button>
            ))}
          </div>
        </div>

        {error && <div style={{ fontSize: 12, color: "var(--danger-text)", marginBottom: 12 }}>{error}</div>}
        {loading && <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>加载中…</div>}
        {!loading && memories.length === 0 && (
          <div style={{ fontSize: 13, color: "var(--text-tertiary)", textAlign: "center", padding: "40px 0" }}>
            暂无记忆。聊几轮后系统会自动提取,或使用上方手动提取。
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {memories.map((m) => {
            const meta = TYPE_META[m.type] ?? { label: m.type, color: "#64748b" };
            return (
              <div
                key={m.id}
                style={{
                  display: "flex", alignItems: "flex-start", gap: 12,
                  padding: "12px 14px", borderRadius: "var(--radius-sm)",
                  background: "rgba(255,255,255,0.5)",
                }}
              >
                <span
                  style={{
                    flexShrink: 0, fontSize: 11, fontWeight: 600,
                    padding: "2px 8px", borderRadius: 10,
                    background: `${meta.color}18`, color: meta.color,
                  }}
                >
                  {meta.label}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, lineHeight: 1.6, color: "var(--text-primary)", wordBreak: "break-word" }}>
                    {m.content}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
                    重要度 {m.importance?.toFixed(2) ?? "—"} · 访问 {m.access_count} 次
                    {m.created_at ? ` · ${new Date(m.created_at).toLocaleString()}` : ""}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
