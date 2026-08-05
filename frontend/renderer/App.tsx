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
import AgentLibraryView from "./views/AgentLibraryView";
import ResizeHandle from "./components/ResizeHandle";
import HomeView from "./views/HomeView";
import KnowledgeView from "./views/KnowledgeView";
import MemoryView from "./views/MemoryView";
import SettingsView from "./views/SettingsView";
import { deAIfy } from "./utils/deAIfy";
import "./styles/design-tokens.css";

import { adminFetch } from "./utils/apiClient";

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
  | "error"
  // V2 P1: 沙箱流式输出 + 权限确认(蓝图 §6.10 / §5.12)
  | "sandbox_output"
  | "tool_confirmation_required"
  | "tool_confirmation_result";

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
  sandbox_output: { bg: "#f1f5f9", label: "Sandbox", icon: "🖥️" },
  tool_confirmation_required: { bg: "#fef3c7", label: "Permission", icon: "⚠️" },
  tool_confirmation_result: { bg: "#f3e8ff", label: "Permission Result", icon: "🔐" },
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

// V1.1-3.8 任务抽屉按钮
const taskBtnStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "5px 14px",
  borderRadius: 6,
  border: "1px solid #ddd",
  background: "#fff",
  color: "#334155",
  cursor: "pointer",
};

// V1.4-8.4 系统通知(任务完成/失败): 仅应用在后台时提醒, 避免前台打扰
function notifyUser(title: string, body: string): void {
  try {
    if (typeof Notification === "undefined") return;
    if (document.visibilityState === "visible") return; // 前台不打扰
    if (Notification.permission === "granted") {
      new Notification(title, { body });
    } else if (Notification.permission !== "denied") {
      void Notification.requestPermission().then((p) => {
        if (p === "granted") new Notification(title, { body });
      });
    }
  } catch {
    /* 通知失败静默 */
  }
}

// V1.1-3.3 消息操作条按钮
function MsgActionBtn({
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
        fontSize: 11,
        border: "1px solid #e5e7eb",
        background: "#f9fafb",
        color: danger ? "#dc2626" : "#6b7280",
        borderRadius: 6,
        padding: "3px 10px",
        cursor: "pointer",
        lineHeight: 1.5,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = danger ? "#fee2e2" : "#eef2ff";
        e.currentTarget.style.color = danger ? "#b91c1c" : "#4f46e5";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "#f9fafb";
        e.currentTarget.style.color = danger ? "#dc2626" : "#6b7280";
      }}
    >
      {label}
    </button>
  );
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
    case "sandbox_output":
      // 沙箱终端流式输出: 仅显示 chunk 内容
      return String(payload.chunk ?? "");
    case "tool_confirmation_required":
      return String(payload.message ?? "需要确认");
    case "tool_confirmation_result":
      return payload.approved ? "已批准" : "已拒绝";
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
  // 阶段三批次3(T3.4): 编辑重发纠正沉淀 —— 记录被编辑的原消息(发送时提取 correction 记忆)
  const [editingOriginal, setEditingOriginal] = useState<string | null>(null);
  const [status, setStatus] = useState<ConnStatus>("disconnected");
  // 对话文档上传: 文件引用(上传成功后记录, 发送时附带路径)
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pendingUpload, setPendingUpload] = useState<{ name: string; path: string } | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // 生成中状态(显示"停止"按钮)
  const [isGenerating, setIsGenerating] = useState(false);
  // 对话中切换技能弹层
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const [availableSkills, setAvailableSkills] = useState<{ name: string; version: string; enabled: boolean }[]>([]);
  const [sessionId, setSessionId] = useState<number>(() => getSessionIdFromUrl());
  const [realSessionId, setRealSessionId] = useState<number | null>(null);
  const [activeSkill, setActiveSkill] = useState<string | null>(null);
  const [view, setView] = useState<ViewKey>("home");
  // V1.4-8.4 主题切换(light/dark, localStorage 持久化)
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    try {
      return (localStorage.getItem("pa:theme") as "light" | "dark") || "light";
    } catch {
      return "light";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("pa:theme", theme);
    } catch {
      /* 忽略 */
    }
  }, [theme]);
  const toggleTheme = (): void => setTheme((t) => (t === "dark" ? "light" : "dark"));
  // 每个 turn 的"推理过程"展开状态(默认收起)
  const [openThinkingTurns, setOpenThinkingTurns] = useState<Set<number>>(new Set());
  // 会话级模型选择: "auto"(fallback 链) 或 provider 名(手动锁定)
  const [sessionModel, setSessionModel] = useState<string>("auto");
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  // 工作区选择(画地为牢): 会话级工作目录
  const [workspace, setWorkspace] = useState<string | null>(null);
  const [editingWorkspace, setEditingWorkspace] = useState(false);
  const [workspaceInput, setWorkspaceInput] = useState("");

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

  // V1.1 布局优化: 左右分区宽度(拖拽分隔条调整, clamp 防极端值)
  const [sidebarW, setSidebarW] = useState(220);
  const [panelW, setPanelW] = useState(300);
  const clampSidebar = (w: number): number => Math.min(400, Math.max(140, w));
  const clampPanel = (w: number): number => Math.min(560, Math.max(240, w));

  // HomeView 模式按钮: 激活 skill + 切换到对话视图
  // 会话锁定(AC-4): 同一 session 激活后不允许切换 skill(409),
  // 因此切换到不同模式时必须新建会话(随机 session_id, 后端懒创建)
  const handlePickMode = async (skill: string): Promise<void> => {
    const needNewSession = activeSkill !== null && activeSkill !== skill;
    const sid =
      needNewSession
        ? Math.floor(Math.random() * 100000) + 1
        : realSessionId ?? sessionId;
    if (needNewSession) {
      // 新会话: 触发 ws 重连 + 清空当前对话(后端对新 session 懒创建行)
      setSessionId(sid);
      setRealSessionId(null);
      setEvents([]);
    }
    try {
      const resp = await adminFetch(
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
  const handleSwitchSession = (
    id: number,
    skillName?: string | null,
    modelId?: string | null
  ): void => {
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
    setSessionModel(modelId ?? "auto");
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

  // 打断/停止: 发送 cancel 消息, 后端取消当前 turn
  const stopGeneration = useCallback((): void => {
    sendWs({ type: "cancel", session_id: sessionId });
  }, [sendWs, sessionId]);

  // V1.1-3.3 消息精细化操作: 重生成/收藏/删除/复制
  const [starredTurns, setStarredTurns] = useState<Set<number>>(new Set());
  const [deletedTurns, setDeletedTurns] = useState<Set<number>>(new Set());

  const getTurnMessages = useCallback(
    async (turn: number): Promise<{ id: number; role: string; content: string | null; starred: boolean }[]> => {
      try {
        const resp = await adminFetch(
          `http://127.0.0.1:8765/admin/sessions/${realSessionId ?? sessionId}/turn/${turn}/messages`
        );
        if (!resp.ok) return [];
        return await resp.json();
      } catch {
        return [];
      }
    },
    [realSessionId, sessionId]
  );

  const regenerateTurn = useCallback(
    (turn: number): void => {
      sendWs({ type: "regenerate", session_id: realSessionId ?? sessionId, turn });
    },
    [sendWs, realSessionId, sessionId]
  );

  const toggleStar = useCallback(
    async (turn: number): Promise<void> => {
      const msgs = await getTurnMessages(turn);
      const target = [...msgs].reverse().find((m) => m.role === "assistant") ?? msgs[0];
      if (!target) return;
      const next = !target.starred;
      try {
        const resp = await adminFetch(`http://127.0.0.1:8765/admin/messages/${target.id}/starred`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ starred: next }),
        });
        if (!resp.ok) return;
        setStarredTurns((prev) => {
          const s = new Set(prev);
          if (next) s.add(turn);
          else s.delete(turn);
          return s;
        });
      } catch {
        /* 收藏失败静默 */
      }
    },
    [getTurnMessages, realSessionId, sessionId]
  );

  const deleteTurnMessage = useCallback(
    async (turn: number): Promise<void> => {
      const msgs = await getTurnMessages(turn);
      const target =
        [...msgs].reverse().find((m) => m.role === "assistant" && m.content) ?? msgs[0];
      if (!target) return;
      if (!window.confirm("删除这条回复? (软删除, 不影响会话其余内容)")) return;
      try {
        const resp = await adminFetch(`http://127.0.0.1:8765/admin/messages/${target.id}`, {
          method: "DELETE",
        });
        if (!resp.ok) return;
        setDeletedTurns((prev) => new Set(prev).add(turn));
      } catch {
        /* 删除失败静默 */
      }
    },
    [getTurnMessages]
  );

  const copyText = useCallback(async (text: string): Promise<void> => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* 剪贴板失败静默 */
    }
  }, []);

  // V1.1-3.4 输入区增强: 图片粘贴上传 + 代码块复制
  const [pendingImage, setPendingImage] = useState<{ name: string; path: string; preview: string } | null>(null);

  const uploadImage = useCallback(async (file: File): Promise<void> => {
    const MAX = 5 * 1024 * 1024; // 图片 ≤5MB(token 成本控制, 方案 §8.4)
    if (file.size > MAX) {
      // eslint-disable-next-line no-alert
      window.alert("图片超过 5MB 限制");
      return;
    }
    try {
      const buf = await file.arrayBuffer();
      let binary = "";
      const bytes = new Uint8Array(buf);
      const chunk = 0x8000;
      for (let i = 0; i < bytes.length; i += chunk) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
      }
      const b64 = btoa(binary);
      const resp = await adminFetch("http://127.0.0.1:8765/admin/files/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name || "pasted-image.png", content_base64: b64 }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = (await resp.json()) as { name: string; path: string };
      const preview = URL.createObjectURL(file);
      setPendingImage({ name: data.name, path: data.path, preview });
    } catch (e) {
      // eslint-disable-next-line no-alert
      window.alert(`图片上传失败: ${String(e)}`);
    }
  }, []);

  const handlePaste = useCallback(
    (e: React.ClipboardEvent): void => {
      const items = e.clipboardData?.items;
      if (!items) return;
      for (const item of Array.from(items)) {
        if (item.type.startsWith("image/")) {
          e.preventDefault();
          const file = item.getAsFile();
          if (file) void uploadImage(file);
          break;
        }
      }
    },
    [uploadImage]
  );

  // V1.4-8.4 提示词模板库(localStorage, 跨会话)
  const [templates, setTemplates] = useState<{ name: string; content: string }[]>(() => {
    try {
      return JSON.parse(localStorage.getItem("pa:templates") ?? "[]") as { name: string; content: string }[];
    } catch {
      return [];
    }
  });
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [tplName, setTplName] = useState("");
  const [tplMsg, setTplMsg] = useState<string | null>(null);

  const persistTemplates = (next: { name: string; content: string }[]): void => {
    setTemplates(next);
    try {
      localStorage.setItem("pa:templates", JSON.stringify(next));
    } catch {
      /* 忽略 */
    }
  };

  const saveCurrentAsTemplate = (): void => {
    const content = input.trim();
    if (!content) {
      setTplMsg("输入区为空, 无法保存模板");
      return;
    }
    const name = tplName.trim() || content.slice(0, 20);
    persistTemplates([...templates, { name, content }]);
    setTplName("");
    setTplMsg(`已保存模板「${name}」`);
  };

  const insertTemplate = (content: string): void => {
    setInput((prev) => (prev ? `${prev}\n${content}` : content));
    setTemplatesOpen(false);
    inputRef.current?.focus();
  };

  const removeTemplate = (name: string): void => {
    persistTemplates(templates.filter((t) => t.name !== name));
  };

  const copyCodeBlocks = useCallback(async (text: string): Promise<void> => {
    const re = /```(?:[a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g;
    const blocks: string[] = [];
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      if (m[1] && m[1].trim()) blocks.push(m[1].trim());
    }
    if (blocks.length === 0) return;
    await copyText(blocks.join("\n\n"));
  }, [copyText]);

  // V1.1-3.5 上下文可控: 会话设置弹窗(记忆开关/截断/查看系统提示词)
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [memoryEnabled, setMemoryEnabled] = useState(true);
  const [truncateTurn, setTruncateTurn] = useState(0);
  const [systemPromptText, setSystemPromptText] = useState<string | null>(null);
  const [settingsMsg, setSettingsMsg] = useState<string | null>(null);
  // V1.3-7.2 工作流自动化: 自动连续执行配置(会话级)
  const [autoExec, setAutoExec] = useState(false);
  const [autoRounds, setAutoRounds] = useState(3);

  const openSettings = useCallback(async (): Promise<void> => {
    setSettingsOpen(true);
    setSettingsMsg(null);
    setSystemPromptText(null);
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/sessions/${realSessionId ?? sessionId}`
      );
      if (resp.ok) {
        const data = await resp.json();
        setMemoryEnabled(data.memory_enabled !== false);
        setAutoExec(data.auto_execute === true);
        setAutoRounds(Number(data.max_rounds ?? 3));
        setTruncateTurn(0);
      }
    } catch {
      /* 详情加载失败静默 */
    }
  }, [realSessionId, sessionId]);

  const toggleMemory = useCallback(async (): Promise<void> => {
    const next = !memoryEnabled;
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/sessions/${realSessionId ?? sessionId}/memory-enabled`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: next }),
        }
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setMemoryEnabled(next);
      setSettingsMsg(next ? "已开启会话记忆" : "已关闭会话记忆(不再注入/提取)");
    } catch (e) {
      setSettingsMsg(`记忆开关失败: ${String(e)}`);
    }
  }, [memoryEnabled, realSessionId, sessionId]);

  // V1.3-7.2 工作流自动化: 保存会话级 auto_execute/max_rounds
  const saveAutoExec = useCallback(async (): Promise<void> => {
    const rounds = Math.min(20, Math.max(1, Number(autoRounds) || 3));
    setAutoRounds(rounds);
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/sessions/${realSessionId ?? sessionId}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ auto_execute: autoExec, max_rounds: rounds }),
        }
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setSettingsMsg(
        autoExec
          ? `已开启自动执行(最多 ${rounds} 轮, 发送消息时生效)`
          : "已关闭自动执行"
      );
    } catch (e) {
      setSettingsMsg(`自动执行配置失败: ${String(e)}`);
    }
  }, [autoExec, autoRounds, realSessionId, sessionId]);

  const doTruncate = useCallback(async (): Promise<void> => {
    const afterTurn = Number(truncateTurn);
    if (!Number.isInteger(afterTurn) || afterTurn < 0) {
      setSettingsMsg("请输入有效的轮次(>=0)");
      return;
    }
    if (!window.confirm(`截断上下文: 保留 0~${afterTurn} 轮, 之后的对话将被移出上下文(可从会话导出追溯)。确定?`)) return;
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/sessions/${realSessionId ?? sessionId}/truncate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ after_turn: afterTurn }),
        }
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setSettingsMsg(`已截断: ${data.truncated_messages} 条消息移出上下文`);
    } catch (e) {
      setSettingsMsg(`截断失败: ${String(e)}`);
    }
  }, [truncateTurn, realSessionId, sessionId]);

  const loadSystemPrompt = useCallback(async (): Promise<void> => {
    setSystemPromptText(null);
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/sessions/${realSessionId ?? sessionId}/system-prompt`
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setSystemPromptText(data.system_prompt ?? "(空)");
    } catch (e) {
      setSettingsMsg(`加载系统提示词失败: ${String(e)}`);
    }
  }, [realSessionId, sessionId]);

  // V1.1-3.8 任务状态反馈: 任务列表抽屉(轮次/工具/错误/重试/终止)
  const [tasksOpen, setTasksOpen] = useState(false);
  const [eventsOpen, setEventsOpen] = useState(false);
  const [diagOpen, setDiagOpen] = useState(false);
  const [tasksData, setTasksData] = useState<{
    status: string;
    updated_at: string | null;
    total_turns: number;
    turns: { turn: number; events: Record<string, number>; error?: string; last_ts?: string | null }[];
  } | null>(null);
  const [tasksLoading, setTasksLoading] = useState(false);

  const loadTasks = useCallback(async (): Promise<void> => {
    setTasksLoading(true);
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/tasks?session_id=${realSessionId ?? sessionId}`
      );
      if (resp.ok) setTasksData(await resp.json());
    } catch {
      setTasksData(null);
    } finally {
      setTasksLoading(false);
    }
  }, [realSessionId, sessionId]);

  // V1.2-6.2: 工具事件日志时间线(读 react_events)
  const [eventsLog, setEventsLog] = useState<
    { id: number; turn: number; event_type: string; ts: string | null; summary: string }[] | null
  >(null);
  const [eventsLoading, setEventsLoading] = useState(false);

  const loadEvents = useCallback(async (): Promise<void> => {
    setEventsLoading(true);
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/events?session_id=${realSessionId ?? sessionId}&limit=60`
      );
      if (resp.ok) setEventsLog(await resp.json());
    } catch {
      setEventsLog(null);
    } finally {
      setEventsLoading(false);
    }
  }, [realSessionId, sessionId]);

  // V1.2-6.3: 用量统计 + 错误摘要(任务抽屉内)
  const [usageData, setUsageData] = useState<{
    total_calls: number;
    total_tokens: number;
    input_tokens: number;
    output_tokens: number;
    total_cost: number;
    currency: string;
  } | null>(null);
  const [errorsData, setErrorsData] = useState<{
    total_errors: number;
    distinct_errors: number;
    top: { message: string; count: number }[];
  } | null>(null);
  const [diagBusy, setDiagBusy] = useState(false);

  const loadDiagnostics = useCallback(async (): Promise<void> => {
    setDiagBusy(true);
    try {
      const [uResp, eResp] = await Promise.all([
        adminFetch(
          `http://127.0.0.1:8765/admin/usage?session_id=${realSessionId ?? sessionId}`
        ),
        adminFetch(
          `http://127.0.0.1:8765/admin/errors/summary?session_id=${realSessionId ?? sessionId}`
        ),
      ]);
      if (uResp.ok) setUsageData(await uResp.json());
      if (eResp.ok) setErrorsData(await eResp.json());
    } catch {
      /* 诊断加载失败静默 */
    } finally {
      setDiagBusy(false);
    }
  }, [realSessionId, sessionId]);

  // 技能切换弹层: 打开时加载技能列表; 选择 → 新会话激活(handlePickMode)
  useEffect(() => {
    if (!skillPickerOpen) return;
    let cancelled = false;
    void (async () => {
      try {
        const resp = await adminFetch("http://127.0.0.1:8765/admin/skills");
        const data = (await resp.json()) as { name: string; version: string; enabled: boolean }[];
        if (!cancelled) setAvailableSkills(Array.isArray(data) ? data : []);
      } catch {
        if (!cancelled) setAvailableSkills([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [skillPickerOpen]);

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
          // 推理增量: 逐 token thinking 事件累积合并到该 turn 最后一条
          // thinking(避免每条都追加 → 渲染抖动/多条"思考中"卡片)
          if (msg.event_type === "thinking") {
            const reasoning = String(
              msg.payload.reasoning ?? msg.payload.content ?? ""
            );
            setEvents((prev) => {
              const last = [...prev]
                .reverse()
                .find((e) => e.turn === msg.turn && e.event_type === "thinking");
              if (last) {
                return prev.map((e) =>
                  e.id === last.id
                    ? {
                        ...e,
                        payload: {
                          turn: msg.turn,
                          reasoning:
                            String(e.payload.reasoning ?? "") + reasoning,
                        },
                      }
                    : e
                );
              }
              const t = msg.turn as number;
              return [
                ...prev,
                {
                  id: ++eventIdRef.current,
                  session_id: msg.session_id ?? sessionId,
                  turn: t,
                  event_type: "thinking" as EventType,
                  payload: { turn: t, reasoning },
                  ts: Date.now(),
                },
              ];
            });
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
        setIsGenerating(false);
        // V1.4-8.4: 应用在后台时系统通知(任务完成)
        void notifyUser("任务完成", "本轮对话已结束");
        break;

      case "turn_cancelled":
        // 打断/停止: 后端已取消当前 turn
        setIsGenerating(false);
        void notifyUser("任务已停止", "你手动停止了本轮对话");
        break;

      case "error":
        setIsGenerating(false);
        void notifyUser("任务出错", String(msg.message ?? "未知错误"));
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
  // 对话文档上传: 选择文件 → base64 上传 → 记录文件引用(发送时附带)
  const handleFilePick = useCallback(
    async (file: File | undefined | null): Promise<void> => {
      if (!file) return;
      const MAX = 15 * 1024 * 1024;
      if (file.size > MAX) {
        // eslint-disable-next-line no-alert
        window.alert("文件超过 15MB 限制");
        return;
      }
      try {
        const buf = await file.arrayBuffer();
        let binary = "";
        const bytes = new Uint8Array(buf);
        const chunk = 0x8000;
        for (let i = 0; i < bytes.length; i += chunk) {
          binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
        }
        const b64 = btoa(binary);
        const resp = await adminFetch("http://127.0.0.1:8765/admin/files/upload", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ filename: file.name, content_base64: b64 }),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.error ?? `HTTP ${resp.status}`);
        }
        const data = (await resp.json()) as { name: string; path: string };
        setPendingUpload({ name: data.name, path: data.path });
      } catch (e) {
        // eslint-disable-next-line no-alert
        window.alert(`上传失败: ${String(e)}`);
      }
    },
    []
  );

  const sendMessage = useCallback((): void => {
    let content = input.trim();
    if (!content) return;
    // 阶段三批次3(T3.4): 编辑重发 → 先异步沉淀纠正记忆(不阻塞发送)
    if (editingOriginal && editingOriginal.trim() !== content) {
      try {
        void adminFetch(
          `http://127.0.0.1:8765/admin/sessions/${realSessionId ?? sessionId}/extract_correction`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ original: editingOriginal, corrected: content }),
          },
        ).catch(() => undefined);
      } catch {
        // 沉淀失败不影响对话
      }
    }
    setEditingOriginal(null);
    // 已上传文件: 消息头部附带文件路径(模型可用 file_read 工具读取)
    if (pendingUpload) {
      content = `[已上传文件: ${pendingUpload.name} 路径: ${pendingUpload.path}]\n${content}`;
    }
    // V1.1-3.4 粘贴图片: 附带图片引用(模型可 file_read 尝试读取/告知已保存)
    if (pendingImage) {
      content = `[用户粘贴图片: ${pendingImage.name} 路径: ${pendingImage.path}]\n${content}`;
    }
    sendWs({
      type: "user_message",
      session_id: sessionId,
      content,
      // V1.3-7.2 工作流自动化: 携带会话级自动执行配置(后端优先取显式传参)
      auto_execute: autoExec || undefined,
      max_rounds: autoExec ? autoRounds : undefined,
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
    setPendingUpload(null); // 发送后清除文件引用(一次一文件)
    setPendingImage(null); // 发送后清除图片引用
    setIsGenerating(true); // 生成中(显示"停止"按钮)
  }, [input, sessionId, sendWs, pendingUpload, pendingImage, editingOriginal, realSessionId, autoExec, autoRounds]);

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

  // 加载默认工作区(画地为牢选择器显示用; 会话工作区后端持久化)
  useEffect(() => {
    let cancelled = false;
    adminFetch("http://localhost:8765/admin/workspaces")
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) {
          const def = (data.default as string) || "";
          const candidates = (data.workspaces ?? []) as string[];
          setWorkspace(def || (candidates[0] ?? null));
          setWorkspaceInput(def || (candidates[0] ?? ""));
        }
      })
      .catch(() => {
        /* 后端未起时忽略 */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 保存会话工作区(画地为牢)
  const saveWorkspace = useCallback(async (): Promise<void> => {
    const path = workspaceInput.trim();
    try {
      const resp = await adminFetch(
        `http://localhost:8765/admin/sessions/${realSessionId ?? sessionId}/workspace`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workspace: path || null }),
        }
      );
      const data = await resp.json();
      if (!resp.ok) {
        window.alert(`工作区设置失败: ${data.error ?? data.detail ?? `HTTP ${resp.status}`}`);
        return;
      }
      setWorkspace(data.workspace ?? null);
      setEditingWorkspace(false);
    } catch (err) {
      window.alert(`工作区设置失败: ${String(err)}`);
    }
  }, [realSessionId, sessionId, workspaceInput]);

  // 加载可用模型列表(模型选择器用)
  useEffect(() => {
    let cancelled = false;
    adminFetch("http://localhost:8765/admin/settings/providers")
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) {
          const names = (data.providers ?? [])
            .filter((p: { enabled: boolean }) => p.enabled)
            .map((p: { name: string }) => p.name);
          setModelOptions(names);
        }
      })
      .catch(() => {
        // 后端未就绪时静默, 选择器只显示"自动"
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 切换会话模型选择(自动/手动)
  const changeSessionModel = async (modelId: string): Promise<void> => {
    const target = realSessionId ?? sessionId;
    if (target <= 0) return;
    try {
      const resp = await adminFetch(
        `http://localhost:8765/admin/sessions/${target}/model`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model_id: modelId }),
        }
      );
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail ?? `HTTP ${resp.status}`);
      setSessionModel(modelId);
    } catch (err) {
      window.alert(`切换模型失败: ${String(err)}`);
    }
  };

  // ── 渲染 ──────────────────────────────────────────────────────────────────
  // ── 渲染: FlowSpace 布局(液体背景 + 侧边栏 + 顶栏 + 内容视图) ─────────
  // V1.1 布局优化: 外层 100vw×100vh 铺满视口(overflow hidden), 内层三栏 flex
  // + 拖拽分隔条(ResizeHandle), 左右栏可拖拽调宽、可折叠
  return (
    <div
      style={{
        position: "relative",
        width: "100vw",
        height: "100vh",
        overflow: "hidden",
        fontFamily: "var(--font-sans)",
        color: "var(--text-primary)",
        background:
          theme === "dark"
            ? "linear-gradient(160deg, #0f172a 0%, #1e1b4b 45%, #111827 100%)"
            : "linear-gradient(160deg, #eef1f8 0%, #e6ebf6 40%, #ece7f7 100%)",
        transition: "background 0.4s var(--transition-smooth)",
        WebkitFontSmoothing: "antialiased",
      }}
    >
      <LiquidBackground />
      <div
        style={{
          position: "relative",
          zIndex: 1,
          display: "flex",
          width: "100%",
          height: "100%",
          overflow: "hidden",
        }}
      >
        <Sidebar
          active={view}
          onChange={setView}
          currentSessionId={realSessionId ?? sessionId}
          onSwitchSession={handleSwitchSession}
          status={status}
          width={sidebarW}
        />
        <ResizeHandle
          onDrag={(delta) => setSidebarW((w) => clampSidebar(w + delta))}
        />
        <div style={{ flex: 1, minWidth: 0, display: "flex", minHeight: 0 }}>
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
                  {/* 对话中加载/切换技能: 弹技能面板 → 新会话激活 */}
                  <button
                    onClick={() => setSkillPickerOpen(true)}
                    title="切换技能(将新建会话)"
                    style={{
                      fontSize: 12, padding: "4px 12px", borderRadius: 8,
                      border: "1px solid #ddd", background: "#fff", cursor: "pointer",
                    }}
                  >
                    🔄 切换技能
                  </button>
                  <span style={{ flex: 1 }} />
                  {/* 会话级模型选择: 自动(fallback 链) / 手动锁定单模型 */}
                  <select
                    value={sessionModel}
                    onChange={(e) => void changeSessionModel(e.target.value)}
                    style={{
                      fontSize: 11,
                      padding: "4px 8px",
                      borderRadius: 6,
                      border: "1px solid rgba(148,163,184,0.3)",
                      background: "rgba(255,255,255,0.6)",
                      color: "var(--text-primary)",
                      outline: "none",
                    }}
                    title="选择本会话使用的模型: 自动=fallback 链降级; 手动=全程使用所选模型"
                  >
                    <option value="auto">🤖 自动(fallback 链)</option>
                    {modelOptions.map((m) => (
                      <option key={m} value={m}>
                        {m}
                        {sessionModel === m ? " ✓" : ""}
                      </option>
                    ))}
                  </select>
                  {/* V1.4-8.4 主题切换 */}
                  <button
                    onClick={toggleTheme}
                    title={theme === "dark" ? "切换到亮色主题" : "切换到暗色主题"}
                    style={{
                      fontSize: 13, padding: "4px 10px", borderRadius: 8,
                      border: "1px solid #ddd", background: "#fff", cursor: "pointer",
                    }}
                  >
                    {theme === "dark" ? "☀️" : "🌙"}
                  </button>
                  {/* V1.1-3.5 会话设置(记忆开关/截断/系统提示词) */}
                  <button
                    onClick={() => void openSettings()}
                    title="会话设置(记忆/截断/系统提示词)"
                    style={{
                      fontSize: 12, padding: "4px 10px", borderRadius: 8,
                      border: "1px solid #ddd", background: "#fff", cursor: "pointer",
                    }}
                  >
                    ⚙ 设置
                  </button>
                  {/* V1.1-3.8 任务状态(轮次/工具/错误/重试) */}
                  <button
                    onClick={() => {
                      setTasksOpen(true);
                      void loadTasks();
                    }}
                    title="任务执行状态"
                    style={{
                      fontSize: 12, padding: "4px 10px", borderRadius: 8,
                      border: "1px solid #ddd", background: "#fff", cursor: "pointer",
                    }}
                  >
                    📋 任务
                  </button>
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
          // V2 P1: 沙箱终端流式输出(按 turn 合并全部 chunk, 等宽字体终端效果)
          const sandboxText = evs
            .filter((e) => e.event_type === "sandbox_output")
            .map((e) => String(e.payload.chunk ?? ""))
            .join("");
          // V2 P1: 权限确认请求(渲染确认卡片, 同意/拒绝按钮)
          const confirmEvents = evs.filter(
            (e) => e.event_type === "tool_confirmation_required"
          );
          const confirmResultEv = evs.find(
            (e) => e.event_type === "tool_confirmation_result"
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
                  {/* 阶段三批次3(T3.4): 编辑重发(最后一条 user 消息, 非生成中) */}
                  {!isGenerating &&
                    turnGroups.length > 0 &&
                    turn === turnGroups[turnGroups.length - 1][0] && (
                      <button
                        onClick={() => {
                          const orig = formatPayload("user", userEv.payload);
                          setEditingOriginal(orig);
                          setInput(orig);
                          inputRef.current?.focus();
                        }}
                        title="编辑并重发(自动沉淀纠正记忆)"
                        style={{
                          alignSelf: "center",
                          marginLeft: 6,
                          padding: "4px 8px",
                          fontSize: 11,
                          borderRadius: 6,
                          border: "1px solid var(--border)",
                          background: "var(--panel-bg)",
                          color: "var(--text-tertiary)",
                          cursor: "pointer",
                        }}
                      >
                        ✎ 编辑
                      </button>
                    )}
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
                    智
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "#374151", marginBottom: 4 }}>
                      私人智能体
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
                        // V1.2-6.3: 工具耗时展示
                        const durationMs =
                          te.event_type === "tool_result"
                            ? (te.payload.duration_ms as number | undefined)
                            : undefined;
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
                                <>✅ {text.slice(0, 120)}{text.length > 120 ? "…" : ""}
                                  {durationMs != null ? ` · ${durationMs}ms` : ""}
                                </>
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

                    {/* V2 P1: 沙箱终端流式输出 */}
                    {sandboxText && (
                      <div
                        style={{
                          marginBottom: 8,
                          border: "1px solid #e2e8f0",
                          borderRadius: 8,
                          overflow: "hidden",
                        }}
                      >
                        <div
                          style={{
                            padding: "4px 10px",
                            background: "#f8fafc",
                            fontSize: 11,
                            fontWeight: 600,
                            color: "#64748b",
                            borderBottom: "1px solid #e2e8f0",
                          }}
                        >
                          🖥️ 沙箱输出
                        </div>
                        <pre
                          style={{
                            margin: 0,
                            padding: "8px 12px",
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-all",
                            fontFamily: "Consolas, 'Courier New', monospace",
                            fontSize: 12,
                            color: "#334155",
                            maxHeight: 260,
                            overflowY: "auto",
                          }}
                        >
                          {sandboxText}
                        </pre>
                      </div>
                    )}

                    {/* V2 P1 + 阶段三批次1(B-8): 权限确认卡片(风险分级 + 来源解释) */}
                    {confirmEvents.length > 0 &&
                      confirmEvents.map((ce) => {
                        const args = ce.payload.args_summary as Record<string, unknown> | undefined;
                        const argsPreview = args
                          ? JSON.stringify(args).slice(0, 200)
                          : "";
                        const risk = (ce.payload.risk_level as string) || "medium";
                        const reason = (ce.payload.reason as string) || "";
                        const riskColor =
                          risk === "high" ? "#dc2626" : risk === "medium" ? "#d97706" : "#16a34a";
                        const riskLabel =
                          risk === "high" ? "高风险" : risk === "medium" ? "中风险" : "低风险";
                        return (
                          <div
                            key={ce.id}
                            style={{
                              marginBottom: 8,
                              border: "1px solid #fcd34d",
                              borderRadius: 8,
                              background: "#fffbeb",
                              padding: "10px 12px",
                            }}
                          >
                            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                              <span style={{ fontSize: 12, fontWeight: 600, color: "#92400e" }}>
                                ⚠️ {formatPayload("tool_confirmation_required", ce.payload)}
                              </span>
                              <span
                                style={{
                                  fontSize: 10,
                                  fontWeight: 700,
                                  color: "#fff",
                                  background: riskColor,
                                  borderRadius: 10,
                                  padding: "1px 8px",
                                }}
                              >
                                {riskLabel}
                              </span>
                            </div>
                            {reason && (
                              <div style={{ fontSize: 11, color: "#78350f", marginBottom: 4 }}>
                                原因: {reason}
                              </div>
                            )}
                            {argsPreview && (
                              <pre
                                style={{
                                  margin: "0 0 8px",
                                  whiteSpace: "pre-wrap",
                                  wordBreak: "break-all",
                                  fontSize: 11,
                                  color: "#b45309",
                                  fontFamily: "Consolas, monospace",
                                }}
                              >
                                {argsPreview}
                              </pre>
                            )}
                            {confirmResultEv ? (
                              <div style={{ fontSize: 12, color: "#6b7280" }}>
                                {formatPayload("tool_confirmation_result", confirmResultEv.payload)}
                              </div>
                            ) : (
                              <div style={{ display: "flex", gap: 8 }}>
                                <button
                                  onClick={() => {
                                    sendWs({
                                      type: "tool_confirmation",
                                      session_id: realSessionId ?? sessionId,
                                      confirmation_id: ce.payload.confirmation_id,
                                      approved: true,
                                    });
                                  }}
                                  style={{
                                    background: "#16a34a",
                                    color: "#fff",
                                    border: "none",
                                    borderRadius: 6,
                                    padding: "4px 14px",
                                    fontSize: 12,
                                    cursor: "pointer",
                                  }}
                                >
                                  同意执行
                                </button>
                                <button
                                  onClick={() => {
                                    sendWs({
                                      type: "tool_confirmation",
                                      session_id: realSessionId ?? sessionId,
                                      confirmation_id: ce.payload.confirmation_id,
                                      approved: false,
                                    });
                                  }}
                                  style={{
                                    background: "#dc2626",
                                    color: "#fff",
                                    border: "none",
                                    borderRadius: 6,
                                    padding: "4px 14px",
                                    fontSize: 12,
                                    cursor: "pointer",
                                  }}
                                >
                                  拒绝
                                </button>
                                {/* 阶段三批次4(B-14): 稍后决定(挂起确认, 不立即拒绝) */}
                                <button
                                  onClick={() => {
                                    sendWs({
                                      type: "approval_defer",
                                      session_id: realSessionId ?? sessionId,
                                      confirmation_id: ce.payload.confirmation_id,
                                    });
                                  }}
                                  title="60 秒后不自动拒绝, 挂起等待后续决定"
                                  style={{
                                    background: "#6d28d9",
                                    color: "#fff",
                                    border: "none",
                                    borderRadius: 6,
                                    padding: "4px 14px",
                                    fontSize: 12,
                                    cursor: "pointer",
                                  }}
                                >
                                  稍后决定
                                </button>
                              </div>
                            )}
                          </div>
                        );
                      })}

                    {finalText && !deletedTurns.has(turn) ? (
                      <div>
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
                        {/* V1.1-3.3 消息操作条(非生成中显示) */}
                        {!isGenerating && (
                          <div style={{ display: "flex", gap: 4, marginTop: 6, flexWrap: "wrap" }}>
                            <MsgActionBtn label="🔄 重生成" title="重新生成这条回复" onClick={() => regenerateTurn(turn)} />
                            <MsgActionBtn
                              label={starredTurns.has(turn) ? "★ 已收藏" : "☆ 收藏"}
                              title="收藏/取消收藏"
                              onClick={() => void toggleStar(turn)}
                            />
                            <MsgActionBtn label="🗑 删除" title="软删除这条回复" danger onClick={() => void deleteTurnMessage(turn)} />
                            <MsgActionBtn label="📋 复制" title="复制回复内容" onClick={() => void copyText(finalText)} />
                            {finalText.includes("```") && (
                              <MsgActionBtn label="📄 复制代码" title="提取代码块并复制" onClick={() => void copyCodeBlocks(finalText)} />
                            )}
                          </div>
                        )}
                      </div>
                    ) : finalText && deletedTurns.has(turn) ? (
                      <div style={{ fontSize: 12, color: "#9ca3af", fontStyle: "italic" }}>
                        此回复已删除
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

      {/* 工作区条(画地为牢): 显示/修改会话工作目录, agent 操作范围告知层 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginTop: 10,
          fontSize: 12,
          color: "#6b7280",
        }}
      >
        {editingWorkspace ? (
          <>
            <input
              type="text"
              value={workspaceInput}
              onChange={(e) => setWorkspaceInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void saveWorkspace();
                }
              }}
              placeholder="输入工作区目录路径, 如 D:/MyProject"
              style={{
                flex: 1,
                padding: "6px 10px",
                borderRadius: 6,
                border: "1px solid #ccc",
                fontSize: 12,
                outline: "none",
              }}
            />
            <button
              onClick={() => void saveWorkspace()}
              style={{ padding: "6px 12px", borderRadius: 6, border: "none", background: "#1976d2", color: "#fff", fontSize: 12, cursor: "pointer" }}
            >
              保存
            </button>
            <button
              onClick={() => setEditingWorkspace(false)}
              style={{ padding: "6px 10px", borderRadius: 6, border: "1px solid #ddd", background: "#fff", color: "#666", fontSize: 12, cursor: "pointer" }}
            >
              取消
            </button>
          </>
        ) : (
          <>
            <span>📁 工作区:</span>
            <span
              style={{
                flex: 1,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                background: "#f3f4f6",
                borderRadius: 6,
                padding: "4px 8px",
              }}
              title={workspace ?? "（默认工作目录）"}
            >
              {workspace ?? "（默认工作目录）"}
            </span>
            <button
              onClick={() => {
                setWorkspaceInput(workspace ?? "");
                setEditingWorkspace(true);
              }}
              style={{ padding: "6px 10px", borderRadius: 6, border: "1px solid #ddd", background: "#fff", color: "#374151", fontSize: 12, cursor: "pointer" }}
            >
              更换
            </button>
          </>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        {/* 上传文档: 选择文件 → base64 上传后端 → 发送时附带文件路径 */}
        <input
          ref={fileInputRef}
          type="file"
          style={{ display: "none" }}
          onChange={(e) => void handleFilePick(e.target.files?.[0])}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          title="上传文档(≤15MB, 支持 pdf/docx/xlsx/txt/图片等)"
          style={{
            padding: "10px 12px", borderRadius: 6, border: "1px solid #ddd",
            backgroundColor: "#fff", fontSize: 14, cursor: "pointer",
          }}
        >
          📎
        </button>
        {/* V1.4-8.4 提示词模板库 */}
        <button
          onClick={() => setTemplatesOpen(!templatesOpen)}
          title="提示词模板库(保存常用提示词/插入)"
          style={{
            padding: "10px 12px", borderRadius: 6, border: "1px solid #ddd",
            backgroundColor: templatesOpen ? "#f5f3ff" : "#fff",
            color: templatesOpen ? "#5b21b6" : "#333",
            fontSize: 14, cursor: "pointer",
          }}
        >
          📋
        </button>
        {pendingUpload && (
          <span style={{ fontSize: 12, color: "#4caf50", alignSelf: "center", whiteSpace: "nowrap" }}>
            ✓ {pendingUpload.name}
          </span>
        )}
        {pendingImage && (
          <span
            style={{
              position: "relative",
              alignSelf: "center",
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <img
              src={pendingImage.preview}
              alt="待发送图片"
              style={{ height: 34, borderRadius: 6, border: "1px solid #ddd" }}
            />
            <button
              onClick={() => setPendingImage(null)}
              title="移除图片"
              style={{
                position: "absolute",
                top: -6,
                right: -6,
                width: 16,
                height: 16,
                borderRadius: "50%",
                border: "none",
                background: "#d32f2f",
                color: "#fff",
                fontSize: 10,
                lineHeight: 1,
                cursor: "pointer",
                padding: 0,
              }}
            >
              ×
            </button>
          </span>
        )}
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPaste={handlePaste}
          onKeyDown={(e) => {
            // V1.1-3.4: Enter 发送, Shift+Enter 换行(多行输入)
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              sendMessage();
            }
          }}
          placeholder="输入消息,Enter 发送,Shift+Enter 换行,可直接粘贴图片"
          rows={2}
          style={{
            flex: 1,
            padding: "10px 12px",
            borderRadius: 6,
            border: "1px solid #ddd",
            fontSize: 14,
            outline: "none",
            resize: "vertical",
            minHeight: 40,
            maxHeight: 160,
            fontFamily: "inherit",
            lineHeight: 1.5,
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
        {isGenerating && (
          <button
            onClick={stopGeneration}
            title="停止生成"
            style={{
              padding: "10px 20px", borderRadius: 6, border: "none",
              backgroundColor: "#d32f2f", color: "#fff", fontSize: 14, cursor: "pointer",
            }}
          >
            ⏹ 停止
          </button>
        )}
          </div>
          </div>
          )}
          {view === "settings" && <SettingsView sessionId={realSessionId ?? sessionId} />}
          {view === "knowledge" && <KnowledgeView sessionId={realSessionId ?? sessionId} />}
          {view === "memory" && (
            <MemoryView
              sessionId={realSessionId ?? sessionId}
              // V1.5 规划项-8: 记忆来源跳转(切换会话, 有 skill 直达对话视图)
              onOpenSession={(sid) => handleSwitchSession(sid)}
            />
          )}
          {view === "agents" && <AgentLibraryView onActivate={(skill) => void handlePickMode(skill)} />}
        </main>

        {/* V1.1-3.5 会话设置弹窗(记忆开关/上下文截断/系统提示词) */}
        {settingsOpen && (
          <div
            style={{
              position: "fixed",
              inset: 0,
              zIndex: 100,
              background: "rgba(15,23,42,0.4)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
            onClick={() => setSettingsOpen(false)}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                width: 560,
                maxWidth: "90vw",
                maxHeight: "80vh",
                overflowY: "auto",
                background: "#fff",
                borderRadius: 14,
                padding: "20px 24px",
                boxShadow: "0 20px 60px rgba(15,23,42,0.25)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
                <span style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>
                  ⚙ 会话设置
                </span>
                <button
                  onClick={() => setSettingsOpen(false)}
                  style={{ border: "none", background: "transparent", fontSize: 18, cursor: "pointer", color: "#64748b" }}
                >
                  ×
                </button>
              </div>

              {/* 记忆开关 */}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>会话记忆</div>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
                  <input type="checkbox" checked={memoryEnabled} onChange={() => void toggleMemory()} />
                  开启记忆(自动提取并注入长期记忆)
                </label>
              </div>

              {/* V1.3-7.2 自动执行 */}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>自动执行(工作流)</div>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={autoExec}
                    onChange={(e) => setAutoExec(e.target.checked)}
                  />
                  开启后,发一条消息自动连续执行多轮(无需逐条追问)
                </label>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
                  <span style={{ fontSize: 12, color: "#64748b" }}>最多</span>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={autoRounds}
                    onChange={(e) => setAutoRounds(Number(e.target.value))}
                    disabled={!autoExec}
                    style={{
                      width: 70, padding: "4px 8px", borderRadius: 6,
                      border: "1px solid #ddd", fontSize: 13,
                    }}
                  />
                  <span style={{ fontSize: 12, color: "#64748b" }}>轮</span>
                  <button
                    onClick={() => void saveAutoExec()}
                    style={{
                      fontSize: 12, padding: "5px 14px", borderRadius: 6,
                      border: "1px solid #6d28d9", background: "#f5f3ff",
                      color: "#5b21b6", cursor: "pointer",
                    }}
                  >
                    保存
                  </button>
                </div>
                <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 6 }}>
                  每轮模型自动继续(直到任务完成或达到轮次上限); 可在任意时刻点"停止"
                </div>
              </div>

              {/* 上下文截断 */}
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>上下文截断</div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span style={{ fontSize: 12, color: "#64748b" }}>保留 0 ~</span>
                  <input
                    type="number"
                    min={0}
                    value={truncateTurn}
                    onChange={(e) => setTruncateTurn(Number(e.target.value))}
                    style={{
                      width: 80, padding: "4px 8px", borderRadius: 6,
                      border: "1px solid #ddd", fontSize: 13,
                    }}
                  />
                  <span style={{ fontSize: 12, color: "#64748b" }}>轮</span>
                  <button
                    onClick={() => void doTruncate()}
                    style={{
                      fontSize: 12, padding: "5px 14px", borderRadius: 6,
                      border: "1px solid #d97706", background: "#fffbeb",
                      color: "#92400e", cursor: "pointer",
                    }}
                  >
                    截断
                  </button>
                </div>
                <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 6 }}>
                  之后的对话将移出模型上下文(软删除, 可从会话导出追溯)
                </div>
              </div>

              {/* 系统提示词 */}
              <div style={{ marginBottom: 8 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>系统提示词</div>
                <button
                  onClick={() => void loadSystemPrompt()}
                  style={{
                    fontSize: 12, padding: "5px 14px", borderRadius: 6,
                    border: "1px solid #ddd", background: "#f8fafc",
                    color: "#334155", cursor: "pointer", marginBottom: 8,
                  }}
                >
                  {systemPromptText === null ? "查看完整系统提示词" : "重新加载"}
                </button>
                {systemPromptText !== null && (
                  <textarea
                    readOnly
                    value={systemPromptText}
                    style={{
                      width: "100%", boxSizing: "border-box", minHeight: 180,
                      resize: "vertical", fontSize: 12, fontFamily: "Consolas, monospace",
                      border: "1px solid #e2e8f0", borderRadius: 8, padding: 10,
                      color: "#334155", background: "#f8fafc",
                    }}
                  />
                )}
              </div>

              {settingsMsg && (
                <div style={{ fontSize: 12, color: "#059669", marginTop: 8 }}>{settingsMsg}</div>
              )}
            </div>
          </div>
        )}

        {/* V1.1-3.8 任务状态抽屉 */}
        {/* V1.4-8.4 提示词模板库弹窗 */}
        {templatesOpen && (
          <div
            style={{
              position: "fixed",
              inset: 0,
              zIndex: 110,
              background: "rgba(15,23,42,0.4)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
            onClick={() => setTemplatesOpen(false)}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                width: 440,
                maxWidth: "90vw",
                maxHeight: "70vh",
                overflowY: "auto",
                background: "#fff",
                borderRadius: 14,
                padding: "18px 22px",
                boxShadow: "0 20px 60px rgba(15,23,42,0.25)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                <span style={{ fontSize: 15, fontWeight: 700, color: "var(--text-primary)" }}>📋 提示词模板</span>
                <button
                  onClick={() => setTemplatesOpen(false)}
                  style={{ border: "none", background: "transparent", fontSize: 18, cursor: "pointer", color: "#64748b" }}
                >
                  ×
                </button>
              </div>

              {tplMsg && (
                <div style={{ fontSize: 12, color: tplMsg.startsWith("已保存") ? "#059669" : "#dc2626", marginBottom: 8 }}>
                  {tplMsg}
                </div>
              )}

              {/* 保存当前输入为模板 */}
              <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                <input
                  value={tplName}
                  onChange={(e) => setTplName(e.target.value)}
                  placeholder="模板名(默认取输入前 20 字)"
                  style={{
                    flex: 1, padding: "6px 10px", borderRadius: 6,
                    border: "1px solid #ddd", fontSize: 12,
                  }}
                />
                <button
                  onClick={saveCurrentAsTemplate}
                  style={{
                    fontSize: 12, padding: "6px 14px", borderRadius: 6,
                    border: "1px solid #6d28d9", background: "#f5f3ff",
                    color: "#5b21b6", cursor: "pointer", whiteSpace: "nowrap",
                  }}
                >
                  + 保存当前输入
                </button>
              </div>

              {/* 模板列表 */}
              {templates.length === 0 && (
                <div style={{ fontSize: 12, color: "#94a3b8", textAlign: "center", padding: "16px 0" }}>
                  暂无模板。在输入区写好内容后点"+ 保存当前输入"。
                </div>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {templates.map((t) => (
                  <div
                    key={t.name}
                    style={{
                      display: "flex", alignItems: "center", gap: 8,
                      padding: "8px 10px", borderRadius: 8,
                      background: "rgba(245,243,255,0.6)", fontSize: 12,
                    }}
                  >
                    <button
                      onClick={() => insertTemplate(t.content)}
                      title="插入到输入区"
                      style={{
                        flex: 1, textAlign: "left", border: "none",
                        background: "transparent", cursor: "pointer",
                        color: "var(--text-primary)", fontSize: 12,
                      }}
                    >
                      <div style={{ fontWeight: 600 }}>{t.name}</div>
                      <div style={{ fontSize: 10, color: "#94a3b8", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {t.content}
                      </div>
                    </button>
                    <button
                      onClick={() => removeTemplate(t.name)}
                      title="删除模板"
                      style={{ border: "none", background: "transparent", color: "#ef4444", cursor: "pointer", fontSize: 13 }}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {tasksOpen && (
          <div
            style={{
              position: "fixed",
              inset: 0,
              zIndex: 100,
              background: "rgba(15,23,42,0.4)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
            onClick={() => setTasksOpen(false)}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                width: 620,
                maxWidth: "92vw",
                maxHeight: "80vh",
                overflowY: "auto",
                background: "#fff",
                borderRadius: 14,
                padding: "20px 24px",
                boxShadow: "0 20px 60px rgba(15,23,42,0.25)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
                <span style={{ fontSize: 16, fontWeight: 700 }}>
                  📋 任务执行状态
                  {tasksData && (
                    <span
                      style={{
                        marginLeft: 10,
                        fontSize: 11,
                        padding: "2px 10px",
                        borderRadius: 10,
                        background:
                          tasksData.status === "active"
                            ? "rgba(16,185,129,0.12)"
                            : tasksData.status === "error"
                              ? "rgba(220,38,38,0.12)"
                              : "rgba(245,158,11,0.12)",
                        color:
                          tasksData.status === "active"
                            ? "#059669"
                            : tasksData.status === "error"
                              ? "#dc2626"
                              : "#d97706",
                      }}
                    >
                      {tasksData.status === "active" ? "进行中" : tasksData.status === "error" ? "出错" : tasksData.status}
                    </span>
                  )}
                </span>
                <button
                  onClick={() => setTasksOpen(false)}
                  style={{ border: "none", background: "transparent", fontSize: 18, cursor: "pointer", color: "#64748b" }}
                >
                  ×
                </button>
              </div>

              <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                <button
                  onClick={() => void loadTasks()}
                  style={taskBtnStyle}
                  disabled={tasksLoading}
                >
                  {tasksLoading ? "…" : "刷新"}
                </button>
                {/* V1.2-6.2: 工具事件日志时间线 */}
                <button
                  onClick={() => {
                    setEventsOpen(!eventsOpen);
                    if (!eventsLog && !eventsOpen) void loadEvents();
                  }}
                  style={{ ...taskBtnStyle, color: eventsOpen ? "#6d28d9" : "#334155" }}
                >
                  📜 事件日志
                </button>
                {/* V1.2-6.3: 用量统计 + 错误摘要 */}
                <button
                  onClick={() => {
                    setDiagOpen(!diagOpen);
                    if (!usageData && !errorsData && !diagOpen) void loadDiagnostics();
                  }}
                  style={{ ...taskBtnStyle, color: diagOpen ? "#6d28d9" : "#334155" }}
                >
                  📊 用量/错误
                </button>
                {isGenerating && (
                  <button
                    onClick={stopGeneration}
                    style={{ ...taskBtnStyle, color: "#dc2626", borderColor: "#fca5a5" }}
                  >
                    ⏹ 终止当前生成
                  </button>
                )}
              </div>

              {/* V1.2-6.2: 事件时间线(倒序, 最新在上) */}
              {eventsOpen && (
                <div
                  style={{
                    maxHeight: 240,
                    overflowY: "auto",
                    border: "1px solid #e2e8f0",
                    borderRadius: 10,
                    padding: 8,
                    marginBottom: 12,
                    background: "#f8fafc",
                  }}
                >
                  {eventsLoading && (
                    <div style={{ fontSize: 12, color: "#94a3b8", padding: 8 }}>加载中…</div>
                  )}
                  {!eventsLoading && (!eventsLog || eventsLog.length === 0) && (
                    <div style={{ fontSize: 12, color: "#94a3b8", padding: 8 }}>暂无事件记录</div>
                  )}
                  {(eventsLog ?? []).map((ev) => (
                    <div
                      key={ev.id}
                      style={{
                        display: "flex",
                        gap: 8,
                        alignItems: "baseline",
                        padding: "3px 4px",
                        fontSize: 11,
                        borderBottom: "1px solid rgba(148,163,184,0.12)",
                      }}
                    >
                      <span
                        style={{
                          flexShrink: 0,
                          width: 64,
                          color: "#94a3b8",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {ev.ts ? new Date(ev.ts).toLocaleTimeString() : "--:--:--"}
                      </span>
                      <span
                        style={{
                          flexShrink: 0,
                          fontWeight: 600,
                          color:
                            ev.event_type === "error" || ev.event_type === "tool_error"
                              ? "#dc2626"
                              : ev.event_type === "tool_call"
                                ? "#6d28d9"
                                : ev.event_type === "final"
                                  ? "#059669"
                                  : "#64748b",
                        }}
                      >
                        #{ev.turn} {ev.event_type}
                      </span>
                      <span
                        style={{
                          flex: 1,
                          minWidth: 0,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          color: "#475569",
                        }}
                        title={ev.summary}
                      >
                        {ev.summary}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* V1.2-6.3: 用量统计 + 错误摘要 */}
              {diagOpen && (
                <div
                  style={{
                    maxHeight: 240,
                    overflowY: "auto",
                    border: "1px solid #e2e8f0",
                    borderRadius: 10,
                    padding: 12,
                    marginBottom: 12,
                    background: "#f8fafc",
                    display: "flex",
                    flexDirection: "column",
                    gap: 10,
                  }}
                >
                  {diagBusy && (
                    <div style={{ fontSize: 12, color: "#94a3b8" }}>加载中…</div>
                  )}
                  {usageData && (
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>
                        📊 LLM 用量
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
                        {[
                          { label: "调用次数", value: usageData.total_calls },
                          { label: "总 Token", value: usageData.total_tokens.toLocaleString() },
                          {
                            label: `成本(${usageData.currency})`,
                            value: usageData.total_cost.toFixed(4),
                          },
                        ].map((s) => (
                          <div key={s.label} style={{ background: "#fff", borderRadius: 8, padding: "8px 10px" }}>
                            <div style={{ fontSize: 10, color: "#94a3b8" }}>{s.label}</div>
                            <div style={{ fontSize: 14, fontWeight: 700, color: "#334155" }}>{s.value}</div>
                          </div>
                        ))}
                      </div>
                      <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 4 }}>
                        输入 {usageData.input_tokens.toLocaleString()} · 输出 {usageData.output_tokens.toLocaleString()}
                      </div>
                    </div>
                  )}
                  {errorsData && (
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>
                        ⚠️ 错误摘要({errorsData.total_errors} 次 / {errorsData.distinct_errors} 类)
                      </div>
                      {(errorsData.top ?? []).map((t) => (
                        <div
                          key={t.message}
                          style={{
                            display: "flex",
                            gap: 8,
                            fontSize: 11,
                            color: "#dc2626",
                            padding: "3px 0",
                            borderBottom: "1px solid rgba(220,38,38,0.1)",
                          }}
                        >
                          <span style={{ flexShrink: 0, fontWeight: 700 }}>×{t.count}</span>
                          <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={t.message}>
                            {t.message}
                          </span>
                        </div>
                      ))}
                      {(errorsData.top ?? []).length === 0 && (
                        <div style={{ fontSize: 11, color: "#94a3b8" }}>暂无错误记录 🎉</div>
                      )}
                    </div>
                  )}
                  {!diagBusy && !usageData && !errorsData && (
                    <div style={{ fontSize: 12, color: "#94a3b8" }}>暂无诊断数据</div>
                  )}
                </div>
              )}

              {!tasksData && !tasksLoading && (
                <div style={{ fontSize: 12, color: "#94a3b8", padding: "20px 0", textAlign: "center" }}>
                  暂无任务数据
                </div>
              )}

              {tasksData && tasksData.total_turns === 0 && (
                <div style={{ fontSize: 12, color: "#94a3b8", padding: "20px 0", textAlign: "center" }}>
                  还没有执行轮次
                </div>
              )}

              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {(tasksData?.turns ?? []).map((t) => {
                  const toolCalls = t.events.tool_call ?? 0;
                  const hasError = !!t.error;
                  return (
                    <div
                      key={t.turn}
                      style={{
                        border: "1px solid #e2e8f0",
                        borderRadius: 10,
                        padding: "10px 14px",
                        background: hasError ? "#fff7f7" : "#fafafa",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <span style={{ fontSize: 13, fontWeight: 700 }}>第 {t.turn} 轮</span>
                        {toolCalls > 0 && (
                          <span style={{ fontSize: 11, color: "#64748b" }}>
                            🔧 工具调用 {toolCalls} 次
                          </span>
                        )}
                        {(t.events.thinking ?? 0) > 0 && (
                          <span style={{ fontSize: 11, color: "#64748b" }}>
                            💭 推理 {(t.events.thinking ?? 0)} 段
                          </span>
                        )}
                        {(t.events.tool_result ?? 0) > 0 && (
                          <span style={{ fontSize: 11, color: "#64748b" }}>
                            ✅ 结果 {(t.events.tool_result ?? 0)} 个
                          </span>
                        )}
                        {t.last_ts && (
                          <span style={{ fontSize: 10, color: "#94a3b8", marginLeft: "auto" }}>
                            {new Date(t.last_ts).toLocaleTimeString()}
                          </span>
                        )}
                      </div>
                      {hasError && (
                        <div style={{ fontSize: 12, color: "#dc2626", marginTop: 6, wordBreak: "break-all" }}>
                          ❌ {t.error}
                        </div>
                      )}
                      {!isGenerating && (
                        <button
                          onClick={() => {
                            setTasksOpen(false);
                            regenerateTurn(t.turn);
                          }}
                          style={{ ...taskBtnStyle, marginTop: 8 }}
                        >
                          🔄 重试此轮
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
        <ResizeHandle
          onDrag={(delta) => setPanelW((w) => clampPanel(w - delta))}
        />
        <ArtifactPanel
          open={artifactsOpen}
          artifacts={artifacts}
          onToggle={() => setArtifactsOpen(!artifactsOpen)}
          width={panelW}
        />

        {/* 对话中切换技能弹层 */}
        {skillPickerOpen && (
          <div
            style={{
              position: "fixed", inset: 0, background: "rgba(15,23,42,0.4)",
              display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
            }}
            onClick={() => setSkillPickerOpen(false)}
          >
            <div
              className="glass-panel"
              style={{ padding: 20, width: 420, maxHeight: "70vh", overflowY: "auto" }}
              onClick={(e) => e.stopPropagation()}
            >
              <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 4 }}>选择技能</div>
              <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 12 }}>
                切换技能将新建会话(当前会话保持原技能)
              </div>
              {availableSkills.filter((s) => s.enabled).map((s) => (
                <button
                  key={s.name}
                  onClick={() => {
                    setSkillPickerOpen(false);
                    void handlePickMode(s.name as "office" | "data_analysis" | "frontend_design");
                  }}
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    width: "100%", padding: "10px 14px", marginBottom: 8,
                    borderRadius: 8, border: "1px solid #ddd", background: "#fff",
                    fontSize: 14, cursor: "pointer", textAlign: "left",
                  }}
                >
                  <span>{s.name}</span>
                  <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>v{s.version}</span>
                </button>
              ))}
              {availableSkills.filter((s) => s.enabled).length === 0 && (
                <div style={{ fontSize: 13, color: "var(--text-tertiary)", padding: 16, textAlign: "center" }}>
                  暂无可用技能
                </div>
              )}
              <button
                onClick={() => setSkillPickerOpen(false)}
                style={{ width: "100%", padding: "8px", marginTop: 6, borderRadius: 6, border: "none", background: "#eee", cursor: "pointer", fontSize: 13 }}
              >
                取消
              </button>
            </div>
          </div>
        )}
        </div>
      </div>
    </div>
  );
}
