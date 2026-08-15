// FlowSpace 侧边栏导航 + 历史任务树(Phase 1.5)
// 导航 + 任务树(可展开/收起, 点击切换会话触发后端 WS replay 加载历史)
import { useCallback, useEffect, useState } from "react";

import { adminFetch } from "../utils/apiClient";
import RobotAvatar from "./RobotAvatar";

// 2026-08-10: 侧边栏固定宽度两态(展开/折叠), 取消外部拖拽调宽
// 左右侧边栏对称: 折叠宽度均为 44px(与 ArtifactPanel 的 PANEL_COLLAPSED_WIDTH 一致)
export const SIDEBAR_EXPANDED_WIDTH = 220;
export const SIDEBAR_COLLAPSED_WIDTH = 44;

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
  kind?: string | null; // main / sub / monitor(0.5.0 P3: 主智能体监控会话)
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

// 0.5.0 M1: 场景技术标识 → 中文名(显示层统一 scene_name)
const SCENE_NAME_MAP: Record<string, string> = {
  office: "子瞻",
  data_analysis: "白圭",
  frontend_design: "清和",
};
const sceneName = (key: string | null): string =>
  key ? SCENE_NAME_MAP[key] ?? key : "无 skill";

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
  onResumeSession,
  agentName,
}: {
  currentSessionId: number | null;
  onSwitchSession: (id: number, skillName?: string | null, modelId?: string | null) => void;
  // V1.4-8.4: 跨模块搜索结果跳转(技能/知识库视图)
  onNavigate: (v: ViewKey) => void;
  // V1.5 项-4: 断点恢复(interrupted 会话"断点继续"按钮)
  onResumeSession?: (id: number) => void;
  // 0.5.0 P5(2026-08-15): 主智能体显示名(无涯等, 历史树主智能体组名)
  agentName?: string;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // V1.1-3.1 会话管理: 内联重命名 + hover 操作 + 归档折叠
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renamingValue, setRenamingValue] = useState("");
  const [hoverId, setHoverId] = useState<number | null>(null);
  // 2026-08-15(蒋先生反馈): 已归档并入场景组(对话名后标签), 取消独立归档区
  // 0.5.0 M1(2026-08-08): 历史树按场景分组(子瞻/白圭/清和), 场景组可折叠
  // 2026-08-15(蒋先生反馈): 组默认收起 —— 不再每次展开全列表(反人类)
  const [sceneOpen, setSceneOpen] = useState<Record<string, boolean>>({
    office: false,
    data_analysis: false,
    frontend_design: false,
    global: false,
    monitor: false,
  });
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

  // 0.5.0 M1(2026-08-08): 历史按场景分三大类展示 ——
  // 子瞻(office)/白圭(data_analysis)/清和(frontend_design) 三组 +
  // 全局组(未锁定场景/其他 skill 的会话, 保留 folder 子分组兼容旧行为)。
  // 2026-08-15(蒋先生反馈): ① 主智能体(monitor)会话归入独立组, 组名用
  // 实时智能体名(无涯等), 不再混进"全局"; ② 已归档会话并入各自场景组
  // (对话名后带"已归档"标签), 取消底部独立"已归档"大区。
  const SCENE_GROUPS: { key: string; name: string; icon: string }[] = [
    { key: "monitor", name: agentName || "主智能体", icon: "🤖" },
    { key: "office", name: "子瞻", icon: "📄" },
    { key: "data_analysis", name: "白圭", icon: "📈" },
    { key: "frontend_design", name: "清和", icon: "🎨" },
  ];
  const sceneMap = new Map<string, SessionItem[]>();
  for (const g of SCENE_GROUPS) sceneMap.set(g.key, []);
  sceneMap.set("global", []);
  for (const s of sessions) {
    const key =
      s.kind === "monitor"
        ? "monitor"
        : SCENE_GROUPS.some((g) => g.key === s.locked_skill_name)
          ? s.locked_skill_name!
          : "global";
    sceneMap.get(key)!.push(s);
  }

  const toggleScene = (key: string): void => {
    setSceneOpen((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const renderSessionRow = (s: SessionItem): JSX.Element => {
    const isCurrent = currentSessionId === s.id;
    // 2026-08-15(蒋先生反馈): hover 图标遮挡对话名 —— 非当前会话只显示
    // 核心 3 个操作(重命名/归档/删除); 文件夹/导出/断点继续仅当前会话显示。
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
          background: isCurrent ? "var(--panel-bg-hover)" : "transparent",
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
              background: "var(--input-bg)",
              color: "var(--text-primary)",
              outline: "none",
            }}
          />
        ) : (
          <span style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 2, flex: 1, minWidth: 0 }}>
            <span style={{ display: "flex", alignItems: "center", gap: 4, maxWidth: "100%", overflow: "hidden" }}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {s.title || `#${s.id}`}
              </span>
              {/* V1.5 项-4: interrupted 状态徽标(断点可恢复) */}
              {s.status === "interrupted" && (
                <span
                  style={{
                    flexShrink: 0,
                    fontSize: 9,
                    padding: "1px 6px",
                    borderRadius: 8,
                    background: "var(--warning-bg)",
                    color: "var(--warning-text)",
                    border: "1px solid var(--warning-border)",
                    whiteSpace: "nowrap",
                  }}
                  title="会话被中断, 可断点继续"
                >
                  未归档
                </span>
              )}
              {/* 2026-08-15(蒋先生反馈): 已归档改为对话名后标签(并入场景组) */}
              {s.status === "archived" && (
                <span
                  style={{
                    flexShrink: 0,
                    fontSize: 9,
                    padding: "1px 6px",
                    borderRadius: 8,
                    background: "var(--panel-bg)",
                    color: "var(--text-tertiary)",
                    border: "1px solid var(--border)",
                    whiteSpace: "nowrap",
                  }}
                  title="会话已归档, 点击可查看"
                >
                  已归档
                </span>
              )}
            </span>
            <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
              {sceneName(s.locked_skill_name)}
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
            {/* V1.5 项-4: 中断会话断点继续按钮(先切到该会话再 resume) */}
            {s.status === "interrupted" && onResumeSession && (
              <ActionBtn
                label="▶"
                title="断点继续: 从中断处恢复生成"
                onClick={() => {
                  onSwitchSession(s.id, s.locked_skill_name, s.model_id);
                  onResumeSession(s.id);
                }}
              />
            )}
            <ActionBtn
              label={s.status === "archived" ? "▶" : "⏸"}
              title={s.status === "archived" ? "恢复会话" : "归档会话"}
              onClick={() => toggleArchive(s)}
            />
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
            {/* 仅当前会话显示低频操作(文件夹/导出), 减少 hover 遮挡 */}
            {isCurrent && (
              <>
                <ActionBtn label="📁" title={s.folder ? `移到文件夹(当前:${s.folder})` : "移到文件夹"} onClick={() => promptFolder(s)} />
                <ActionBtn label="⤓" title="导出(MD/JSON)" onClick={() => setExportFor(exportFor === s.id ? null : s.id)} />
              </>
            )}
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
              background: "var(--panel-bg-hover)",
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
            border: "1px solid var(--border-color)",
            background: "var(--button-ghost-bg)",
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
                background: "var(--panel-bg)",
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
                  background: "var(--panel-bg-hover)",
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
          {/* 0.5.0 M1: 历史按场景分组(子瞻/白圭/清和 + 主智能体 + 全局) */}
          {[...SCENE_GROUPS, { key: "global", name: "全局", icon: "🌐" }].map((g) => {
            const list = sceneMap.get(g.key) ?? [];
            if (list.length === 0) return null;
            const isOpen = sceneOpen[g.key] !== false;
            return (
              <div key={g.key} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <button
                  onClick={() => toggleScene(g.key)}
                  title={`${g.name}场景会话(${list.length})`}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "4px 8px 2px",
                    fontSize: 11,
                    fontWeight: 600,
                    color: "var(--text-tertiary)",
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    {g.icon} {g.name}
                    <span style={{ fontWeight: 400, fontSize: 10 }}>({list.length})</span>
                  </span>
                  <span>{isOpen ? "▾" : "▸"}</span>
                </button>
                {isOpen && list.map(renderSessionRow)}
              </div>
            );
          })}
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
        // 2026-08-15(蒋先生反馈): 缩小图标尺寸(20→15px), 减少 hover 遮挡对话名
        width: 15,
        height: 15,
        border: "none",
        background: "transparent",
        color: danger ? "var(--text-tertiary)" : "var(--text-tertiary)",
        fontSize: 10,
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
  width = SIDEBAR_EXPANDED_WIDTH,
  onResumeSession,
  theme,
  toggleTheme,
  agentName,
  onRenameAgent,
  onOpenMonitor,
  monitorActive,
}: {
  active: ViewKey;
  onChange: (v: ViewKey) => void;
  currentSessionId: number | null;
  onSwitchSession: (id: number, skillName?: string | null, modelId?: string | null) => void;
  status: "connected" | "disconnected" | "reconnecting";
  /** 2026-08-10: 展开宽度由 App 以固定常量传入(不可拖拽); 折叠为 SIDEBAR_COLLAPSED_WIDTH */
  width?: number;
  /** V1.5 项-4: 断点恢复 —— 点击对 interrupted 会话发送 resume(可空: 兼容未接入方) */
  onResumeSession?: (id: number) => void;
  /** 2026-08-08: 主题切换入口移到侧边栏(亮色/暗色滑块) */
  theme?: "light" | "dark";
  toggleTheme?: () => void;
  /** V1.1-3.6 智能体显示名(侧边栏顶部展示) */
  agentName?: string;
  /** V1.1-3.6 改名回调(App.tsx 注入, PUT /admin/agent-profile) */
  onRenameAgent?: (name: string) => Promise<void>;
  /** 0.5.0 P4(2026-08-08 蒋先生反馈): 左上角 PA 图标点击 → 开启主智能体对话
      (原"改名"功能已移至设置页「智能体名称配置」卡) */
  onOpenMonitor?: () => void;
  /** 0.5.0 P5: 主智能体是否有未关闭对话(PA 图标旁状态圆点: 绿=对话中/红=无) */
  monitorActive?: boolean;
}): JSX.Element {
  const [collapsed, setCollapsed] = useState(false);
  // V1.1-3.6 改名 popover 状态(锚定在侧边栏顶部智能体标识)
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [renameSaving, setRenameSaving] = useState(false);
  const [renameErr, setRenameErr] = useState<string | null>(null);
  const doRename = async (): Promise<void> => {
    const v = renameValue.trim();
    if (!v) {
      setRenameErr("名称不能为空");
      return;
    }
    if (!onRenameAgent) {
      setRenameErr("当前环境不支持改名");
      return;
    }
    setRenameSaving(true);
    setRenameErr(null);
    try {
      await onRenameAgent(v);
      setRenameOpen(false);
    } catch (e) {
      setRenameErr(String(e));
    } finally {
      setRenameSaving(false);
    }
  };

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
        width: collapsed ? SIDEBAR_COLLAPSED_WIDTH : width,
        flexShrink: 0,
        padding: collapsed ? "16px 6px 16px" : "16px 16px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 4,
        height: "100%",
        minHeight: 0,
        overflowY: "auto",
        boxSizing: "border-box",
        transition: "width 0.3s var(--transition-smooth), padding 0.3s var(--transition-smooth)",
        alignItems: collapsed ? "center" : "stretch",
        position: "relative",
      }}
    >
      {/* 顶部: 智能体标识(可点击改名) + 折叠按钮同行, 节省垂直空白与右侧对称
          2026-08-10 21:55: 收起态修复 —— 原实现收起时仍渲染 28px 智能体头像
          (flex:1) + 28px 折叠按钮, 挤在 44-12=32px 内容区内被截成半个。
          与右栏 ArtifactPanel 行为对齐: 收起时只渲染折叠按钮并居中, 不渲染智能体按钮。 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: collapsed ? "center" : "space-between",
          gap: 8,
          padding: collapsed ? "2px 0 10px" : "4px 4px 12px",
          borderBottom: "1px solid rgba(148,163,184,0.15)",
          marginBottom: 8,
          flexShrink: 0,
        }}
      >
        {!collapsed && (
          <button
            onClick={() => {
              // 0.5.0 P4(2026-08-08 蒋先生反馈): PA 图标点击 → 开启主智能体对话
              // (原"修改智能体名称"popover 已移除, 改名迁至设置页「智能体名称配置」卡)
              if (onOpenMonitor) {
                onOpenMonitor();
              } else {
                setRenameOpen((o) => !o);
                setRenameValue(agentName || "私人智能体");
                setRenameErr(null);
              }
            }}
            title={`${agentName || "主智能体"}(点击进入主智能体对话)`}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: "4px 6px",
              display: "flex",
              alignItems: "center",
              gap: 8,
              minWidth: 0,
              fontFamily: "inherit",
              flex: 1,
            }}
          >
            <RobotAvatar size={28} style={{ borderRadius: 8 }} />
            {/* 0.5.0 P5(2026-08-08 蒋先生反馈): 排列方式与左下角"本地用户"卡片一致 ——
                头像在左, 名称加粗在上, 状态行(7px 圆点 + 文字)在名称下方 */}
            <span style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 2, minWidth: 0 }}>
              <span
                style={{
                  fontSize: 15,
                  fontWeight: 700,
                  color: "var(--text-primary)",
                  letterSpacing: "-0.02em",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  lineHeight: 1.2,
                }}
              >
                {agentName || "私人智能体"}
              </span>
              <span
                title={monitorActive ? "主智能体对话中" : "主智能体无对话"}
                style={{
                  fontSize: 11,
                  color: "var(--text-tertiary)",
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  lineHeight: 1.2,
                }}
              >
                <span
                  style={{
                    display: "inline-block",
                    width: 7,
                    height: 7,
                    borderRadius: "50%",
                    flexShrink: 0,
                    backgroundColor: monitorActive ? "#22c55e" : "#ef4444",
                  }}
                />
                {monitorActive ? "对话中" : "无对话"}
              </span>
            </span>
        </button>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          title={collapsed ? "展开侧边栏" : "收起侧边栏"}
          style={{
            width: 28,
            height: 28,
            borderRadius: 8,
            border: "1px solid var(--border-color)",
            background: "var(--button-ghost-bg)",
            cursor: "pointer",
            color: "var(--text-secondary)",
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
        >
          {collapsed ? "»" : "«"}
        </button>
      </div>

      {/* 改名 popover(锚定在 nav 顶部下方, 折叠态不显示) */}
      {renameOpen && !collapsed && (
        <div
          style={{
            position: "absolute",
            top: 56,
            left: 12,
            right: 12,
            zIndex: 100,
            padding: 12,
            borderRadius: 12,
            background: "var(--panel-bg-solid)",
            border: "1px solid var(--border-color)",
            boxShadow: "0 12px 32px rgba(0,0,0,0.18)",
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>
            修改智能体名称
          </div>
          <input
            autoFocus
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void doRename();
              else if (e.key === "Escape") setRenameOpen(false);
            }}
            disabled={renameSaving}
            placeholder="输入新名称"
            style={{
              width: "100%",
              boxSizing: "border-box",
              padding: "6px 8px",
              borderRadius: 6,
              border: "1px solid var(--border-strong, #94a3b8)",
              background: "var(--input-bg)",
              color: "var(--text-primary)",
              fontSize: 13,
              marginBottom: 8,
            }}
          />
          {renameErr && (
            <div style={{ fontSize: 11, color: "var(--danger-text)", marginBottom: 8 }}>{renameErr}</div>
          )}
          <div style={{ display: "flex", gap: 6 }}>
            <button
              className="btn-primary"
              disabled={renameSaving}
              onClick={() => void doRename()}
              style={{ flex: 1, padding: "6px 0", fontSize: 12 }}
            >
              {renameSaving ? "保存中…" : "保存"}
            </button>
            <button
              onClick={() => setRenameOpen(false)}
              disabled={renameSaving}
              style={{
                flexShrink: 0,
                padding: "6px 12px",
                fontSize: 12,
                border: "1px solid rgba(148,163,184,0.4)",
                borderRadius: 6,
                background: "var(--panel-bg-solid)",
                color: "var(--text-secondary)",
                cursor: "pointer",
              }}
            >
              取消
            </button>
          </div>
        </div>
      )}

      {collapsed ? (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 12,
            margin: "auto 0",
          }}
        >
          <div
            style={{
              writingMode: "vertical-rl",
              fontSize: 12,
              color: "var(--text-tertiary)",
              letterSpacing: "0.1em",
            }}
          >
            导航
          </div>
          {toggleTheme && (
            <button
              onClick={toggleTheme}
              title={theme === "dark" ? "切换到亮色主题" : "切换到暗色主题"}
              style={{
                fontSize: 14,
                border: "none",
                background: "var(--button-ghost-bg)",
                color: "var(--text-primary)",
                borderRadius: 10,
                width: 30,
                height: 30,
                cursor: "pointer",
                flexShrink: 0,
              }}
            >
              {theme === "dark" ? "☀️" : "🌙"}
            </button>
          )}
        </div>
      ) : (
        <>
          <NavLabel>工作区</NavLabel>
          {WORKSPACE_ITEMS.map(renderItem)}

          <TaskTree
            currentSessionId={currentSessionId}
            onSwitchSession={onSwitchSession}
            onNavigate={onChange}
            onResumeSession={onResumeSession}
            agentName={agentName}
          />

          <NavLabel>系统</NavLabel>
          {SYSTEM_ITEMS.map(renderItem)}

          {/* 2026-08-08: 主题切换入口(亮色/暗色滑块), 切主题时首页背景联动切换 */}
          {toggleTheme && (
            <div
              style={{
                marginTop: "auto",
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "8px 10px",
                background: "var(--panel-bg)",
                borderRadius: "var(--radius-sm)",
              }}
            >
              <span
                style={{
                  fontSize: 11,
                  color: theme === "light" ? "var(--text-primary)" : "var(--text-tertiary)",
                  fontWeight: theme === "light" ? 600 : 400,
                  flexShrink: 0,
                }}
              >
                ☀️ 亮色
              </span>
              <button
                onClick={toggleTheme}
                role="switch"
                aria-checked={theme === "dark"}
                title={theme === "dark" ? "切换到亮色主题" : "切换到暗色主题"}
                style={{
                  width: 42,
                  height: 22,
                  borderRadius: 11,
                  border: "none",
                  cursor: "pointer",
                  position: "relative",
                  flexShrink: 0,
                  padding: 0,
                  background:
                    theme === "dark" ? "var(--gradient-indigo)" : "var(--surface-2)",
                  transition: "background 0.3s var(--transition-smooth)",
                }}
              >
                <span
                  style={{
                    position: "absolute",
                    top: 2,
                    left: theme === "dark" ? 22 : 2,
                    width: 18,
                    height: 18,
                    borderRadius: "50%",
                    background: "#fff",
                    boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
                    transition: "left 0.3s var(--transition-smooth)",
                  }}
                />
              </button>
              <span
                style={{
                  fontSize: 11,
                  color: theme === "dark" ? "var(--text-primary)" : "var(--text-tertiary)",
                  fontWeight: theme === "dark" ? 600 : 400,
                  flexShrink: 0,
                }}
              >
                🌙 暗色
              </span>
            </div>
          )}

          <div
            style={{
              padding: 12,
              display: "flex",
              alignItems: "center",
              gap: 10,
              background: "var(--panel-bg)",
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
