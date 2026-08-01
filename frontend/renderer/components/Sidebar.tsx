// FlowSpace 侧边栏导航 + 历史任务树(Phase 1.5)
// 导航 + 任务树(可展开/收起, 点击切换会话触发后端 WS replay 加载历史)
import { useCallback, useEffect, useState } from "react";

export type ViewKey = "home" | "chat" | "knowledge" | "memory" | "settings";

export interface SessionItem {
  id: number;
  title: string | null;
  status: string;
  locked_skill_name: string | null;
  last_turn: number;
  updated_at: string | null;
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
}: {
  currentSessionId: number | null;
  onSwitchSession: (id: number) => void;
}): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError("");
    try {
      const resp = await fetch("http://127.0.0.1:8765/admin/sessions?limit=30");
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

  return (
    <div style={{ marginTop: 8 }}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="nav-item"
        style={{ width: "100%", justifyContent: "space-between" }}
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
          {error && (
            <div style={{ fontSize: 11, color: "var(--danger-text)", padding: "4px 8px" }}>
              {error}
            </div>
          )}
          {!loading && sessions.length === 0 && !error && (
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", padding: "4px 8px" }}>
              暂无历史会话
            </div>
          )}
          {sessions.map((s) => (
            <button
              key={s.id}
              onClick={() => onSwitchSession(s.id)}
              className="nav-item"
              style={{
                width: "100%",
                padding: "6px 8px",
                fontSize: 12,
                background:
                  currentSessionId === s.id
                    ? "rgba(255,255,255,0.8)"
                    : "transparent",
                color:
                  currentSessionId === s.id
                    ? "var(--text-primary)"
                    : "var(--text-secondary)",
                fontWeight: currentSessionId === s.id ? 600 : 400,
              }}
              title={`turn ${s.last_turn} · ${s.locked_skill_name ?? "无 skill"}`}
            >
              <span style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 2, flex: 1, minWidth: 0 }}>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}>
                  #{s.id}
                  {s.last_turn > 0 ? ` · ${s.last_turn} 轮` : ""}
                </span>
                {s.locked_skill_name && (
                  <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
                    {s.locked_skill_name}
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Sidebar({
  active,
  onChange,
  currentSessionId,
  onSwitchSession,
  status,
}: {
  active: ViewKey;
  onChange: (v: ViewKey) => void;
  currentSessionId: number | null;
  onSwitchSession: (id: number) => void;
  status: "connected" | "disconnected" | "reconnecting";
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
        width: collapsed ? 44 : 220,
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
            PA
          </div>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: "var(--text-primary)", fontFamily: "var(--font-sans)", letterSpacing: "-0.02em", whiteSpace: "nowrap" }}>
              Private Agent
            </div>
            <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>FlowSpace v2.1</div>
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

          <TaskTree currentSessionId={currentSessionId} onSwitchSession={onSwitchSession} />

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
