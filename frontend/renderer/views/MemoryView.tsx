// V1.3-7.1 长期记忆系统完善
// 记忆列表(检索/类型过滤) + 手动新增 + 软删除 + 手动提取 + 注入配置
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

interface MemoryConfig {
  enabled: boolean;
  inject_limit: number;
  extract_interval_turns: number;
  eviction: {
    max_active_count: number;
    min_importance_threshold: number;
    expire_days: number;
  };
}

const TYPE_META: Record<string, { label: string; color: string }> = {
  preference: { label: "偏好", color: "#7c3aed" },
  fact: { label: "事实", color: "#059669" },
  todo: { label: "待办", color: "#d97706" },
  decision: { label: "决策", color: "#2563eb" },
  correction: { label: "修正", color: "#db2777" },
};

const TYPES = ["preference", "fact", "todo", "decision", "correction"];

export default function MemoryView({
  sessionId,
  onOpenSession,
}: {
  sessionId: number;
  // V1.5 规划项-8: 记忆来源跳转回调(定位来源会话, 由 App 提供)
  onOpenSession?: (sessionId: number) => void;
}): JSX.Element {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [typeFilter, setTypeFilter] = useState("");
  const [searchQ, setSearchQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [extractSession, setExtractSession] = useState(String(sessionId));
  const [extracting, setExtracting] = useState(false);
  const [extractMsg, setExtractMsg] = useState<string | null>(null);

  // 手动新增
  const [newContent, setNewContent] = useState("");
  const [newType, setNewType] = useState("fact");
  const [newImportance, setNewImportance] = useState(0.5);
  const [adding, setAdding] = useState(false);
  const [addMsg, setAddMsg] = useState<string | null>(null);

  // 注入配置
  const [cfg, setCfg] = useState<MemoryConfig | null>(null);
  const [cfgSaving, setCfgSaving] = useState(false);
  const [cfgMsg, setCfgMsg] = useState<string | null>(null);

  const loadMemories = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (typeFilter) params.set("type", typeFilter);
      if (searchQ.trim()) params.set("q", searchQ.trim());
      const resp = await adminFetch(`${API_BASE}/memories?${params.toString()}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setMemories(await resp.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [typeFilter, searchQ]);

  useEffect(() => {
    void loadMemories();
  }, [loadMemories]);

  const loadConfig = useCallback(async (): Promise<void> => {
    try {
      const resp = await adminFetch(`${API_BASE}/settings/memory`);
      if (resp.ok) setCfg(await resp.json());
    } catch {
      setCfg(null);
    }
  }, []);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

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

  const addMemory = async (): Promise<void> => {
    const content = newContent.trim();
    if (!content) {
      setAddMsg("记忆内容不能为空");
      return;
    }
    setAdding(true);
    setAddMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/memories`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          type: newType,
          importance: newImportance,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setAddMsg(`已新增 #${data.id}`);
      setNewContent("");
      void loadMemories();
    } catch (e) {
      setAddMsg(`新增失败: ${String(e)}`);
    } finally {
      setAdding(false);
    }
  };

  const deleteMemory = async (m: MemoryItem): Promise<void> => {
    if (!window.confirm(`删除这条记忆?(${m.content.slice(0, 40)}…)`)) return;
    try {
      const resp = await adminFetch(`${API_BASE}/memories/${m.id}`, {
        method: "DELETE",
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setMemories((prev) => prev.filter((x) => x.id !== m.id));
    } catch (e) {
      setError(`删除失败: ${String(e)}`);
    }
  };

  const saveConfig = async (): Promise<void> => {
    if (!cfg) return;
    setCfgSaving(true);
    setCfgMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/settings/memory`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: cfg.enabled,
          inject_limit: Number(cfg.inject_limit),
          extract_interval_turns: Number(cfg.extract_interval_turns),
          eviction_max_active_count: Number(cfg.eviction.max_active_count),
          eviction_min_importance_threshold: Number(
            cfg.eviction.min_importance_threshold
          ),
          eviction_expire_days: Number(cfg.eviction.expire_days),
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setCfgMsg("已保存,下一轮生效");
    } catch (e) {
      setCfgMsg(`保存失败: ${String(e)}`);
    } finally {
      setCfgSaving(false);
    }
  };

  const numInput = (
    v: number,
    onChange: (n: number) => void
  ): JSX.Element => (
    <input
      type="number"
      value={String(v)}
      onChange={(e) => onChange(Number(e.target.value))}
      style={{
        width: 76, padding: "4px 8px", borderRadius: 6, fontSize: 12,
        border: "1px solid rgba(148,163,184,0.3)",
        background: "rgba(255,255,255,0.6)",
      }}
    />
  );

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16, overflowY: "auto", paddingRight: 4 }}>
      {/* 操作行: 提取 + 新增 */}
      <div className="stat-card animate-in delay-1" style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
        <input
          className="flow-input"
          style={{ width: 110 }}
          placeholder="会话 ID"
          value={extractSession}
          onChange={(e) => setExtractSession(e.target.value)}
        />
        <button className="btn-primary" onClick={() => void doExtract()} disabled={extracting}>
          {extracting ? "提取中…" : "手动提取"}
        </button>
        {extractMsg && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{extractMsg}</span>}
        <span style={{ width: 1, height: 26, background: "rgba(148,163,184,0.25)" }} />
        <input
          className="flow-input"
          style={{ flex: 1, minWidth: 200 }}
          placeholder="新增记忆内容(如: 用户偏好用中文注释)"
          value={newContent}
          onChange={(e) => setNewContent(e.target.value)}
        />
        <select
          value={newType}
          onChange={(e) => setNewType(e.target.value)}
          style={{ padding: "6px 8px", borderRadius: 6, fontSize: 12, border: "1px solid rgba(148,163,184,0.3)", background: "rgba(255,255,255,0.6)" }}
        >
          {TYPES.map((t) => (
            <option key={t} value={t}>{TYPE_META[t]?.label ?? t}</option>
          ))}
        </select>
        <input
          type="number"
          min={0}
          max={1}
          step={0.1}
          value={String(newImportance)}
          onChange={(e) => setNewImportance(Number(e.target.value))}
          title="重要度 0~1"
          style={{ width: 56, padding: "6px 8px", borderRadius: 6, fontSize: 12, border: "1px solid rgba(148,163,184,0.3)", background: "rgba(255,255,255,0.6)" }}
        />
        <button
          className="btn-primary"
          style={{ background: "#7c3aed" }}
          onClick={() => void addMemory()}
          disabled={adding}
        >
          {adding ? "…" : "+ 新增记忆"}
        </button>
        {addMsg && <span style={{ fontSize: 12, color: addMsg.startsWith("已新增") ? "#059669" : "#dc2626" }}>{addMsg}</span>}
      </div>

      {/* 注入配置 */}
      <div className="glass-panel animate-in delay-1" style={{ padding: "14px 20px" }}>
        <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 14 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>记忆注入配置</span>
          {cfg && (
            <>
              <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                <input
                  type="checkbox"
                  checked={cfg.enabled}
                  onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })}
                />
                启用
              </label>
              <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
                注入上限 {numInput(cfg.inject_limit, (n) => setCfg({ ...cfg, inject_limit: n }))}
              </label>
              <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
                提取间隔(轮) {numInput(cfg.extract_interval_turns, (n) => setCfg({ ...cfg, extract_interval_turns: n }))}
              </label>
              <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
                上限(条) {numInput(cfg.eviction.max_active_count, (n) => setCfg({ ...cfg, eviction: { ...cfg.eviction, max_active_count: n } }))}
              </label>
              <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6 }}>
                过期(天) {numInput(cfg.eviction.expire_days, (n) => setCfg({ ...cfg, eviction: { ...cfg.eviction, expire_days: n } }))}
              </label>
              <button className="btn-primary" style={{ padding: "4px 14px", fontSize: 12 }} onClick={() => void saveConfig()} disabled={cfgSaving}>
                {cfgSaving ? "…" : "保存"}
              </button>
              {cfgMsg && <span style={{ fontSize: 11, color: cfgMsg.startsWith("已保存") ? "#059669" : "#dc2626" }}>{cfgMsg}</span>}
            </>
          )}
        </div>
      </div>

      <div className="glass-panel animate-in delay-2" style={{ padding: "20px 24px", flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, flexWrap: "wrap", gap: 8 }}>
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em" }}>记忆列表</span>
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <input
              className="flow-input"
              style={{ width: 180 }}
              placeholder="🔍 搜索记忆内容…"
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
            />
            {[
              { v: "", l: "全部" },
              { v: "preference", l: "偏好" },
              { v: "fact", l: "事实" },
              { v: "todo", l: "待办" },
              { v: "decision", l: "决策" },
              { v: "correction", l: "修正" },
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
            {searchQ ? "没有匹配的记忆。" : "暂无记忆。聊几轮后系统会自动提取,或使用上方手动提取/新增。"}
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
                  {/* V1.5 规划项-8: 记忆来源跳转(点击定位来源会话) */}
                  {m.source_session_id != null && onOpenSession && (
                    <button
                      onClick={() => onOpenSession(m.source_session_id!)}
                      title={`跳转到来源会话 #${m.source_session_id}`}
                      style={{
                        marginTop: 6, fontSize: 11, padding: "2px 10px",
                        borderRadius: 10, border: "1px solid rgba(139,92,246,0.3)",
                        background: "rgba(237,233,254,0.5)", color: "#6d28d9",
                        cursor: "pointer",
                      }}
                    >
                      ↪ 来源会话 #{m.source_session_id}
                    </button>
                  )}
                </div>
                <button
                  onClick={() => void deleteMemory(m)}
                  title="删除此记忆(软删除)"
                  style={{
                    flexShrink: 0, width: 26, height: 26, border: "none",
                    background: "transparent", color: "var(--text-tertiary)",
                    fontSize: 14, cursor: "pointer", borderRadius: 4,
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = "var(--danger-text)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-tertiary)"; }}
                >
                  ×
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
