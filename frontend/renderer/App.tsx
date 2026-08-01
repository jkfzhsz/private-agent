// M1 Phase 5 - React chat UI 根组件 (蓝图 §2.15 + §9.4 AC-8)
//
// 功能:
// - 消息输入框 + 发送按钮
// - 流式渲染区域:按 event_type 分块(thinking/tool_call/tool_result/final/error)
// - WS 连接状态指示器(connected/disconnected/reconnecting)
// - 重连机制:指数退避(1s,2s,4s,8s,max 16s),重连后发送 replay(session_id + last_turn)
// - ACK 机制:收到 react_event 后发送 ack(session_id + turn)
// - session_id 管理:首次连接时从 URL 参数获取或生成
import { useCallback, useEffect, useRef, useState } from "react";

// ──────────────────────────────────────────────────────────────────────────────
// 类型定义
// ──────────────────────────────────────────────────────────────────────────────

type ConnStatus = "connected" | "disconnected" | "reconnecting";

type EventType = "thinking" | "tool_call" | "tool_result" | "final" | "error";

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
  thinking: { bg: "#f0f0f0", label: "Thinking", icon: "💭" },
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
// 主组件
// ──────────────────────────────────────────────────────────────────────────────

export default function App(): JSX.Element {
  const [events, setEvents] = useState<ReactEvent[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<ConnStatus>("disconnected");
  const [sessionId] = useState<number>(() => getSessionIdFromUrl());

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
        {events.length === 0 && (
          <div style={{ color: "#999", textAlign: "center", paddingTop: 40 }}>
            发送一条消息开始对话
          </div>
        )}
        {events.map((ev) => {
          const style = EVENT_STYLES[ev.event_type] ?? EVENT_STYLES.error;
          const text = formatPayload(ev.event_type, ev.payload);
          // AC-9: tool_result 含 outputs/*.png 等图片路径时渲染 <img>
          const imagePaths = ev.event_type === "tool_result" ? extractImagePaths(text) : [];
          return (
            <div
              key={ev.id}
              style={{
                backgroundColor: style.bg,
                borderRadius: 6,
                padding: "8px 12px",
                marginBottom: 8,
                fontStyle: ev.event_type === "thinking" ? "italic" : "normal",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <span style={{ fontSize: 12, fontWeight: 600, color: "#333" }}>
                  {style.icon} {style.label}
                  <span style={{ color: "#999", fontWeight: 400, marginLeft: 8 }}>
                    turn={ev.turn}
                  </span>
                </span>
                <span style={{ fontSize: 11, color: "#999" }}>
                  {new Date(ev.ts).toLocaleTimeString()}
                </span>
              </div>
              <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 13, fontFamily: "monospace" }}>
                {text}
              </pre>
              {imagePaths.length > 0 && (
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
                  {imagePaths.map((p) => (
                    <img
                      key={p}
                      src={imagePathToUrl(p)}
                      alt={p}
                      style={{ maxWidth: "100%", borderRadius: 4, border: "1px solid #ddd" }}
                    />
                  ))}
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
  );
}
