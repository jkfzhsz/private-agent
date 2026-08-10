// V1.5 项-1(ADR-012 §3.4 M3 + 2026-08-06 对话流渲染) - 子任务卡片面板
//
// 展示父会话本轮委派的子代理状态: 状态徽标(running/succeeded/failed/
// cancelled/停滞)、"最后心跳 Ns 前"计时、展开显示子代理完整对话流。
// 数据来源:
// - WS 事件即时刷新(App.tsx handleMessage 维护 subagents state)
// - GET /admin/subagents DB 轮询兜底(WS 断线丢事件时由 App 重建, R7)
// - GET /admin/subagents/{id}/events **DB 读取完整对话流**(2026-08-06):
//   子代理 ReactLoop 事件(思考链 thinking / tool_call / tool_result /
//   final)已全量入子 session 的 react_events —— 与主对话流 replay 同源,
//   展开卡片时按"读 LLM 对话"的原理从 DB 拉取渲染(不依赖 WS 实时事件)。
// 心跳计时为本地估算(收到 heartbeat 事件的时间戳 + 每秒渲染刷新), 判定
// 停滞以 DB/后端 watchdog 为准(后端推 subagent_stalled)。
import { useEffect, useMemo, useState } from "react";
import { adminFetch } from "../utils/apiClient";

const API_BASE = "http://localhost:8765/admin";

export interface SubagentEventItem {
  eventType: string;
  payload: Record<string, unknown>;
  ts: number;
}

/** 子代理完整对话流事件(DB react_events, 与主对话流同源) */
export interface SubagentFlowEvent {
  id: number;
  turn: number;
  event_type: string;
  payload: Record<string, unknown>;
  ts: string | null;
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
  /** 工具调用序列(tool_call/tool_result, 精简记录; 完整流见 subSessionId) */
  events: SubagentEventItem[];
  /** 子代理独立会话 id(DB react_events 完整对话流读取入口) */
  subSessionId?: number;
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

  // 2026-08-06: 完整对话流(DB react_events, 展开时拉取一次)
  const [flow, setFlow] = useState<Record<number, SubagentFlowEvent[]>>({});
  const [flowLoading, setFlowLoading] = useState<Set<number>>(new Set());

  const loadFlow = (id: number): void => {
    if (flow[id] || flowLoading.has(id)) return;
    const sa = subagents[id];
    if (!sa?.subSessionId) return; // 子 session 未创建(未开始执行)
    setFlowLoading((prev) => new Set(prev).add(id));
    adminFetch(`${API_BASE}/subagents/${id}/events`)
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: SubagentFlowEvent[]) => {
        setFlow((prev) => ({ ...prev, [id]: rows }));
      })
      .catch(() => {
        /* 拉取失败静默(保留 WS 精简事件) */
      })
      .finally(() => {
        setFlowLoading((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      });
  };

  if (list.length === 0) return null;

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
        loadFlow(id); // 展开时从 DB 读取完整对话流
      }
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
        background: "var(--panel-bg)",
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
              border: "1px solid #cbd5e1", background: "var(--panel-bg-solid)", cursor: "pointer",
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
              background: s.stalled ? "var(--confirmation-bg)" : "var(--code-bg)",
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
                {/* 2026-08-06: 完整对话流(DB 读取, 与主对话流 replay 同源) */}
                {flowLoading.has(s.id) && (
                  <div style={{ fontSize: 11, color: "#94a3b8", padding: "4px 0" }}>
                    加载对话流…
                  </div>
                )}
                {!flowLoading.has(s.id) && flow[s.id] && flow[s.id].length > 0 && (
                  <SubagentFlowView events={flow[s.id]} />
                )}
                {!flowLoading.has(s.id) && !flow[s.id] && s.events.length > 0 && (
                  <div
                    style={{
                      maxHeight: 160,
                      overflowY: "auto",
                      border: "1px solid var(--border-color)",
                      borderRadius: 8,
                      padding: 6,
                      background: "var(--panel-bg-solid)",
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
                      background: "var(--tool-result-bg)",
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
                      background: "var(--error-bg)",
                      border: "1px solid var(--border-color)",
                      borderRadius: 8,
                      padding: 6,
                      color: "var(--danger-text)",
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

// ──────────────────────────────────────────────────────────────────────────────
// 子代理完整对话流视图(DB react_events, 2026-08-06)
// 按 turn 分组渲染: 思考链(thinking 增量合并, 可折叠) → 工具调用/结果 →
// 最终回复; 与主对话流的渲染逻辑同构。
// ──────────────────────────────────────────────────────────────────────────────

function SubagentFlowView({ events }: { events: SubagentFlowEvent[] }): JSX.Element {
  const [openThinking, setOpenThinking] = useState<Set<number>>(new Set());
  // 按 turn 分组(保持时间顺序)
  const groups = useMemo(() => {
    const m = new Map<number, SubagentFlowEvent[]>();
    for (const ev of events) {
      const t = ev.turn ?? 0;
      if (!m.has(t)) m.set(t, []);
      m.get(t)!.push(ev);
    }
    return Array.from(m.entries());
  }, [events]);

  const toggleThinking = (turn: number) => {
    setOpenThinking((prev) => {
      const next = new Set(prev);
      if (next.has(turn)) next.delete(turn);
      else next.add(turn);
      return next;
    });
  };

  return (
    <div
      style={{
        maxHeight: 320,
        overflowY: "auto",
        border: "1px solid var(--border-color)",
        borderRadius: 8,
        padding: 6,
        background: "var(--panel-bg-solid)",
        marginBottom: 6,
      }}
    >
      {groups.map(([turn, evs]) => {
        // thinking 增量合并(reasoning 逐段)
        const thinkingText = evs
          .filter((e) => e.event_type === "thinking")
          .map((e) =>
            String(e.payload?.reasoning ?? e.payload?.content ?? "")
          )
          .join("");
        const toolEvents = evs.filter(
          (e) => e.event_type === "tool_call" || e.event_type === "tool_result"
        );
        const deltaText = evs
          .filter((e) => e.event_type === "delta")
          .map((e) => String(e.payload?.content ?? ""))
          .join("");
        const finalEv = evs.find((e) => e.event_type === "final");
        const errorEv = evs.find((e) => e.event_type === "error");
        const finalText = finalEv
          ? String(finalEv.payload?.content ?? "")
          : deltaText;
        const isOpenT = openThinking.has(turn);
        return (
          <div key={turn} style={{ marginBottom: 6 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
              <span
                style={{
                  fontSize: 10, padding: "1px 6px", borderRadius: 6,
                  background: "var(--tool-call-bg)", color: "#4f46e5", fontWeight: 600,
                }}
              >
                第 {turn} 轮
              </span>
              {thinkingText && (
                <span
                  onClick={() => toggleThinking(turn)}
                  style={{
                    fontSize: 11, color: "#64748b", cursor: "pointer",
                    padding: "1px 6px", borderRadius: 6, background: "var(--code-bg)",
                    border: "1px solid var(--border-color)",
                  }}
                >
                  💭 思考链 {isOpenT ? "▾" : "▸"}
                </span>
              )}
              {toolEvents.length > 0 && (
                <span style={{ fontSize: 11, color: "#94a3b8" }}>
                  🔧 ×{toolEvents.filter((e) => e.event_type === "tool_call").length}
                </span>
              )}
            </div>
            {isOpenT && thinkingText && (
              <div
                style={{
                  fontSize: 11, color: "#64748b", background: "var(--code-bg)",
                  border: "1px solid var(--border-color)", borderRadius: 6, padding: 6,
                  marginBottom: 4, whiteSpace: "pre-wrap", maxHeight: 160,
                  overflowY: "auto",
                }}
              >
                {thinkingText}
              </div>
            )}
            {toolEvents.map((ev, i) => (
              <ToolFlowRow key={`${turn}-${i}`} ev={ev} />
            ))}
            {finalText && (
              <div
                style={{
                  fontSize: 12, color: "#334155", whiteSpace: "pre-wrap",
                  padding: "4px 6px", borderLeft: "3px solid #6366f1",
                  background: "var(--accent-soft-bg)", borderRadius: 4, marginTop: 2,
                }}
              >
                {finalText}
              </div>
            )}
            {errorEv && (
              <div
                style={{
                  fontSize: 11, color: "var(--danger-text)", background: "var(--error-bg)",
                  border: "1px solid var(--border-color)", borderRadius: 6, padding: 6,
                  marginTop: 2,
                }}
              >
                ❌ {String(errorEv.payload?.message ?? "")}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ToolFlowRow({ ev }: { ev: SubagentFlowEvent }): JSX.Element | null {
  const p = ev.payload ?? {};
  const [open, setOpen] = useState(false);
  if (ev.event_type === "tool_call") {
    let argsText = "";
    try {
      argsText = JSON.stringify(p.arguments ?? {}, null, 1);
    } catch {
      argsText = String(p.arguments ?? "");
    }
    return (
      <div
        onClick={() => setOpen(!open)}
        style={{
          fontSize: 11, padding: "3px 6px", cursor: "pointer",
          borderLeft: "3px solid #3b82f6", background: "var(--tool-call-bg)",
          borderRadius: 4, marginBottom: 2,
        }}
      >
        <span style={{ color: "var(--accent-soft-text)", fontWeight: 600 }}>🔧 {String(p.tool_name ?? "")}</span>
        {open && argsText && (
          <pre
            style={{
              margin: "4px 0 0", fontSize: 10, color: "#475569",
              background: "var(--panel-bg-solid)", padding: 4, borderRadius: 4,
              whiteSpace: "pre-wrap", overflowX: "auto",
            }}
          >
            {argsText}
          </pre>
        )}
      </div>
    );
  }
  if (ev.event_type === "tool_result") {
    const out = String(p.output ?? "");
    const err = String(p.error ?? "");
    return (
      <div
        style={{
          fontSize: 11, padding: "3px 6px",
          borderLeft: "3px solid #10b981", background: "var(--success-bg)",
          borderRadius: 4, marginBottom: 2, whiteSpace: "pre-wrap",
        }}
      >
        {err ? (
          <span style={{ color: "var(--danger-text)" }}>❌ {err.slice(0, 400)}</span>
        ) : (
          <span style={{ color: "#047857" }}>
            ✅ {out.length > 500 ? `${out.slice(0, 500)}…` : out}
          </span>
        )}
      </div>
    );
  }
  return null;
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
