// V1.5 项-1(ADR-012 §3.4 M3) - 子任务卡片面板
//
// 展示父会话本轮委派的子代理状态: 状态徽标(running/succeeded/failed/
// cancelled/停滞)、"最后心跳 Ns 前"计时、展开显示工具调用序列与最终结果。
// 数据来源:
// - WS 事件即时刷新(App.tsx handleMessage 维护 subagents state)
// - GET /admin/subagents DB 轮询兜底(WS 断线丢事件时由 App 重建, R7)
// 心跳计时为本地估算(收到 heartbeat 事件的时间戳 + 每秒渲染刷新), 判定
// 停滞以 DB/后端 watchdog 为准(后端推 subagent_stalled)。
import { useEffect, useMemo, useState } from "react";

export interface SubagentEventItem {
  eventType: string;
  payload: Record<string, unknown>;
  ts: number;
}

export type SubagentStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface SubagentState {
  id: number;
  taskId: string;
  prompt: string;
  status: SubagentStatus;
  result?: string;
  error?: string;
  toolCalls: number;
  /** 最近一次 WS heartbeat 到达时间(本地时钟, ms) */
  lastHeartbeatTs?: number;
  /** 后端判定心跳停滞(stalled_at 置位后推 subagent_stalled) */
  stalled?: boolean;
  /** 工具调用序列(tool_call/tool_result, 精简记录) */
  events: SubagentEventItem[];
  createdAt: number;
}

export function createSubagent(
  id: number,
  taskId: string,
  prompt: string
): SubagentState {
  return {
    id,
    taskId,
    prompt,
    status: "running",
    toolCalls: 0,
    events: [],
    createdAt: Date.now(),
  };
}

const STATUS_META: Record<
  SubagentStatus,
  { bg: string; color: string; label: string; icon: string }
> = {
  pending: { bg: "#e2e8f0", color: "#475569", label: "排队中", icon: "⏳" },
  running: { bg: "#dbeafe", color: "#1d4ed8", label: "运行中", icon: "🔵" },
  succeeded: { bg: "#d1fae5", color: "#047857", label: "成功", icon: "✅" },
  failed: { bg: "#fee2e2", color: "#b91c1c", label: "失败", icon: "❌" },
  cancelled: { bg: "#e2e8f0", color: "#64748b", label: "已取消", icon: "⏹" },
};

interface Props {
  subagents: Record<number, SubagentState>;
  onClearFinished?: () => void;
}

export default function SubagentPanel({ subagents, onClearFinished }: Props) {
  const [now, setNow] = useState(() => Date.now());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const list = useMemo(
    () => Object.values(subagents).sort((a, b) => a.createdAt - b.createdAt),
    [subagents]
  );
  // 心跳计时: 每秒刷新一次(仅面板有数据时)
  useEffect(() => {
    if (list.length === 0) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [list.length]);

  if (list.length === 0) return null;

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const finishedCount = list.filter(
    (s) => s.status === "succeeded" || s.status === "failed" || s.status === "cancelled"
  ).length;

  return (
    <div
      style={{
        margin: "10px 4px 14px",
        padding: "10px 12px",
        borderRadius: 14,
        background: "rgba(255,255,255,0.7)",
        border: "1px solid rgba(148,163,184,0.25)",
        boxShadow: "0 4px 16px rgba(148,163,184,0.12)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 8,
          fontSize: 13,
          fontWeight: 600,
          color: "#334155",
        }}
      >
        <span>🧩 子任务</span>
        <span style={{ fontSize: 11, color: "#94a3b8", fontWeight: 400 }}>
          {list.length - finishedCount} 运行中 · {finishedCount} 已完成
        </span>
        <span style={{ flex: 1 }} />
        {finishedCount > 0 && onClearFinished && (
          <button
            onClick={onClearFinished}
            style={{
              fontSize: 11, padding: "2px 8px", borderRadius: 6,
              border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer",
              color: "#64748b",
            }}
            title="清除已完成的子任务卡片"
          >
            清除已完成
          </button>
        )}
      </div>
      {list.map((s) => {
        const meta = STATUS_META[s.status];
        const isOpen = expanded.has(s.id);
        // "最后心跳 Ns 前": 仅运行中显示; 停滞时转警示文案
        let hbText = "";
        if (s.status === "running") {
          if (s.stalled) {
            hbText = "⚠️ 心跳停滞，等待宽限中…";
          } else if (s.lastHeartbeatTs) {
            const secs = Math.max(0, Math.round((now - s.lastHeartbeatTs) / 1000));
            hbText = secs <= 1 ? "心跳中" : `最后心跳 ${secs}s 前`;
          } else {
            hbText = "等待首跳…";
          }
        }
        return (
          <div
            key={s.id}
            style={{
              borderRadius: 10,
              border: s.stalled
                ? "1px solid #fbbf24"
                : "1px solid rgba(148,163,184,0.3)",
              background: s.stalled ? "#fffbeb" : "#f8fafc",
              padding: "8px 10px",
              marginBottom: 6,
            }}
          >
            <div
              style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
              onClick={() => toggle(s.id)}
            >
              <span
                style={{
                  fontSize: 11,
                  padding: "2px 8px",
                  borderRadius: 8,
                  background: meta.bg,
                  color: meta.color,
                  fontWeight: 600,
                  flexShrink: 0,
                }}
              >
                {meta.icon} {meta.label}
              </span>
              <span
                style={{ fontSize: 12, fontWeight: 600, color: "#334155", flexShrink: 0 }}
              >
                {s.taskId || `#${s.id}`}
              </span>
              {s.status === "running" && (
                <span
                  style={{
                    fontSize: 11,
                    color: s.stalled ? "#d97706" : "#94a3b8",
                    fontWeight: s.stalled ? 600 : 400,
                  }}
                >
                  {hbText}
                </span>
              )}
              {s.toolCalls > 0 && (
                <span style={{ fontSize: 11, color: "#94a3b8" }}>🔧 ×{s.toolCalls}</span>
              )}
              <span style={{ flex: 1 }} />
              <span style={{ fontSize: 11, color: "#94a3b8" }}>{isOpen ? "▾" : "▸"}</span>
            </div>
            {isOpen && (
              <div style={{ marginTop: 8, fontSize: 12, color: "#475569" }}>
                <div style={{ color: "#64748b", marginBottom: 4 }}>
                  <span style={{ color: "#94a3b8" }}>指令: </span>
                  {s.prompt.length > 180 ? `${s.prompt.slice(0, 180)}…` : s.prompt}
                </div>
                {s.events.length > 0 && (
                  <div
                    style={{
                      maxHeight: 160,
                      overflowY: "auto",
                      border: "1px solid #e2e8f0",
                      borderRadius: 8,
                      padding: 6,
                      background: "#fff",
                      marginBottom: 6,
                      fontFamily:
                        'ui-monospace, SFMono-Regular, "Cascadia Code", Consolas, monospace',
                      fontSize: 11,
                    }}
                  >
                    {s.events.map((ev, i) => (
                      <div
                        key={i}
                        style={{
                          padding: "2px 0",
                          borderBottom:
                            i < s.events.length - 1
                              ? "1px solid rgba(148,163,184,0.12)"
                              : "none",
                        }}
                      >
                        <span style={{ color: "#94a3b8", marginRight: 6 }}>
                          {new Date(ev.ts).toLocaleTimeString()}
                        </span>
                        <span style={{ color: "#6d28d9", fontWeight: 600 }}>
                          {ev.eventType}
                        </span>
                        <span style={{ color: "#475569", marginLeft: 6 }}>
                          {formatEventBrief(ev)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
                {s.status === "succeeded" && s.result && (
                  <div
                    style={{
                      background: "#ecfdf5",
                      border: "1px solid #a7f3d0",
                      borderRadius: 8,
                      padding: 6,
                      color: "#065f46",
                      whiteSpace: "pre-wrap",
                      maxHeight: 200,
                      overflowY: "auto",
                    }}
                  >
                    {s.result.length > 800 ? `${s.result.slice(0, 800)}…` : s.result}
                  </div>
                )}
                {(s.status === "failed" || s.status === "cancelled") && s.error && (
                  <div
                    style={{
                      background: "#fef2f2",
                      border: "1px solid #fecaca",
                      borderRadius: 8,
                      padding: 6,
                      color: "#991b1b",
                    }}
                  >
                    {s.error}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function formatEventBrief(ev: SubagentEventItem): string {
  const p = ev.payload ?? {};
  if (ev.eventType === "tool_call") {
    return String(p.tool_name ?? "");
  }
  if (ev.eventType === "tool_result") {
    const out = String(p.output ?? "");
    return out.length > 120 ? `${out.slice(0, 120)}…` : out;
  }
  if (ev.eventType === "final") {
    const c = String(p.content ?? "");
    return c.length > 120 ? `${c.slice(0, 120)}…` : c;
  }
  if (ev.eventType === "error") {
    return String(p.message ?? "");
  }
  return "";
}
