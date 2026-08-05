// FlowSpace 侧边栏导航 + 历史任务树(Phase 1.5)
// 导航 + 任务树(可展开/收起, 点击切换会话触发后端 WS replay 加载历史)
import { useCallback, useEffect, useState } from "react";

import { adminFetch } from "../utils/apiClient";

export type ViewKey = "home" | "chat" | "knowledge" | "memory" | "agents" | "settings";

export interface SessionItem {
  id: number;
  title: string | null;
  status: string;
  folder: string | null;
  locked_skill_name: string | null;
  last_turn: number;
  user_msg_count?: number;
  updated_at: string | null;
  model_id?: string | null; // 会话选择的模型(auto 为 null/空)
}

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const WORKSPACE_ITEMS: { key: ViewKey; label: string; icon: JSX.Element }[] = [
  {
    key: "home",
    label: "首页",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" style={stroke}>
        <path d="M3 9.5L12 3l9 6.5V20a1 1 0 0 1-1 1h-5v-7h-6v7H4a1 1 0 0 1-1-1V9.5z" />
      </svg>
    ),
  },
  {
    key: "chat",
    label: "对话",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" style={stroke}>
        <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
      </svg>
    ),
  },
  {
    key: "knowledge",
    label: "知识库",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" style={stroke}>
        <ellipse cx="12" cy="5" rx="9" ry="3" />
        <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
        <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
      </svg>
    ),
  },
  {
    key: "memory",
    label: "记忆",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" style={stroke}>
        <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
      </svg>
    ),
  },
  {
    key: "agents",
    label: "技能",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" style={stroke}>
        <path d="M12 2a3 3 0 0 1 3 3v1h4a2 2 0 0 1 2 2v4h1a3 3 0 0 1 0 6h-1v4a2 2 0 0 1-2 2h-4v1a3 3 0 0 1-6 0v-1H5a2 2 0 0 1-2-2v-4H2a3 3 0 0 1 0-6h1V8a2 2 0 0 1 2-2h4V5a3 3 0 0 1 3-3z" />
      </svg>
    ),
  },
];

const SYSTEM_ITEMS: { key: ViewKey; label: string; icon: JSX.Element }[] = [
  {
    key: "settings",
    label: "设置",
    icon: (
      <svg width="18" height="18" viewBox="0 0 24 24" style={stroke}>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
      </svg>
    ),
  },
];

function NavLabel({ children }: { children: string }): JSX.Element {
  return (
    <div
      style={{
        fontSize: 10,
        fontWeight: 600,
        textTransform: "uppercase",
        letterSpacing: "0.08em",
        color: "var(--text-tertiary)",
        padding: "0 12px",
        margin: "12px 0 6px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      {children}
    </div>
  );
}

function TaskTree({
  currentSessionId,
  onSwitchSession,
  onNavigate,
}: {
  currentSessionId: number | null;
  onSwitchSession: (id: number, skillName?: string | null, modelId?: string | null) => void;
  // V1.4-8.4: 跨模块搜索结果跳转(技能/知识库视图)
  onNavigate: (v: ViewKey) => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // V1.1-3.1 会话管理: 内联重命名 + hover 操作 + 归档折叠
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renamingValue, setRenamingValue] = useState("");
  const [hoverId, setHoverId] = useState<number | null>(null);
  const [archivedOpen, setArchivedOpen] = useState(false);
  // V1.1-3.2 搜索 + 导出; V1.4-8.4 升级为跨模块(会话/技能/知识库)
  const [searchQ, setSearchQ] = useState("");
  const [searchResults, setSearchResults] = useState<GlobalHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [exportFor, setExportFor] = useState<number | null>(null);

  interface GlobalHit {
    type: "session" | "skill" | "kb";
    id: number | string;
    title: string;
    detail: string;
  }

  const runSearch = async (q: string): Promise<void> => {
    const query = q.trim();
    if (query.length < 2) {
      setSearchResults([]);
      return;
    }
    setSearching(true);
    try {
      // 并行: 会话全文 + 技能 + 知识库
      const [sessResp, skillResp, kbResp] = await Promise.all([
        adminFetch(
          `http://127.0.0.1:8765/admin/sessions/search?q=${encodeURIComponent(query)}`
        ),
        adminFetch(`http://127.0.0.1:8765/admin/skills`),
        adminFetch(`http://127.0.0.1:8765/admin/knowledge`),
      ]);
      const hits: GlobalHit[] = [];
      if (sessResp.ok) {
        const rows = await sessResp.json();
        for (const r of rows.slice(0, 8)) {
          hits.push({
            type: "session",
            id: r.id,
            title: r.title ?? `#${r.id}`,
            detail: r.hit_snippet || `会话 #${r.id}`,
          });
        }
      }
      if (skillResp.ok) {
        const rows = await skillResp.json();
        for (const s of rows) {
          if (s.name.toLowerCase().includes(query.toLowerCase()) ||
              String(s.description ?? "").toLowerCase().includes(query.toLowerCase())) {
            hits.push({ type: "skill", id: s.name, title: s.name, detail: (s.description ?? "").slice(0, 60) });
          }
        }
      }
      if (kbResp.ok) {
        const kb = await kbResp.json();
        for (const b of kb.bases ?? []) {
          const matched = (b.scenario ?? "").toLowerCase().includes(query.toLowerCase()) ||
            (b.documents ?? []).some((d: { source: string }) => (d.source ?? "").toLowerCase().includes(query.toLowerCase()));
          if (matched) {
            hits.push({
              type: "kb",
              id: b.scenario,
              title: b.scenario,
              detail: `${b.documents?.length ?? 0} 文档 · ${b.chunks ?? 0} 片段`,
            });
          }
        }
      }
      setSearchResults(hits);
    } catch {
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  };

  const exportSession = async (id: number, format: "md" | "json"): Promise<void> => {
    setExportFor(null);
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/sessions/${id}/export?format=${format}`
      );
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      const blob = new Blob(
        [typeof data.content === "string" ? data.content : JSON.stringify(data.content, null, 2)],
        { type: format === "md" ? "text/markdown" : "application/json" }
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `session-${id}.${format === "md" ? "md" : "json"}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(`导出失败: ${String(e)}`);
    }
  };

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError("");
    try {
      const resp = await adminFetch("http://127.0.0.1:8765/admin/sessions?limit=50");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setSessions(await resp.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (expanded && sessions.length === 0 && !loading) {
      void load();
    }
  }, [expanded, sessions.length, loading, load]);

  const createSession = async (): Promise<void> => {
    try {
      const resp = await adminFetch("http://127.0.0.1:8765/admin/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      if (typeof data.id === "number") {
        onSwitchSession(data.id);
        setExpanded(true);
        void load();
      }
    } catch (e) {
      setError(`新建失败: ${String(e)}`);
    }
  };

  const patchSession = async (id: number, body: unknown): Promise<boolean> => {
    try {
      const resp = await adminFetch(`http://127.0.0.1:8765/admin/sessions/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      void load();
      return true;
    } catch (e) {
      setError(`操作失败: ${String(e)}`);
      return false;
    }
  };

  const setFolder = async (id: number, folder: string | null): Promise<void> => {
    try {
      const resp = await adminFetch(`http://127.0.0.1:8765/admin/sessions/${id}/folder`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ folder }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      void load();
    } catch (e) {
      setError(`移动失败: ${String(e)}`);
    }
  };

  const startRename = (s: SessionItem): void => {
    setRenamingId(s.id);
    setRenamingValue(s.title ?? "");
  };

  const commitRename = async (id: number): Promise<void> => {
    const title = renamingValue.trim();
    setRenamingId(null);
    if (title !== "") await patchSession(id, { title });
  };

  const toggleArchive = (s: SessionItem): void => {
    void patchSession(s.id, { status: s.status === "archived" ? "active" : "archived" });
  };

  const promptFolder = (s: SessionItem): void => {
    const folder = window.prompt("输入文件夹名(留空移出分组)", s.folder ?? "");
    if (folder === null) return;
    void setFolder(s.id, folder.trim() || null);
  };

  const deleteSession = async (id: number): Promise<void> => {
    try {
      await adminFetch(`http://127.0.0.1:8765/admin/sessions/${id}`, { method: "DELETE" });
      setSessions((prev) => prev.filter((s) => s.id !== id));
    } catch (e) {
      setError(`删除失败: ${String(e)}`);
    }
  };

  // 分组: 活跃会话按文件夹分组(folder 为 null 归"未分组"), 归档会话独立折叠区
  const activeSessions = sessions.filter((s) => s.status !== "archived");
  const archivedSessions = sessions.filter((s) => s.status === "archived");
  const folderOrder: string[] = [];
  const groups = new Map<string, SessionItem[]>();
  for (const s of activeSessions) {
    const key = s.folder ?? "";
    if (!groups.has(key)) {
      groups.set(key, []);
      folderOrder.push(key);
    }
    groups.get(key)!.push(s);
  }

  const renderSessionRow = (s: SessionItem): JSX.Element => {
    const isCurrent = currentSessionId === s.id;
    const showActions = hoverId === s.id || isCurrent;
    return (
      <div
        key={s.id}
        className="nav-item"
        onClick={() => onSwitchSession(s.id, s.locked_skill_name, s.model_id)}
        onDoubleClick={() => startRename(s)}
        onMouseEnter={() => setHoverId(s.id)}
        onMouseLeave={() => setHoverId(null)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 4,
          width: "100%",
          padding: "6px 8px",
          fontSize: 12,
          borderRadius: 6,
          cursor: "pointer",
          position: "relative",
          background: isCurrent ? "rgba(255,255,255,0.8)" : "transparent",
          color: isCurrent ? "var(--text-primary)" : "var(--text-secondary)",
          fontWeight: isCurrent ? 600 : 400,
        }}
        title={`#${s.id} · ${s.last_turn} 轮 · ${s.user_msg_count ?? 0} 条用户消息${s.folder ? ` · 文件夹:${s.folder}` : ""}`}
      >
        {renamingId === s.id ? (
          <input
            autoFocus
            value={renamingValue}
            onChange={(e) => setRenamingValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void commitRename(s.id);
              if (e.key === "Escape") setRenamingId(null);
            }}
            onBlur={() => void commitRename(s.id)}
            onClick={(e) => e.stopPropagation()}
            style={{
              flex: 1,
              minWidth: 0,
              fontSize: 12,
              border: "1px solid var(--border)",
              borderRadius: 4,
              padding: "2px 4px",
              background: "#fff",
              color: "var(--text-primary)",
              outline: "none",
            }}
          />
        ) : (
          <span style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 2, flex: 1, minWidth: 0 }}>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}>
              {s.title || `#${s.id}`}
            </span>
            <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
              {s.locked_skill_name ?? "无 skill"}
              {s.last_turn > 0 ? ` · ${s.last_turn} 轮` : ""}
              {s.user_msg_count ? ` · ${s.user_msg_count} 条` : ""}
            </span>
          </span>
        )}
        {showActions && renamingId !== s.id && (
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              flexShrink: 0,
              marginLeft: 2,
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <ActionBtn label="✎" title="重命名" onClick={() => startRename(s)} />
            <ActionBtn label="📁" title={s.folder ? `移到文件夹(当前:${s.folder})` : "移到文件夹"} onClick={() => promptFolder(s)} />
            <ActionBtn
              label={s.status === "archived" ? "▶" : "⏸"}
              title={s.status === "archived" ? "恢复会话" : "归档会话"}
              onClick={() => toggleArchive(s)}
            />
            <ActionBtn label="⤓" title="导出(MD/JSON)" onClick={() => setExportFor(exportFor === s.id ? null : s.id)} />
            <ActionBtn
              label="×"
              title="删除此会话"
              danger
              onClick={() => {
                if (window.confirm(`删除会话 #${s.id}?该会话的所有消息也会被删除(不可恢复)`)) {
                  void deleteSession(s.id);
                }
              }}
            />
          </span>
        )}
        {exportFor === s.id && showActions && (
          <span
            style={{
              position: "absolute",
              right: 4,
              top: 26,
              zIndex: 30,
              display: "flex",
              flexDirection: "column",
              gap: 2,
              background: "rgba(255,255,255,0.95)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: 4,
              boxShadow: "0 4px 12px rgba(148,163,184,0.2)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              style={exportMenuBtnStyle}
              onClick={() => void exportSession(s.id, "md")}
            >
              ⤓ 导出 Markdown
            </button>
            <button
              style={exportMenuBtnStyle}
              onClick={() => void exportSession(s.id, "json")}
            >
              ⤓ 导出 JSON
            </button>
          </span>
        )}
      </div>
    );
  };

  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
        <button
          onClick={() => setExpanded(!expanded)}
          className="nav-item"
          style={{ flex: 1, justifyContent: "space-between" }}
        >
          <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <svg width="18" height="18" viewBox="0 0 24 24" style={stroke}>
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
            历史任务
          </span>
          <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            {expanded ? "▾" : "▸"}
          </span>
        </button>
        <button
          onClick={() => void createSession()}
          title="新建会话"
          style={{
            flexShrink: 0,
            width: 24,
            height: 24,
            border: "1px solid rgba(148,163,184,0.3)",
            background: "rgba(255,255,255,0.6)",
            color: "var(--text-secondary)",
            fontSize: 15,
            lineHeight: 1,
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          +
        </button>
      </div>
      {expanded && (
        <div
          style={{
            marginTop: 4,
            marginLeft: 14,
            paddingLeft: 10,
            borderLeft: "1px solid rgba(148,163,184,0.2)",
            display: "flex",
            flexDirection: "column",
            gap: 2,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "4px 8px",
              fontSize: 11,
              color: "var(--text-tertiary)",
            }}
          >
            <span>最近 {sessions.length} 个</span>
            <button
              onClick={() => void load()}
              style={{
                fontSize: 11,
                border: "none",
                background: "transparent",
                color: "var(--text-tertiary)",
                cursor: "pointer",
                padding: 2,
              }}
            >
              {loading ? "…" : "刷新"}
            </button>
          </div>
          {/* V1.1-3.2 会话全文搜索 */}
          <div style={{ padding: "0 8px 6px" }}>
            <input
              value={searchQ}
              onChange={(e) => {
                setSearchQ(e.target.value);
                void runSearch(e.target.value);
              }}
              placeholder="🔍 搜索(会话/技能/知识库)…"
              style={{
                width: "100%",
                boxSizing: "border-box",
                fontSize: 12,
                border: "1px solid var(--border)",
                borderRadius: 6,
                padding: "5px 8px",
                background: "rgba(255,255,255,0.7)",
                color: "var(--text-primary)",
                outline: "none",
              }}
            />
            {searchResults.length > 0 && (
              <div
                style={{
                  marginTop: 4,
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  maxHeight: 240,
                  overflowY: "auto",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  background: "rgba(255,255,255,0.95)",
                  padding: 4,
                }}
              >
                {/* V1.4-8.4: 跨模块分组显示 */}
                {(["session", "skill", "kb"] as const).map((type) => {
                  const list = searchResults.filter((h) => h.type === type);
                  if (list.length === 0) return null;
                  const label = { session: "会话", skill: "技能", kb: "知识库" }[type];
                  return (
                    <div key={type}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-tertiary)", padding: "3px 6px 1px" }}>
                        {label}
                      </div>
                      {list.map((h) => (
                        <div
                          key={`${type}-${h.id}`}
                          onClick={() => {
                            setSearchQ("");
                            setSearchResults([]);
                            if (h.type === "session") {
                              onSwitchSession(Number(h.id));
                            } else if (h.type === "skill") {
                              onNavigate("agents");
                            } else {
                              onNavigate("knowledge");
                            }
                          }}
                          style={{
                            padding: "5px 8px",
                            borderRadius: 4,
                            cursor: "pointer",
                            fontSize: 11,
                            color: "var(--text-primary)",
                          }}
                          onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(139,92,246,0.08)"; }}
                          onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                        >
                          <div style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {h.type === "session" ? "💬 " : h.type === "skill" ? "🧩 " : "📚 "}
                            {h.title}
                          </div>
                          <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 2, lineHeight: 1.4 }}>
                            {h.detail || "—"}
                          </div>
                        </div>
                      ))}
                    </div>
                  );
                })}
              </div>
            )}
            {searching && (
              <div style={{ fontSize: 10, color: "var(--text-tertiary)", padding: "2px 2px 0" }}>
                搜索中…
              </div>
            )}
          </div>
          {error && (
            <div style={{ fontSize: 11, color: "var(--danger-text)", padding: "4px 8px" }}>
              {error}
            </div>
          )}
          {!loading && sessions.length === 0 && !error && (
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", padding: "4px 8px" }}>
              暂无历史会话, 点击 + 新建
            </div>
          )}
          {folderOrder.map((key) => (
            <div key={key || "__unfiled__"} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 8px 2px",
                  fontSize: 11,
                  fontWeight: 600,
                  color: "var(--text-tertiary)",
                }}
              >
                {key ? `📁 ${key}` : "未分组"}
                <span style={{ fontWeight: 400, fontSize: 10 }}>({groups.get(key)!.length})</span>
              </div>
              {groups.get(key)!.map(renderSessionRow)}
            </div>
          ))}
          {archivedSessions.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 4 }}>
              <button
                onClick={() => setArchivedOpen(!archivedOpen)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "4px 8px",
                  fontSize: 11,
                  color: "var(--text-tertiary)",
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                <span>🗄 已归档 ({archivedSessions.length})</span>
                <span>{archivedOpen ? "▾" : "▸"}</span>
              </button>
              {archivedOpen && (
                <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  {archivedSessions.map(renderSessionRow)}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const exportMenuBtnStyle: React.CSSProperties = {
  fontSize: 11,
  border: "none",
  background: "transparent",
  color: "var(--text-secondary)",
  cursor: "pointer",
  padding: "4px 8px",
  borderRadius: 4,
  textAlign: "left",
  whiteSpace: "nowrap",
};

function ActionBtn({
  label,
  title,
  onClick,
  danger,
}: {
  label: string;
  title: string;
  onClick: () => void;
  danger?: boolean;
}): JSX.Element {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        width: 20,
        height: 20,
        border: "none",
        background: "transparent",
        color: danger ? "var(--text-tertiary)" : "var(--text-tertiary)",
        fontSize: 11,
        cursor: "pointer",
        borderRadius: 4,
        padding: 0,
        lineHeight: 1,
      }}
      onMouseEnter={(e) => { e.currentTarget.style.color = danger ? "var(--danger-text)" : "var(--text-primary)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-tertiary)"; }}
    >
      {label}
    </button>
  );
}

export default function Sidebar({
  active,
  onChange,
  currentSessionId,
  onSwitchSession,
  status,
  width = 220,
}: {
  active: ViewKey;
  onChange: (v: ViewKey) => void;
  currentSessionId: number | null;
  onSwitchSession: (id: number, skillName?: string | null, modelId?: string | null) => void;
  status: "connected" | "disconnected" | "reconnecting";
  /** V1.1 布局优化: 展开时的宽度(外部拖拽控制), 折叠仍为 44px */
  width?: number;
}): JSX.Element {
  const [collapsed, setCollapsed] = useState(false);

  const statusColor =
    status === "connected" ? "#4caf50" :
    status === "reconnecting" ? "#ff9800" : "#f44336";
  const statusLabel =
    status === "connected" ? "已连接" :
    status === "reconnecting" ? "重连中" : "未连接";

  const renderItem = (item: { key: ViewKey; label: string; icon: JSX.Element }): JSX.Element => (
    <button
      key={item.key}
      className={`nav-item${active === item.key ? " active" : ""}`}
      onClick={() => onChange(item.key)}
      style={{
        width: collapsed ? "100%" : "100%",
        justifyContent: collapsed ? "center" : "flex-start",
        padding: collapsed ? "10px 0" : "10px 12px",
      }}
      title={collapsed ? item.label : undefined}
    >
      <span style={{ display: "flex", alignItems: "center", width: 20, justifyContent: "center", opacity: active === item.key ? 1 : 0.7 }}>
        {item.icon}
      </span>
      {!collapsed && item.label}
    </button>
  );

  return (
    <nav
      className="glass-sidebar"
      style={{
        width: collapsed ? 44 : width,
        flexShrink: 0,
        padding: collapsed ? "20px 6px 16px" : "24px 16px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 4,
        height: "100%",
        minHeight: 0,
        overflowY: "auto",
        boxSizing: "border-box",
        transition: "width 0.3s var(--transition-smooth), padding 0.3s var(--transition-smooth)",
        alignItems: collapsed ? "center" : "stretch",
      }}
    >
      {/* 折叠控制条: 按钮独占一行(展开靠右, 收起居中), 与右栏按钮同一水平高度 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: collapsed ? "center" : "flex-end",
          minHeight: 28,
          marginBottom: collapsed ? 12 : 16,
          whiteSpace: "nowrap",
        }}
      >
        <button
          onClick={() => setCollapsed(!collapsed)}
          style={{
            width: 28,
            height: 28,
            borderRadius: 8,
            border: "1px solid rgba(148,163,184,0.15)",
            background: "rgba(255,255,255,0.5)",
            cursor: "pointer",
            color: "var(--text-secondary)",
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
          title={collapsed ? "展开侧边栏" : "收起侧边栏"}
        >
          {collapsed ? "»" : "«"}
        </button>
      </div>

      {!collapsed && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "4px 12px 20px",
            borderBottom: "1px solid rgba(148,163,184,0.15)",
            marginBottom: 8,
            width: "100%",
            boxSizing: "border-box",
          }}
        >
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 12,
              background: "var(--gradient-logo)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#fff",
              fontWeight: 700,
              fontSize: 16,
              fontFamily: "var(--font-sans)",
              boxShadow: "0 4px 12px rgba(139,92,246,0.25)",
              flexShrink: 0,
            }}
          >
            智
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: "var(--text-primary)", fontFamily: "var(--font-sans)", letterSpacing: "-0.02em", whiteSpace: "nowrap" }}>
              私人智能体
            </div>
            <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>私人智能体 v2.1</div>
          </div>
        </div>
      )}

      {collapsed ? (
        <div
          style={{
            writingMode: "vertical-rl",
            fontSize: 12,
            color: "var(--text-tertiary)",
            margin: "auto 0",
            letterSpacing: "0.1em",
          }}
        >
          导航
        </div>
      ) : (
        <>
          <NavLabel>工作区</NavLabel>
          {WORKSPACE_ITEMS.map(renderItem)}

          <TaskTree
            currentSessionId={currentSessionId}
            onSwitchSession={onSwitchSession}
            onNavigate={onChange}
          />

          <NavLabel>系统</NavLabel>
          {SYSTEM_ITEMS.map(renderItem)}

          <div
            style={{
              marginTop: "auto",
              padding: 12,
              display: "flex",
              alignItems: "center",
              gap: 10,
              background: "rgba(255,255,255,0.5)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            <div
              style={{
                width: 34,
                height: 34,
                borderRadius: 10,
                background: "linear-gradient(135deg, #f472b6, #c084fc)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "#fff",
                fontWeight: 600,
                fontSize: 14,
                flexShrink: 0,
              }}
            >
              Z
            </div>
            <div style={{ fontSize: 13, flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600, lineHeight: 1.2 }}>本地用户</div>
              <div style={{ fontSize: 11, color: "var(--text-tertiary)", display: "flex", alignItems: "center", gap: 4 }}>
                <span
                  style={{
                    display: "inline-block",
                    width: 7,
                    height: 7,
                    borderRadius: "50%",
                    backgroundColor: statusColor,
                  }}
                />
                {statusLabel}
              </div>
            </div>
          </div>
        </>
      )}
    </nav>
  );
}
