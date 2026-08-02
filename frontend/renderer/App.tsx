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
import LiquidBackground from "./components/LiquidBackground";
import Sidebar, { type ViewKey } from "./components/Sidebar";
import ArtifactPanel, { type Artifact } from "./components/ArtifactPanel";
import HomeView from "./views/HomeView";
import KnowledgeView from "./views/KnowledgeView";
import MemoryView from "./views/MemoryView";
import SettingsView from "./views/SettingsView";
import { deAIfy } from "./utils/deAIfy";
import "./styles/design-tokens.css";

// ──────────────────────────────────────────────────────────────────────────────
// 类型定义
// ──────────────────────────────────────────────────────────────────────────────

type ConnStatus = "connected" | "disconnected" | "reconnecting";

type EventType =
  | "user"
  | "thinking"
  | "tool_call"
  | "tool_result"
  | "delta"
  | "final"
  | "error";

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
  delta: { bg: "#e8eaf6", label: "Streaming", icon: "…" },
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

const FILES_BASE = "http://127.0.0.1:8765/files/outputs";

function imagePathToUrl(path: string): string {
  // 取 outputs/ 之后的部分作为 filename,拼接后端文件服务绝对地址
  // (vite 5173 下相对路径会请求前端自身导致 404)
  const match = path.match(/outputs\/([\w\-\.]+)$/i);
  const filename = match ? match[1] : path.replace(/^\/?outputs\//, "");
  return `${FILES_BASE}/${filename}`;
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
      // 推理过程: reasoning 增量优先, 兼容旧版 content 字段
      return String(payload.reasoning ?? payload.content ?? "");
    case "tool_call": {
      const name = payload.tool_name ?? payload.name ?? "unknown";
      const args = payload.arguments ?? payload.args ?? "";
      return `${name}(${typeof args === "string" ? args : JSON.stringify(args)})`;
    }
    case "tool_result":
      return String(payload.output ?? payload.result ?? JSON.stringify(payload));
    case "final":
      return deAIfy(String(payload.content ?? ""));
    case "delta":
      return deAIfy(String(payload.content ?? ""));
    case "error":
      return String(payload.message ?? JSON.stringify(payload));
    default:
      return JSON.stringify(payload);
  }
}


// ──────────────────────────────────────────────────────────────────────────────
// 视图组件(评估已移除; 设置/知识库/记忆见 views/ 目录; 首页见 HomeView)
// ──────────────────────────────────────────────────────────────────────────────

// ──────────────────────────────────────────────────────────────────────────────
// 主组件
// ──────────────────────────────────────────────────────────────────────────────

export default function App(): JSX.Element {
  const [events, setEvents] = useState<ReactEvent[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<ConnStatus>("disconnected");
  const [sessionId, setSessionId] = useState<number>(() => getSessionIdFromUrl());
  const [realSessionId, setRealSessionId] = useState<number | null>(null);
  const [activeSkill, setActiveSkill] = useState<string | null>(null);
  const [view, setView] = useState<ViewKey>("home");
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

  // 右栏产物: 从 tool_result 提取图片 + 文件(去重)
  const artifacts = useMemo<Artifact[]>(() => {
    const list: Artifact[] = [];
    const fileRe =
      /(?:\/?outputs\/)?[\w\-\.]+\.(?:xlsx|docx|csv|html|md|pdf|json|txt|pptx|zip)/gi;
    for (const ev of events) {
      if (ev.event_type !== "tool_result") continue;
      const text = formatPayload("tool_result", ev.payload);
      for (const p of extractImagePaths(text)) {
        list.push({ type: "image", url: imagePathToUrl(p), name: p });
      }
      const files = text.match(fileRe) ?? [];
      for (const f of files) {
        const name = f.split("/").pop() ?? f;
        list.push({ type: "file", url: `${FILES_BASE}/${name}`, name });
      }
    }
    return Array.from(new Map(list.map((a) => [a.url, a])).values());
  }, [events]);

  const [artifactsOpen, setArtifactsOpen] = useState(true);

  // HomeView 模式按钮: 激活 skill + 切换到对话视图
  const handlePickMode = async (
    skill: "office" | "data_analysis" | "frontend_design"
  ): Promise<void> => {
    const sid = realSessionId ?? sessionId;
    try {
      const resp = await fetch(
        `http://127.0.0.1:8765/admin/sessions/${sid}/activate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ skill_name: skill }),
        }
      );
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail ?? `HTTP ${resp.status}`);
      }
      setActiveSkill(skill);
      setView("chat");
    } catch (e) {
      // eslint-disable-next-line no-alert
      window.alert(`激活 ${skill} 失败: ${String(e)}`);
    }
  };

  // 任务树: 切换到历史会话 → 改 sessionId, 触发 connect 重连 + 后端 replay
  const handleSwitchSession = (id: number, skillName?: string | null): void => {
    if (id === sessionId && view === "chat") return;
    // 关闭当前 ws(connect effect 依赖 sessionId 会重连)
    const ws = wsRef.current;
    if (ws) {
      ws.onclose = null;
      ws.close();
      wsRef.current = null;
    }
    setEvents([]);
    setActiveSkill(skillName ?? null);
    lastTurnRef.current = 0;
    // 切换历史会话: 全量加载(忽略服务端 ws_offset, 否则 offset=1 会跳过第 1 轮)
    fullReloadRef.current = true;
    setRealSessionId(id);
    setSessionId(id);
    // 进入对话视图(恢复该会话的 skill, 若无 skill 则回首页选模式)
    setView(skillName ? "chat" : "home");
  };

  const wsRef = useRef<WebSocket | null>(null);
  const lastTurnRef = useRef<number>(0);
  const fullReloadRef = useRef<boolean>(false);
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
          // 流式增量: 追加到该 turn 的最后一条 delta 事件(累积显示, 不刷爆列表)
          if (msg.event_type === "delta") {
            const deltaText = String(msg.payload.content ?? "");
            if (deltaText) {
              setEvents((prev) => {
                const last = [...prev]
                  .reverse()
                  .find(
                    (e) =>
                      e.turn === msg.turn &&
                      (e.event_type === "delta" || e.event_type === "final")
                  );
                if (last && last.event_type === "delta") {
                  return prev.map((e) =>
                    e.id === last.id
                      ? {
                          ...e,
                          payload: {
                            turn: msg.turn,
                            content:
                              String(e.payload.content ?? "") + deltaText,
                          },
                        }
                      : e
                  );
                }
                // final 已存在则忽略增量(final 为完整文本)
                if (last && last.event_type === "final") return prev;
                const t = msg.turn as number;
                return [
                  ...prev,
                  {
                    id: ++eventIdRef.current,
                    session_id: msg.session_id ?? sessionId,
                    turn: t,
                    event_type: "delta" as EventType,
                    payload: { turn: t, content: deltaText },
                    ts: Date.now(),
                  },
                ];
              });
            }
            return;
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
        // 补发完成: 全量加载标记复位, 后续同会话重连走增量
        fullReloadRef.current = false;
        break;

      case "ack_confirm":
        break;

      case "turn_end":
        // 一轮结束,可在此做 UI 收尾
        break;

      case "error":
        if (msg.message) {
          // B2 P1-9: skill_not_found → 自动切回首页(重新选择 Skill)
          if (/skill_not_found|skill not found/i.test(msg.message)) {
            setActiveSkill(null);
            setView("home");
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
        // 重连后发送 replay(首次连接 last_turn=0; 切换历史会话 full=true 全量加载)
        sendWs({
          type: "replay",
          session_id: sessionId,
          last_turn: lastTurnRef.current,
          full: fullReloadRef.current,
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
  }, [connect, sessionId]);

  // ── 渲染 ──────────────────────────────────────────────────────────────────
  // ── 渲染: FlowSpace 布局(液体背景 + 侧边栏 + 顶栏 + 内容视图) ─────────
  return (
    <div
      style={{
        position: "relative",
        minHeight: "100vh",
        fontFamily: "var(--font-sans)",
        color: "var(--text-primary)",
        background:
          "linear-gradient(160deg, #eef1f8 0%, #e6ebf6 40%, #ece7f7 100%)",
        WebkitFontSmoothing: "antialiased",
      }}
    >
      <LiquidBackground />
      <div
        style={{
          position: "relative",
          zIndex: 1,
          display: "flex",
          gap: 16,
          padding: 16,
          height: "100vh",
          boxSizing: "border-box",
        }}
      >
        <Sidebar
          active={view}
          onChange={setView}
          currentSessionId={realSessionId ?? sessionId}
          onSwitchSession={handleSwitchSession}
          status={status}
        />
        <div style={{ flex: 1, minWidth: 0, display: "flex", gap: 16, minHeight: 0 }}>
          <main style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column" }}>
            {view === "home" && (
              <HomeView
                onPickMode={handlePickMode}
                activeSkill={activeSkill}
                sessionId={realSessionId ?? sessionId}
              />
            )}
            {view === "chat" && activeSkill && (
              <div
                className="glass-panel"
                style={{
                  flex: 1,
                  display: "flex",
                  flexDirection: "column",
                  padding: 16,
                  minHeight: 0,
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "0 4px 12px",
                    flexShrink: 0,
                  }}
                >
                  <span
                    style={{
                      fontSize: 12,
                      padding: "2px 10px",
                      borderRadius: 10,
                      background: "var(--success-bg)",
                      color: "var(--success-text)",
                      fontWeight: 600,
                    }}
                  >
                    {activeSkill}
                  </span>
                  <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                    session={realSessionId ?? sessionId}
                  </span>
                </div>
                <div
                  style={{
                    flex: 1,
                    minHeight: 0,
                    overflowY: "auto",
                    padding: 4,
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
          // 流式增量(无 final 时显示累积的 delta 文本, 有 final 用完整文本)
          const deltaText = evs
            .filter((e) => e.event_type === "delta")
            .map((e) => formatPayload("delta", e.payload))
            .join("");
          const finalText = finalEv
            ? formatPayload("final", finalEv.payload)
            : deltaText;
          // 有用户消息但还没有最终文本 → AI 正在思考
          const isPending = !!userEv && !finalText && !errorEv;
          const thinkingOpen = openThinkingTurns.has(turn);
          // 推理过程: 拼接该 turn 全部 thinking 事件(reasoning 逐段增量)
          const thinkingText = evs
            .filter((e) => e.event_type === "thinking")
            .map((e) => formatPayload("thinking", e.payload))
            .join("");

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

                    {finalText ? (
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
                          {finalText}
                        </pre>
                      </div>
                    ) : isPending && !thinkingEv ? (
                      <div style={{ color: "#9ca3af", fontSize: 13 }}>💭 思考中…</div>
                    ) : null}

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
          </div>
          )}
          {view === "settings" && <SettingsView />}
          {view === "knowledge" && <KnowledgeView sessionId={realSessionId ?? sessionId} />}
          {view === "memory" && <MemoryView sessionId={realSessionId ?? sessionId} />}
        </main>
        <ArtifactPanel
          open={artifactsOpen}
          artifacts={artifacts}
          onToggle={() => setArtifactsOpen(!artifactsOpen)}
        />
        </div>
      </div>
    </div>
  );
}
