// M1 Phase 5 - React chat UI 根组件 (蓝图 §2.15 + §9.4 AC-8)
//
// 功能:
// - 消息输入框 + 发送按钮
// - 流式渲染区域:按 event_type 分块(thinking/tool_call/tool_result/final/error)
// - WS 连接状态指示器(connected/disconnected/reconnecting)
// - 重连机制:指数退避(1s,2s,4s,8s,max 16s),重连后发送 replay(session_id + last_turn)
// - ACK 机制:收到 react_event 后发送 ack(session_id + turn)
// - session_id 管理:首次连接时从 URL 参数获取或生成
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import SkillSelectionPanel from "./SkillSelectionPanel";

// ──────────────────────────────────────────────────────────────────────────────
// 类型定义
// ──────────────────────────────────────────────────────────────────────────────

type ConnStatus = "connected" | "disconnected" | "reconnecting";

type EventType = "user" | "thinking" | "tool_call" | "tool_result" | "final" | "error";

interface ReactEvent {
  id: number;
  session_id: number;
  turn: number;
  event_type: EventType;
  payload: Record<string, unknown>;
  ts: number;
  replayed?: boolean;
}

interface WSMessage {
  type: string;
  session_id?: number;
  turn?: number;
  event_type?: EventType;
  payload?: Record<string, unknown>;
  count?: number;
  effective_offset?: number;
  message?: string;
}

// ──────────────────────────────────────────────────────────────────────────────
// 常量
// ──────────────────────────────────────────────────────────────────────────────

const WS_URL = "ws://localhost:8765/ws";
const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000];
const MAX_RECONNECT_DELAY = 16000;

const EVENT_STYLES: Record<EventType, { bg: string; label: string; icon: string }> = {
  user: { bg: "#dbeafe", label: "You", icon: "🧑" },
  thinking: { bg: "#f5f5f5", label: "Thinking", icon: "💭" },
  tool_call: { bg: "#e3f2fd", label: "Tool Call", icon: "🔧" },
  tool_result: { bg: "#e8f5e9", label: "Tool Result", icon: "✅" },
  final: { bg: "#e8eaf6", label: "Final", icon: "🎯" },
  error: { bg: "#ffebee", label: "Error", icon: "❌" },
};

// M3 AC-9: tool_result 中 outputs/*.png 等图片路径解析(蓝图 §7.12)
// 匹配 "outputs/foo.png" 或 "/outputs/foo-bar.jpg" 形式的路径
const IMAGE_PATH_RE = /(?:^|[^\w/])((?:\/?outputs\/)?[\w\-]+\.(?:png|jpg|jpeg|gif|svg|webp))/gi;

function extractImagePaths(text: string): string[] {
  if (!text) return [];
  const paths: string[] = [];
  let m: RegExpExecArray | null;
  IMAGE_PATH_RE.lastIndex = 0;
  while ((m = IMAGE_PATH_RE.exec(text)) !== null) {
    paths.push(m[1]);
  }
  // 去重,保留顺序
  return Array.from(new Set(paths));
}

function imagePathToUrl(path: string): string {
  // 取 outputs/ 之后的部分作为 filename,拼接 /files/outputs/{filename}
  const match = path.match(/outputs\/([\w\-\.]+)$/i);
  const filename = match ? match[1] : path.replace(/^\/?outputs\//, "");
  return `/files/outputs/${filename}`;
}

// ──────────────────────────────────────────────────────────────────────────────
// 辅助函数
// ──────────────────────────────────────────────────────────────────────────────

function getSessionIdFromUrl(): number {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("session_id");
  if (raw) {
    const n = Number.parseInt(raw, 10);
    if (!Number.isNaN(n) && n > 0) return n;
  }
  // 首次连接生成一个随机 session_id(占位,实际由后端创建 session 后回传)
  return Math.floor(Math.random() * 100000) + 1;
}

function formatPayload(eventType: EventType, payload: Record<string, unknown>): string {
  switch (eventType) {
    case "user":
      return String(payload.content ?? "");
    case "thinking":
      return String(payload.content ?? "");
    case "tool_call": {
      const name = payload.tool_name ?? payload.name ?? "unknown";
      const args = payload.arguments ?? payload.args ?? "";
      return `${name}(${typeof args === "string" ? args : JSON.stringify(args)})`;
    }
    case "tool_result":
      return String(payload.output ?? payload.result ?? JSON.stringify(payload));
    case "final":
      return String(payload.content ?? "");
    case "error":
      return String(payload.message ?? JSON.stringify(payload));
    default:
      return JSON.stringify(payload);
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// 评估面板组件(M4 §8.12,AC-11)
// ──────────────────────────────────────────────────────────────────────────────

interface EvalRun {
  run_id: string;
  skill_name: string;
  skill_version: string;
  model_id: string;
  eval_mode: string;
  finished_at: string | null;
  metrics?: Record<string, Record<string, number>>;
}

interface CompareResult {
  base_version: string;
  target_version: string;
  diff: Record<string, Record<string, { delta: number; status: string }>>;
}

const API_BASE = "http://localhost:8765/admin/eval";

async function fetchJson(url: string): Promise<unknown> {
  const resp = await fetch(url);
  return resp.json();
}

function EvalPanel(): JSX.Element {
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [compare, setCompare] = useState<CompareResult | null>(null);
  const [loading, setLoading] = useState(false);

  const loadRuns = useCallback(async () => {
    setLoading(true);
    try {
      const data = (await fetchJson(`${API_BASE}/runs?limit=20`)) as { runs: EvalRun[] };
      setRuns(data.runs ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadCompare = useCallback(async () => {
    if (runs.length < 2) return;
    const versions = [...new Set(runs.map((r) => r.skill_version))].slice(0, 2);
    if (versions.length < 2) return;
    const data = (await fetchJson(
      `${API_BASE}/versions/compare?skill_name=${runs[0].skill_name}&base_version=${versions[0]}&target_version=${versions[1]}`
    )) as CompareResult;
    setCompare(data);
  }, [runs]);

  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  // 版本趋势折线图数据(completion_rate 随版本变化)
  const trendPoints = runs
    .filter((r) => r.metrics?.task_completion?.completion_rate !== undefined)
    .map((r, i) => ({
      x: 20 + i * 60,
      y: 120 - r.metrics!.task_completion!.completion_rate * 100,
      version: r.skill_version,
      rate: r.metrics!.task_completion!.completion_rate,
    }));

  return (
    <div style={{ marginTop: 24, padding: 16, border: "1px solid #ddd", borderRadius: 8 }}>
      <h2 style={{ fontSize: 16, marginBottom: 12 }}>评估面板</h2>

      {/* 运行列表 */}
      <div style={{ marginBottom: 16 }}>
        <h3 style={{ fontSize: 13, marginBottom: 8 }}>运行列表</h3>
        <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid #ddd", textAlign: "left" }}>
              <th style={{ padding: 4 }}>run_id</th>
              <th style={{ padding: 4 }}>skill</th>
              <th style={{ padding: 4 }}>version</th>
              <th style={{ padding: 4 }}>model</th>
              <th style={{ padding: 4 }}>mode</th>
              <th style={{ padding: 4 }}>status</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id} style={{ borderBottom: "1px solid #eee" }}>
                <td style={{ padding: 4 }}>{r.run_id.slice(0, 8)}</td>
                <td style={{ padding: 4 }}>{r.skill_name}</td>
                <td style={{ padding: 4 }}>{r.skill_version}</td>
                <td style={{ padding: 4 }}>{r.model_id}</td>
                <td style={{ padding: 4 }}>{r.eval_mode}</td>
                <td style={{ padding: 4 }}>{r.finished_at ? "completed" : "running"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs.length === 0 && <div style={{ fontSize: 12, color: "#999" }}>暂无运行记录</div>}
      </div>

      {/* 版本趋势折线图(SVG) */}
      <div style={{ marginBottom: 16 }}>
        <h3 style={{ fontSize: 13, marginBottom: 8 }}>版本趋势</h3>
        {trendPoints.length >= 2 ? (
          <svg width={400} height={140} style={{ border: "1px solid #eee" }}>
            <line x1={20} y1={20} x2={20} y2={120} stroke="#ccc" />
            <line x1={20} y1={120} x2={380} y2={120} stroke="#ccc" />
            <polyline
              points={trendPoints.map((p) => `${p.x},${p.y}`).join(" ")}
              fill="none"
              stroke="#1976d2"
              strokeWidth={2}
            />
            {trendPoints.map((p, i) => (
              <g key={i}>
                <circle cx={p.x} cy={p.y} r={3} fill="#1976d2" />
                <text x={p.x} y={135} fontSize={10} textAnchor="middle" fill="#666">
                  {p.version}
                </text>
              </g>
            ))}
          </svg>
        ) : (
          <div style={{ fontSize: 12, color: "#999" }}>Insufficient data (需要 ≥2 个版本数据点)</div>
        )}
      </div>

      {/* 版本对比表格 + 退化告警标记 */}
      <div style={{ marginBottom: 16 }}>
        <h3 style={{ fontSize: 13, marginBottom: 8 }}>版本对比</h3>
        <button
          onClick={loadCompare}
          disabled={runs.length < 2 || loading}
          style={{ fontSize: 12, padding: "4px 8px", marginBottom: 8 }}
        >
          对比最新两版本
        </button>
        {compare && (
          <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid #ddd", textAlign: "left" }}>
                <th style={{ padding: 4 }}>category</th>
                <th style={{ padding: 4 }}>metric</th>
                <th style={{ padding: 4 }}>delta</th>
                <th style={{ padding: 4 }}>status</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(compare.diff).flatMap(([cat, metrics]) =>
                Object.entries(metrics).map(([metric, info]) => (
                  <tr key={`${cat}-${metric}`} style={{ borderBottom: "1px solid #eee" }}>
                    <td style={{ padding: 4 }}>{cat}</td>
                    <td style={{ padding: 4 }}>{metric}</td>
                    <td style={{ padding: 4 }}>{info.delta.toFixed(3)}</td>
                    <td style={{ padding: 4 }}>
                      {info.status === "degraded" ? (
                        <span style={{ color: "#fff", background: "#f44336", padding: "2px 6px", borderRadius: 3, fontSize: 11 }}>
                          degraded
                        </span>
                      ) : info.status === "improved" ? (
                        <span style={{ color: "#fff", background: "#4caf50", padding: "2px 6px", borderRadius: 3, fontSize: 11 }}>
                          improved
                        </span>
                      ) : (
                        <span style={{ color: "#666", fontSize: 11 }}>stable</span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// 主组件
// ──────────────────────────────────────────────────────────────────────────────

export default function App(): JSX.Element {
  const [events, setEvents] = useState<ReactEvent[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<ConnStatus>("disconnected");
  const [sessionId] = useState<number>(() => getSessionIdFromUrl());
  const [realSessionId, setRealSessionId] = useState<number | null>(null);
  const [activeSkill, setActiveSkill] = useState<string | null>(null);
  const [view, setView] = useState<"skill_selection" | "chat">("skill_selection");
  const [showEvalPanel, setShowEvalPanel] = useState(false);
  // 每个 turn 的"推理过程"展开状态(默认收起)
  const [openThinkingTurns, setOpenThinkingTurns] = useState<Set<number>>(new Set());

  // 按 turn 分组事件:同一轮对话合并为一个 AI 回复块
  const turnGroups = useMemo(() => {
    const map = new Map<number, ReactEvent[]>();
    for (const ev of events) {
      const t = ev.turn;
      if (!map.has(t)) map.set(t, []);
      map.get(t)!.push(ev);
    }
    return Array.from(map.entries()).sort((a, b) => a[0] - b[0]);
  }, [events]);

  const toggleThinking = (turn: number): void => {
    setOpenThinkingTurns((prev) => {
      const next = new Set(prev);
      if (next.has(turn)) {
        next.delete(turn);
      } else {
        next.add(turn);
      }
      return next;
    });
  };

  const wsRef = useRef<WebSocket | null>(null);
  const lastTurnRef = useRef<number>(0);
  const reconnectAttemptRef = useRef<number>(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const eventIdRef = useRef<number>(0);
  const manualCloseRef = useRef<boolean>(false);

  // ── 发送消息到 WS ──────────────────────────────────────────────────────────
  const sendWs = useCallback((msg: Record<string, unknown>): void => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }, []);

  // ── 处理收到的 WS 消息 ──────────────────────────────────────────────────────
  const handleMessage = useCallback((msg: WSMessage): void => {
    switch (msg.type) {
      case "pong":
        break;

      case "react_event": {
        if (msg.event_type && msg.turn !== undefined && msg.payload) {
          // 从后端回传更新真实 session_id(B2 P1-9:activate 需要真实 session)
          if (msg.session_id && msg.session_id !== sessionId) {
            setRealSessionId(msg.session_id);
          }
          const event: ReactEvent = {
            id: ++eventIdRef.current,
            session_id: msg.session_id ?? sessionId,
            turn: msg.turn,
            event_type: msg.event_type,
            payload: msg.payload,
            ts: Date.now(),
          };
          setEvents((prev) => [...prev, event]);
          // 更新 last_turn(取最大值)
          if (msg.turn > lastTurnRef.current) {
            lastTurnRef.current = msg.turn;
          }
          // ACK 回写
          sendWs({
            type: "ack",
            session_id: msg.session_id ?? sessionId,
            turn: msg.turn,
          });
        }
        break;
      }

      case "replay_end":
        // 补发完成,标记后续事件不再特殊处理
        break;

      case "ack_confirm":
        break;

      case "turn_end":
        // 一轮结束,可在此做 UI 收尾
        break;

      case "error":
        if (msg.message) {
          // B2 P1-9: skill_not_found → 自动切回技能选择页
          if (/skill_not_found|skill not found/i.test(msg.message)) {
            setView("skill_selection");
          }
          const event: ReactEvent = {
            id: ++eventIdRef.current,
            session_id: msg.session_id ?? sessionId,
            turn: msg.turn ?? lastTurnRef.current,
            event_type: "error",
            payload: { message: msg.message },
            ts: Date.now(),
          };
          setEvents((prev) => [...prev, event]);
        }
        break;

      default:
        break;
    }
  }, [sessionId, sendWs]);

  // ── 建立 WS 连接 ──────────────────────────────────────────────────────────
  const connect = useCallback((): void => {
    if (manualCloseRef.current) return;

    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("connected");
        reconnectAttemptRef.current = 0;
        // 重连后发送 replay(首次连接 last_turn=0,后端返回空补发)
        sendWs({
          type: "replay",
          session_id: sessionId,
          last_turn: lastTurnRef.current,
        });
      };

      ws.onmessage = (ev: MessageEvent) => {
        try {
          const msg: WSMessage = JSON.parse(ev.data);
          handleMessage(msg);
        } catch {
          // 忽略非 JSON 消息
        }
      };

      ws.onerror = () => {
        // onclose 会处理重连
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (manualCloseRef.current) {
          setStatus("disconnected");
          return;
        }
        setStatus("reconnecting");
        scheduleReconnect();
      };
    } catch {
      setStatus("reconnecting");
      scheduleReconnect();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, sendWs, handleMessage]);

  // ── 指数退避重连 ──────────────────────────────────────────────────────────
  const scheduleReconnect = useCallback((): void => {
    if (manualCloseRef.current) return;
    const attempt = reconnectAttemptRef.current;
    const delay = RECONNECT_DELAYS[Math.min(attempt, RECONNECT_DELAYS.length - 1)];
    const actualDelay = attempt >= RECONNECT_DELAYS.length ? MAX_RECONNECT_DELAY : delay;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
    }
    reconnectTimerRef.current = setTimeout(() => {
      reconnectAttemptRef.current += 1;
      connect();
    }, actualDelay);
  }, [connect]);

  // ── 发送用户消息 ──────────────────────────────────────────────────────────
  const sendMessage = useCallback((): void => {
    const content = input.trim();
    if (!content) return;
    sendWs({
      type: "user_message",
      session_id: sessionId,
      content,
    });
    // 用户消息立即上屏(右侧气泡)
    setEvents((prev) => [
      ...prev,
      {
        id: ++eventIdRef.current,
        session_id: sessionId,
        turn: lastTurnRef.current + 1,
        event_type: "user",
        payload: { content },
        ts: Date.now(),
      },
    ]);
    setInput("");
  }, [input, sessionId, sendWs]);

  // ── 生命周期:挂载时连接,卸载时关闭 ──────────────────────────────────────
  useEffect(() => {
    manualCloseRef.current = false;
    connect();
    return () => {
      manualCloseRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      const ws = wsRef.current;
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
      wsRef.current = null;
    };
  }, [connect]);

  // ── 渲染 ──────────────────────────────────────────────────────────────────
  const statusColor =
    status === "connected" ? "#4caf50" :
    status === "reconnecting" ? "#ff9800" : "#f44336";

  // B2 P1-9: 技能选择视图(首次进入 / skill_not_found 跳转)
  if (view === "skill_selection") {
    return (
      <div style={{ fontFamily: "system-ui, sans-serif", maxWidth: 900, margin: "0 auto", padding: 16 }}>
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <h1 style={{ fontSize: 20, margin: 0 }}>Private Agent</h1>
          <span style={{ fontSize: 12, color: "#999" }}>session={realSessionId ?? sessionId}</span>
        </header>
        <div
          style={{
            border: "1px solid #ddd", borderRadius: 8, padding: 24,
            backgroundColor: "#fafafa",
          }}
        >
          <SkillSelectionPanel
            sessionId={realSessionId ?? sessionId}
            onActivated={(name) => {
              setActiveSkill(name);
              setView("chat");
            }}
          />
        </div>
      </div>
    );
  }

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", maxWidth: 900, margin: "0 auto", padding: 16 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h1 style={{ fontSize: 20, margin: 0 }}>Private Agent</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              display: "inline-block", width: 10, height: 10, borderRadius: "50%",
              backgroundColor: statusColor,
            }}
          />
          <span style={{ fontSize: 13, color: "#666" }}>{status}</span>
          <span style={{ fontSize: 12, color: "#999" }}>session={sessionId}</span>
          <span style={{ fontSize: 12, color: "#999" }}>last_turn={lastTurnRef.current}</span>
          {activeSkill && (
            <span style={{ fontSize: 12, color: "#1976d2" }}>skill={activeSkill}</span>
          )}
          <button
            onClick={() => setShowEvalPanel(!showEvalPanel)}
            style={{
              fontSize: 12, padding: "4px 10px", borderRadius: 4, border: "1px solid #ddd",
              background: showEvalPanel ? "#1976d2" : "#fff", color: showEvalPanel ? "#fff" : "#333",
              cursor: "pointer",
            }}
          >
            评估面板
          </button>
        </div>
      </header>

      <div
        style={{
          border: "1px solid #ddd",
          borderRadius: 8,
          minHeight: 400,
          maxHeight: "60vh",
          overflowY: "auto",
          padding: 12,
          backgroundColor: "#fafafa",
        }}
      >
        {turnGroups.length === 0 && (
          <div style={{ color: "#999", textAlign: "center", paddingTop: 40 }}>
            发送一条消息开始对话
          </div>
        )}
        {turnGroups.map(([turn, evs]) => {
          const userEv = evs.find((e) => e.event_type === "user");
          const thinkingEv = evs.find((e) => e.event_type === "thinking");
          const finalEv = evs.find((e) => e.event_type === "final");
          const errorEv = evs.find((e) => e.event_type === "error");
          const toolEvents = evs.filter(
            (e) => e.event_type === "tool_call" || e.event_type === "tool_result"
          );
          // 有用户消息但还没有 final → AI 正在思考
          const isPending = !!userEv && !finalEv && !errorEv;
          const thinkingOpen = openThinkingTurns.has(turn);
          const thinkingText = thinkingEv
            ? formatPayload("thinking", thinkingEv.payload)
            : "";

          return (
            <div key={turn} style={{ marginBottom: 14 }}>
              {userEv && (
                <div
                  style={{ display: "flex", justifyContent: "flex-end", marginBottom: 10 }}
                >
                  <div
                    style={{
                      backgroundColor: "#dbeafe",
                      borderRadius: "12px 12px 2px 12px",
                      padding: "8px 14px",
                      maxWidth: "80%",
                    }}
                  >
                    <pre
                      style={{
                        margin: 0,
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        fontSize: 13,
                        fontFamily: "inherit",
                      }}
                    >
                      {formatPayload("user", userEv.payload)}
                    </pre>
                  </div>
                </div>
              )}

              {userEv && (
                <div style={{ display: "flex", gap: 10 }}>
                  <div
                    style={{
                      width: 32,
                      height: 32,
                      borderRadius: "50%",
                      flexShrink: 0,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 14,
                      fontWeight: 600,
                      color: "#fff",
                      background: "linear-gradient(135deg, #818cf8, #c084fc)",
                    }}
                  >
                    PA
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "#374151", marginBottom: 4 }}>
                      Private Agent
                    </div>

                    {isPending && !thinkingEv && (
                      <div style={{ color: "#9ca3af", fontSize: 13 }}>
                        💭 思考中…
                      </div>
                    )}

                    {thinkingEv && (
                      <div
                        style={{
                          border: "1px solid #e5e7eb",
                          borderRadius: 8,
                          marginBottom: 8,
                          overflow: "hidden",
                        }}
                      >
                        <button
                          onClick={() => toggleThinking(turn)}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                            width: "100%",
                            padding: "6px 10px",
                            border: "none",
                            background: "#f9fafb",
                            cursor: "pointer",
                            fontSize: 12,
                            color: "#6b7280",
                            textAlign: "left",
                          }}
                        >
                          <span style={{ fontSize: 11 }}>{thinkingOpen ? "▾" : "▸"}</span>
                          {thinkingOpen ? "收起推理过程" : "查看推理过程"}
                          {!thinkingOpen && (
                            <span style={{ color: "#9ca3af", marginLeft: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {thinkingText.slice(0, 60)}
                            </span>
                          )}
                        </button>
                        {thinkingOpen && (
                          <pre
                            style={{
                              margin: 0,
                              padding: "8px 12px",
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-word",
                              fontSize: 12,
                              color: "#6b7280",
                              maxHeight: 260,
                              overflowY: "auto",
                              fontStyle: "italic",
                            }}
                          >
                            {thinkingText || "（无推理内容）"}
                          </pre>
                        )}
                      </div>
                    )}

                    {toolEvents.length > 0 &&
                      toolEvents.map((te) => {
                        const text = formatPayload(te.event_type, te.payload);
                        const imagePaths =
                          te.event_type === "tool_result"
                            ? extractImagePaths(text)
                            : [];
                        return (
                          <div key={te.id} style={{ marginBottom: 6 }}>
                            <div
                              style={{
                                backgroundColor:
                                  te.event_type === "tool_call" ? "#eef2ff" : "#ecfdf5",
                                borderRadius: 8,
                                padding: "6px 10px",
                                fontSize: 12,
                                color: "#6b7280",
                              }}
                            >
                              {te.event_type === "tool_call" ? (
                                <>🔧 {text}</>
                              ) : (
                                <>✅ {text.slice(0, 120)}{text.length > 120 ? "…" : ""}</>
                              )}
                            </div>
                            {imagePaths.length > 0 && (
                              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 6 }}>
                                {imagePaths.map((p) => (
                                  <img
                                    key={p}
                                    src={imagePathToUrl(p)}
                                    alt={p}
                                    style={{ maxWidth: "100%", borderRadius: 8, border: "1px solid #e5e7eb" }}
                                  />
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })}

                    {finalEv && (
                      <div
                        style={{
                          backgroundColor: "#ffffff",
                          border: "1px solid #e5e7eb",
                          borderRadius: 12,
                          padding: "10px 14px",
                        }}
                      >
                        <pre
                          style={{
                            margin: 0,
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-word",
                            fontSize: 13,
                            fontFamily: "inherit",
                            lineHeight: 1.6,
                          }}
                        >
                          {formatPayload("final", finalEv.payload)}
                        </pre>
                      </div>
                    )}

                    {errorEv && (
                      <div
                        style={{
                          backgroundColor: "#ffebee",
                          borderRadius: 8,
                          padding: "8px 12px",
                          fontSize: 13,
                          color: "#c62828",
                        }}
                      >
                        ❌ {formatPayload("error", errorEv.payload)}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              sendMessage();
            }
          }}
          placeholder="输入消息,Enter 发送"
          style={{
            flex: 1, padding: "10px 12px", borderRadius: 6,
            border: "1px solid #ddd", fontSize: 14, outline: "none",
          }}
        />
        <button
          onClick={sendMessage}
          disabled={status !== "connected" || !input.trim()}
          style={{
            padding: "10px 20px", borderRadius: 6, border: "none",
            backgroundColor: status === "connected" ? "#1976d2" : "#bbb",
            color: "#fff", fontSize: 14, cursor: status === "connected" ? "pointer" : "not-allowed",
          }}
        >
          发送
        </button>
      </div>

      {showEvalPanel && <EvalPanel />}
    </div>
  );
}
