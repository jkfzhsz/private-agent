// M1 Phase 5 - React chat UI 根组件 (蓝图 §2.15 + §9.4 AC-8)
//
// 功能:
// - 消息输入框 + 发送按钮
// - 流式渲染区域:按 event_type 分块(thinking/tool_call/tool_result/final/error)
// - WS 连接状态指示器(connected/disconnected/reconnecting)
// - 重连机制:指数退避(1s,2s,4s,8s,max 16s),重连后发送 replay(session_id + last_turn)
// - ACK 机制:收到 react_event 后发送 ack(session_id + turn)
// - session_id 管理:首次连接时从 URL 参数获取或生成
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import LiquidBackground from "./components/LiquidBackground";
import Sidebar, { type ViewKey, SIDEBAR_EXPANDED_WIDTH } from "./components/Sidebar";
import ArtifactPanel, { type Artifact, PANEL_EXPANDED_WIDTH } from "./components/ArtifactPanel";
// P3-3(2026-08-17): 视图代码分割 —— 按需加载(首屏只载 HomeView 依赖, 其余懒加载)
import RobotAvatar from "./components/RobotAvatar";
import ConfirmDialog from "./components/ConfirmDialog";
import { ToastHost, toast } from "./components/Toast";
import { deAIfy } from "./utils/deAIfy";
import { renderFinalText } from "./utils/renderFinal";
import { ICON_THINKING, ICON_TASKS } from "./utils/icons";
// P3-2 批次1(2026-08-17): 工具函数与小组件自 chatUi.tsx 导入(自本文件拆出)
import {
  ThinkingWait,
  MsgActionBtn,
  formatPayload,
  errorCategoryColor,
  sceneDisplayName,
  extractImagePaths,
  imagePathToUrl,
  getSessionIdFromUrl,
  notifyUser,
  SCENE_NAME_MAP,
} from "./utils/chatUi";
import "./styles/design-tokens.css";

import { adminFetch } from "./utils/apiClient";

// P3-3(2026-08-17): 视图懒加载(替换静态 import)—— 首屏包体显著减小,
// 切视图时才拉取对应 chunk
const HomeView = lazy(() => import("./views/HomeView"));
const KnowledgeView = lazy(() => import("./views/KnowledgeView"));
const MemoryView = lazy(() => import("./views/MemoryView"));
const SettingsView = lazy(() => import("./views/SettingsView"));
const AgentLibraryView = lazy(() => import("./views/AgentLibraryView"));

// V1.5 项-1(ADR-012 §3.4 M3): 子任务卡片面板(WS 即时刷新 + DB 轮询兜底)
import SubagentPanel, {
  createSubagent,
  type SubagentState,
} from "./components/SubagentPanel";
import { TurnCard, type TurnGroupData, type ReactEvent, type EventType } from "./components/TurnCard";


// ──────────────────────────────────────────────────────────────────────────────
// 类型定义
// ──────────────────────────────────────────────────────────────────────────────

type ConnStatus = "connected" | "disconnected" | "reconnecting";

// 2026-08-12 perf: 预计算的 turn 分组数据 —— render 阶段从 O(turn_events × 11)
// 次遍历降为 O(1) 解构。delta/thinking 累积更新时 turnGroups useMemo 重算
// 一次(O(n) 全表遍历), 所有 turn 卡片共享预计算结果, 避免每卡片重复 find/filter。
interface WSMessage {
  type: string;
  session_id?: number;
  turn?: number;
  event_type?: EventType;
  payload?: Record<string, unknown>;
  count?: number;
  effective_offset?: number;
  message?: string;
  // 2026-08-10 22:00: 后端 replay 重放的历史事件带此标记, 前端据此跳过
  // 确认弹窗等实时副作用(历史工具调用已执行完毕, 不应再次触发权限确认)
  replayed?: boolean;
  // 0.5.1 A-1(C-4 事件级去重): 后端 react_events 自增 id, 实时推送与
  // replay 重放同源。前端据此去重(修复: turn N 中途断线重连, 该轮事件
  // 全量重放导致 delta 重复累积 / 事件重复渲染)
  event_id?: number;
  // V1.5 项-1(M3): 子代理事件(后端 delegate_subtask / runner 推送)
  subagent_id?: number;
  task_id?: string;
  prompt?: string;
  status?: string;
  result?: string;
  error?: string;
  phase?: string;
  // 2026-08-19(A 方案): turn_end 携带的服务端权威耗时(ms, 后端 monotonic
  // 计时)。前端优先用此值显示"总耗时", 回退 Date.now() 差值。
  duration_ms?: number;
  // 2026-08-19(断点恢复反馈): turn_resumed 携带的最新 checkpoint 轮
  // (后端断点恢复成功时补发, 区别于流程级"继续")。
  checkpoint_turn?: number;
}

// ──────────────────────────────────────────────────────────────────────────────
// 常量
// ──────────────────────────────────────────────────────────────────────────────

const WS_URL = "ws://localhost:8765/ws";
const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000];
const MAX_RECONNECT_DELAY = 16000;

// P0-2(2026-08-17): 事件底色改引 var(--event-*) token(design-tokens.css 亮/暗双值)
const EVENT_STYLES: Record<EventType, { bg: string; label: string; icon: string }> = {
  user: { bg: "var(--event-user-bg)", label: "You", icon: "🧑" },
  thinking: { bg: "var(--event-thinking-bg)", label: "Thinking", icon: "💭" },
  tool_call: { bg: "var(--event-tool_call-bg)", label: "Tool Call", icon: "🔧" },
  tool_result: { bg: "var(--event-tool_result-bg)", label: "Tool Result", icon: "✅" },
  delta: { bg: "var(--event-delta-bg)", label: "Streaming", icon: "…" },
  final: { bg: "var(--event-final-bg)", label: "Final", icon: "🎯" },
  error: { bg: "var(--event-error-bg)", label: "Error", icon: "❌" },
  sandbox_output: { bg: "var(--event-sandbox_output-bg)", label: "Sandbox", icon: "🖥️" },
  tool_confirmation_required: { bg: "var(--event-tool_confirmation_required-bg)", label: "Permission", icon: "⚠️" },
  tool_confirmation_result: { bg: "var(--event-tool_confirmation_result-bg)", label: "Permission Result", icon: "🔐" },
  // V1.5 项-5: 流程级暂停/继续(不入事件列表, 仅类型完整)
  turn_paused: { bg: "var(--event-turn_paused-bg)", label: "Paused", icon: "⏸" },
  turn_resumed: { bg: "var(--event-turn_resumed-bg)", label: "Resumed", icon: "▶" },
  // 2026-08-16(阶段2 反馈): 迭代上限询问(不入事件列表, 仅类型完整)
  iteration_limit_reached: { bg: "var(--event-iteration_limit_reached-bg)", label: "Iteration Limit", icon: "🔄" },
  // 2026-08-19(后端进程反馈): LLM 调用中/工具执行中状态事件(不入事件列表)
  status: { bg: "var(--event-status-bg, #f1f5f9)", label: "Status", icon: "⏳" },
};

// M3 AC-9: tool_result 中 outputs/*.png 等图片路径解析(蓝图 §7.12)
// 匹配 "outputs/foo.png" 或 "/outputs/foo-bar.jpg" 形式的路径
// 2026-08-10 22:00: [\w\-] → [\w\-\u4e00-\u9fff] 支持中文文件名(如 分析报告.png),
// 原实现 \w 不含中文, 中文名产物提取不到导致右栏产物区为空
const IMAGE_PATH_RE = /(?:^|[^\w/])((?:\/?outputs\/)?[\w\-\u4e00-\u9fff]+\.(?:png|jpg|jpeg|gif|svg|webp))/gi;

// 0.5.0 M1(2026-08-08): 场景技术标识 → 中文名显示映射(与后端 skill.yaml
// scene_name 同步; activeSkill 存技术标识, 显示层映射为子瞻/白圭/清和)。
const SCENE_ALLOWED_CATEGORIES: Record<string, string[]> = {
  office: ["documents", "writing"],
  data_analysis: ["documents"],
  frontend_design: ["design", "engineering"],
  monitor: ["engineering", "meta"],
};
// P3-2 批次1: 主组件专用常量(文件服务基址 + 任务抽屉按钮样式)
const FILES_BASE = "http://127.0.0.1:8765/files/outputs";
const taskBtnStyle: React.CSSProperties = {
  fontSize: 12,
  padding: "5px 14px",
  borderRadius: 10,
  border: "1px solid var(--border-strong)",
  background: "var(--panel-bg-solid)",
  color: "var(--text-primary)",
  cursor: "pointer",
};
// ──────────────────────────────────────────────────────────────────────────────
// 视图组件(评估已移除; 设置/知识库/记忆见 views/ 目录; 首页见 HomeView)
// ──────────────────────────────────────────────────────────────────────────────

// ──────────────────────────────────────────────────────────────────────────────
// 主组件
// ──────────────────────────────────────────────────────────────────────────────

export default function App(): JSX.Element {
  const [events, setEvents] = useState<ReactEvent[]>([]);
  const [input, setInput] = useState("");
  // 0.5.1 A(2026-08-09 蒋先生反馈): 权限确认全局弹窗 —— 独立置顶,
  // 任何窗口收到确认请求都弹出(不再只渲染在对话流内导致错过/超时)。
  // payload 来自 tool_confirmation_required 事件; 倒计时默认 60s(与后端一致)。
  const [pendingConfirm, setPendingConfirm] = useState<{
    confirmation_id: string;
    session_id: number;
    message: string;
    risk?: string;
    reason?: string;
    argsPreview?: string;
    // 2026-08-15: 后端人性化描述(title/summary 人话要点, 未登录老事件无此字段)
    display?: { title?: string; summary?: string[]; tool_label?: string };
  } | null>(null);
  // 确认弹窗倒计时(秒)。比后端超时(60s)提前 5s 关闭 —— 避免用户在
  // 超时边界点击"同意"时后端 confirmation_id 已过期 → unknown confirmation_id
  const [confirmCountdown, setConfirmCountdown] = useState<number>(55);
  // 2026-08-18(请求卡片点不了): 已过期/已处理的确认 ID 集合 —— 对应
  // TurnCard 内嵌卡片按钮禁用并显示"已过期", 防用户对失效确认重复点击
  // 报 unknown confirmation_id。标记时机: ① 点击同意/拒绝/稍后决定(立即
  // 本地标记防重复) ② 后端 confirmation error(带 id) ③ 弹窗 55s 倒计时
  // 关闭 ④ tool_confirmation_result 事件(已处理) ⑤ replay 加载的历史
  // 确认事件(历史工具调用早已结束, 确认不可能有效)。
  const [expiredConfirmIds, setExpiredConfirmIds] = useState<Set<string>>(
    () => new Set()
  );
  const markConfirmExpired = useCallback((id?: string | null): void => {
    if (!id) return;
    setExpiredConfirmIds((prev) => {
      if (prev.has(id)) return prev;
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  }, []);
  // 2026-08-19: 每轮 turn 完成时间(turn → 完成时刻 ms), TurnCard 渲染
  // "完成于 HH:MM:SS"; 长任务(全量回归)结束后用户可一眼看出耗时上下文
  const [turnEndTimes, setTurnEndTimes] = useState<Map<number, number>>(
    () => new Map()
  );
  // 2026-08-19: 每轮 turn 开始时间(首个事件到达时刻, 近似开始) ——
  // 与 turnEndTimes 差值即该轮总耗时(四场景通用, 显示于 TurnCard)
  const [turnStartTimes, setTurnStartTimes] = useState<Map<number, number>>(
    () => new Map()
  );
  // 2026-08-19(A 方案): 服务端权威耗时(turn_end 携带 duration_ms, 毫秒)。
  // 优先于前端 Date.now() 差值 —— 跨窗口 turn 号撞车 + prev.has 短路会
  // 污染 turnStartTimes, 前端差值不可靠; 后端 monotonic 计时与窗口/turn
  // 号/前端时钟完全解耦。
  const [turnDurations, setTurnDurations] = useState<Map<number, number>>(
    () => new Map()
  );
  // 2026-08-19(假思考检测): 最近一次收到 react_event 的时刻(ms)。
  // 供 ThinkingWait 判断"已等待 Xs 但期间无任何新事件"→ 提示模型响应
  // 可能缓慢/卡住(区别于正常思考: 思考会产生 thinking/delta 流式事件)。
  const [lastEventAt, setLastEventAt] = useState<number>(() => Date.now());
  useEffect(() => {
    if (!pendingConfirm) return;
    setConfirmCountdown(55);
    const iv = setInterval(() => {
      setConfirmCountdown((c) => {
        if (c <= 1) {
          clearInterval(iv);
          // 超时: 关闭弹窗(后端会返回 Tool confirmation timeout, 无需前端回传)
          // 2026-08-18: 同时标记该确认过期 —— 内嵌卡片按钮禁用,
          // 防用户在弹窗关闭后仍点击已失效确认
          markConfirmExpired(pendingConfirm.confirmation_id);
          setPendingConfirm(null);
          return 0;
        }
        return c - 1;
      });
    }, 1000);
    return () => clearInterval(iv);
  }, [pendingConfirm, markConfirmExpired]);
  // 阶段三批次3(T3.4): 编辑重发纠正沉淀 —— 记录被编辑的原消息(发送时提取 correction 记忆)
  const [editingOriginal, setEditingOriginal] = useState<string | null>(null);
  const [status, setStatus] = useState<ConnStatus>("disconnected");
  // P0-1(2026-08-17): 重连尝试序号(响应式, 供侧边栏"重连中(第 N 次)"展示)
  const [reconnectCount, setReconnectCount] = useState(0);
  // P0-3(2026-08-17): 玻璃确认弹层状态(替代 window.confirm; 异步确认, 确认后才执行动作)
  const [closeConfirmOpen, setCloseConfirmOpen] = useState(false);
  const [deleteTurnConfirm, setDeleteTurnConfirm] = useState<number | null>(null);
  const [truncateConfirm, setTruncateConfirm] = useState<number | null>(null);
  const [iterLimitConfirm, setIterLimitConfirm] = useState<{
    used: number;
    max: number;
    sid: number;
  } | null>(null);
  // P1-4(2026-08-17): 对话顶部工具条收纳 —— 「⋯ 更多」下拉(会话设置/任务/关闭对话)
  const [moreMenuOpen, setMoreMenuOpen] = useState(false);
  // 对话文档上传: 文件引用(上传成功后记录, 发送时附带路径)
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [pendingUpload, setPendingUpload] = useState<{ name: string; path: string } | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // 生成中状态(显示"停止"按钮)
  const [isGenerating, setIsGenerating] = useState(false);
  // 对话中切换技能弹层(2026-08-12 Phase 2: 多选挂载附加技能)
  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  // 2026-08-12 Phase 3: 对话中 / 召唤技能 —— 输入框斜杠浮层
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashQuery, setSlashQuery] = useState("");
  const [slashIndex, setSlashIndex] = useState(0);
  // 2026-08-12 Phase 2: 弹层内临时多选(打开时初始化为已挂载列表)
  const [pickerSelected, setPickerSelected] = useState<string[]>([]);
  // 2026-08-12 Phase 2: 会话附加技能(多技能调用) —— 已挂载技能名列表
  const [supplementarySkills, setSupplementarySkills] = useState<string[]>([]);
  const [availableSkills, setAvailableSkills] = useState<
    { name: string; version: string; enabled: boolean; description?: string; display_name?: string; model_scope?: string[]; scenario?: string }[]
  >([]);
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
  const toggleTheme = (): void => {
    const next = (theme === "dark" ? "light" : "dark") as "light" | "dark";
    // V1.4-8.4 主题切换淡入淡出: 用 View Transitions API(Electron 30 = Chromium 124+)
    // 对整个 root 做 cross-fade, CSS 变量瞬间变化配合截图交叉淡化, 平滑过渡所有
    // 元素(背景/文字/边框)。flushSync 强制 React 在 callback 内同步 flush DOM,
    // 否则 setState 异步更新导致 startViewTransition 截图前后相同 → 无动画。
    // 不支持时降级为瞬时切换(老浏览器/极端环境零回归)。
    const docAny = document as unknown as {
      startViewTransition?: (cb: () => void) => unknown;
    };
    if (typeof docAny.startViewTransition === "function") {
      docAny.startViewTransition(() => {
        flushSync(() => setTheme(next));
      });
    } else {
      setTheme(next);
    }
  };
  // V1.1-3.6 智能体显示名(首页问候卡/对话区/侧边栏展示, 可改名持久化)
  // 0.5.0 M1(2026-08-08 修复): 会话激活场景时, agentName 跟随场景智能体名
  // (子瞻/白圭/清和) 同步, 蒋先生反馈: 对话应展示场景智能体而非手动设置的
  // "无涯"等主智能体名。手动改名仅在无场景会话生效。
  // 0.5.0 P4: 默认值改为 "主智能体" —— 与监控 tab 标签 + 系统监控角色一致
  const [agentName, setAgentName] = useState<string>("主智能体");
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await adminFetch("http://127.0.0.1:8765/admin/agent-profile");
        if (resp.ok) {
          const data = await resp.json();
          if (!cancelled && typeof data.display_name === "string" && data.display_name.trim()) {
            setAgentName(data.display_name.trim());
          }
        }
      } catch {
        /* 读取失败用默认名 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  // 0.5.0 P4(2026-08-08 蒋先生二次反馈修复):
  // 删除"场景变化→agentName 同步"effect —— 该逻辑导致返回首页后
  // agentName 残留场景名(如"子瞻")。agentName 恒为主智能体名(用户配置);
  // 对话区助手名用 sceneDisplayName(activeSkill) 单独计算(见下方 renderChatAssistantName)。
  const renderChatAssistantName = (): string => {
    // 场景会话 → 显示场景智能体名; 监控/全局会话 → 显示主智能体名
    if (activeSlot !== 0 && activeSkill && SCENE_NAME_MAP[activeSkill]) {
      return SCENE_NAME_MAP[activeSkill];
    }
    return agentName || "主智能体";
  };
  const renameAgent = useCallback(async (name: string): Promise<void> => {
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      const resp = await adminFetch("http://127.0.0.1:8765/admin/agent-profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: trimmed }),
      });
      if (resp.ok) {
        setAgentName(trimmed);
        return;
      }
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.error ?? `HTTP ${resp.status}`);
    } catch (e) {
      throw new Error(`保存失败: ${String(e)}`);
    }
  }, []);
  // 每个 turn 的"推理过程"展开状态(默认收起)
  const [openThinkingTurns, setOpenThinkingTurns] = useState<Set<number>>(new Set());
  // 会话级模型选择: "auto"(fallback 链) 或 provider 名(手动锁定)
  const [sessionModel, setSessionModel] = useState<string>("auto");
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  // 工作区选择(画地为牢): 会话级工作目录
  const [workspace, setWorkspace] = useState<string | null>(null);
  const [workspaceInput, setWorkspaceInput] = useState("");
  // 2026-08-08: 工作区设置弹层(输入区左侧 📁 图标触发; 支持原生目录选择器)
  const [workspacePanelOpen, setWorkspacePanelOpen] = useState(false);

  // 按 turn 分组事件:同一轮对话合并为一个 AI 回复块
  // 2026-08-12 perf: turnGroups 预计算所有 render 阶段需要的派生字段
  // (user/thinking/final/error 事件 + toolEvents/confirmEvents 数组 +
  // sandboxText/deltaText/thinkingText/finalText 拼接文本)。原 render 阶段
  // 每个 turn 卡片做 5 次 find + 4 次 filter + 2 次 map+join = O(turn_events × 11),
  // delta 流式时所有 turn 重算 → 卡顿。预计算后 render 阶段 O(1) 解构。
  const turnGroups = useMemo(() => {
    const map = new Map<number, TurnGroupData>();
    for (const ev of events) {
      const t = ev.turn;
      let g = map.get(t);
      if (!g) {
        g = {
          toolEvents: [],
          confirmEvents: [],
          sandboxText: "",
          deltaText: "",
          finalText: "",
          thinkingText: "",
        };
        map.set(t, g);
      }
      switch (ev.event_type) {
        case "user":
          g.user = ev;
          break;
        case "thinking":
          // thinking 事件可能多条(理论上累积合并为一条, 防御性保留最后一条)
          g.thinking = ev;
          g.thinkingText = formatPayload("thinking", ev.payload);
          break;
        case "final":
          g.final = ev;
          g.finalText = formatPayload("final", ev.payload);
          break;
        case "error":
          g.error = ev;
          break;
        case "tool_call":
        case "tool_result":
          g.toolEvents.push(ev);
          break;
        case "sandbox_output":
          g.sandboxText += String(ev.payload.chunk ?? "");
          break;
        case "tool_confirmation_required":
          g.confirmEvents.push(ev);
          break;
        case "tool_confirmation_result":
          g.confirmResult = ev;
          break;
        case "delta":
          // delta 累积合并为一条事件(见 lastDeltaIdByTurnRef), 但防御性
          // 处理多条情况: 累加文本
          g.deltaText += formatPayload("delta", ev.payload);
          break;
        case "status":
          // 2026-08-19(后端进程反馈): LLM 调用中/工具执行中状态事件
          // (persist=False 不入库, 工具心跳每 10s 覆盖 → 展示实时耗时)。
          g.status = ev;
          break;
      }
    }
    // finalText 回退到 deltaText(无 final 时显示流式增量)
    for (const g of map.values()) {
      if (!g.finalText) g.finalText = g.deltaText;
    }
    return Array.from(map.entries()).sort((a, b) => a[0] - b[0]);
  }, [events]);

  // 0.5.1(2026-08-10 蒋先生反馈): 对话自动滚动 —— 消息/流式内容变化时
  // 滚动到底部, 避免用户提问后需手动下拉。
  // 2026-08-16(蒋先生反馈): 修复"锁定在最后一句" —— 原实现无条件
  // scrollTop=scrollHeight, 流式更新时把用户正在翻看的历史强制拉回底部。
  // 优化: 滚动位置感知 —— 用户主动上翻(离开底部)时暂停自动跟随, 回到
  // 底部附近(120px 内)自动恢复; 仅"新回合开始/结束生成"时强制回底部。
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef<boolean>(true);

  // 监听滚动: 用户主动上翻 → stickToBottom=false; 回到底部附近 → true
  const handleChatScroll = useCallback((): void => {
    const el = chatScrollRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = dist < 120;
  }, []);

  // 新回合开始(isGenerating 由 false→true)时强制回底部(看新消息起点)
  const prevGeneratingRef = useRef<boolean>(false);
  useEffect(() => {
    const el = chatScrollRef.current;
    if (!el) return;
    const started = isGenerating && !prevGeneratingRef.current;
    prevGeneratingRef.current = isGenerating;
    if (started) {
      stickToBottomRef.current = true;
      el.scrollTop = el.scrollHeight;
      return;
    }
    // 流式内容变化: 仅当用户停留在底部(或未主动上翻)时才跟随
    if (stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [turnGroups, isGenerating, view]);

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

  // 右栏产物: 从 final 事件提取图片 + 文件(去重)
  // 2026-08-11 用户反馈: 过程中的中间产物过多, 改为只提取 final 轮最终回答
  // 中提及的文件/图片, 过渡性产物不再显示。
  // 2026-08-10 22:00: fileRe 支持中文文件名([\w\-\u4e00-\u9fff] 替代 \w)
  // 2026-08-12 perf: 依赖 finalCount 而非 events —— delta/thinking 流式增量
  // 不产生新产物文件, 无需触发 regex 全表扫描。finalCount 是数字, delta
  // 不改变其值 → useMemo 跳过重算。
  const finalCount = useMemo(
    () => events.reduce((n, e) => n + (e.event_type === "final" ? 1 : 0), 0),
    [events]
  );
  const artifacts = useMemo<Artifact[]>(() => {
    const list: Artifact[] = [];
    const fileRe =
      /(?:\/?outputs\/)?[\w\-\u4e00-\u9fff]+\.(?:xlsx|docx|csv|html|md|pdf|json|txt|pptx|zip)/gi;
    for (const ev of events) {
      if (ev.event_type !== "final") continue;
      const text = formatPayload("final", ev.payload);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finalCount]);

  const [artifactsOpen, setArtifactsOpen] = useState(true);

  // 2026-08-10: 左右侧边栏改为固定宽度两态(展开/折叠), 移除拖拽调宽
  // 展开宽度用固定常量(Sidebar 220 / ArtifactPanel 300), 折叠均为 44px,
  // 点击各自收起按钮折叠/恢复; 不再允许自由拖拽导致布局失衡。

  // HomeView 模式按钮: 激活 skill + 切换到对话视图
  // 会话锁定(AC-4): 同一 session 激活后不允许切换 skill(409),
  // 因此切换到不同模式时必须新建会话(随机 session_id, 后端懒创建)
  // 2026-08-07 修复: 首页(view=home)点模式必须新建会话 —— 原逻辑在
  // activeSkill=null(如从"无 skill"历史会话切回首页)时复用 realSessionId,
  // 导致"新对话走进旧会话" / 对已锁定其他 skill 的历史会话报 409
  const handlePickMode = async (skill: string): Promise<void> => {
    // 0.5.0 P2: 激活新技能前保存当前窗口快照
    saveWindowSnapshot();
    // 0.5.0 P5(2026-08-08): 场景技能 → slot 映射; 若该场景已有未关闭对话
    // (windowCacheRef 快照), 点击图标=恢复该对话(无缝切换), 否则新建。
    const slotMap: Record<string, number> = {
      office: 1,
      data_analysis: 2,
      frontend_design: 3,
    };
    const targetSlot = slotMap[skill] ?? 1;
    const existing = windowCacheRef.current[targetSlot];
    // 恢复校验: 快照 sessionId 必须是真实会话(>0)且属于该场景
    // (saveWindowSnapshot 会把当前 activeSlot 的 home 态写入快照, 需排除)
    if (
      existing &&
      existing.sessionId &&
      existing.sessionId > 0 &&
      existing.skill === skill
    ) {
      // 恢复既有场景对话(不重建会话): 与 switchWindow 恢复分支一致
      setEvents(existing.events);
      setInput(existing.input);
      setActiveSkill(existing.skill ?? skill);
      setSessionModel(existing.model);
      lastTurnRef.current = existing.lastTurn;
      // 0.5.0 P6(2026-08-09): 恢复快照已有本地 events → 增量续传(不全量重放)
      fullReloadRef.current = false;
      // 同步 ref(不等 effect): 渲染提交前旧窗口事件不被误收
      sessionIdRef.current = existing.sessionId;
      realSessionIdRef.current = existing.sessionId;
      activeSlotRef.current = targetSlot;
      setRealSessionId(existing.sessionId);
      setSessionId(existing.sessionId);
      setActiveSlot(targetSlot);
      setView(existing.skill ? "chat" : "home");
      bumpSlots();
      return;
    }
    const onHome = view === "home";
    const needNewSession =
      onHome || (activeSkill !== null && activeSkill !== skill);
    const sid =
      needNewSession
        ? Math.floor(Math.random() * 100000) + 1
        : realSessionId ?? sessionId;
    if (needNewSession) {
      // 新会话: 触发 ws replay + 清空当前对话(后端对新 session 懒创建行)
      setSessionId(sid);
      setRealSessionId(null);
      // 0.5.0 P6(2026-08-09): 同步 ref —— 新会话 sid 未知, 置 0 表示"未确认",
      // 事件过滤接受懒创建回传; 窗口快照尚未挂载, 其他窗口事件不误收
      sessionIdRef.current = sid;
      realSessionIdRef.current = null;
      activeSlotRef.current = targetSlot;
      setEvents([]);
      // 0.5.0 P4(2026-08-08): tab 条已删, 场景切换走 handlePickMode ——
      // 新会话必须清空输入框(否则上一场景草稿残留到新场景)
      setInput("");
      // 0.5.0 P6(2026-08-09): 单 WS 复用下 replay 用 lastTurnRef —— 新会话
      // 必须归零, 否则残留旧会话 turn 值导致新会话 replay 从错误位置续传
      lastTurnRef.current = 0;
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
      setActiveSlot(targetSlot);
      // 新建会话 → 挂载快照(绿点)
      windowCacheRef.current[targetSlot] = {
        sessionId: sid,
        skill,
        events: [],
        input: "",
        model: "auto",
        lastTurn: 0,
      };
      bumpSlots();
      setView("chat");
    } catch (e) {
      // eslint-disable-next-line no-alert
      toast.error(`激活 ${skill} 失败: ${String(e)}`);
    }
  };

  // 任务树: 切换到历史会话 → 改 sessionId, 触发 connect 重连 + 后端 replay
  const handleSwitchSession = (
    id: number,
    skillName?: string | null,
    modelId?: string | null,
    kind?: string | null
  ): void => {
    if (id === sessionId && view === "chat") return;
    // 0.5.0 P2: 切换会话前保存当前窗口快照(输入框/事件流不丢失)
    saveWindowSnapshot();
    // 0.5.0 P6(2026-08-09): 不再手动 close WS —— 单 WS 复用, sessionId 变化后
    // 由下方 replay effect 通过既有连接发送 replay 切换会话(原实现 close+重连,
    // 重连期间 status!=="connected" 导致切换后输入框短时间无法发送)。
    setIsPaused(false); // V1.5 项-5: 切换会话清除暂停态
    // 0.5.0 M1(2026-08-08 修复): 切换会话重置生成态, 避免 A 会话 isGenerating=true
    // 残留到 B 会话导致 UI 一直显示"停止"按钮但无实际生成 → 用户感知"无输出"
    setIsGenerating(false);
    setEvents([]);
    setActiveSkill(skillName ?? null);
    lastTurnRef.current = 0;
    // 2026-08-19: 切换会话清空耗时记录(turn 号跨会话会重复, 防污染)
    setTurnStartTimes(new Map());
    setTurnEndTimes(new Map());
    setTurnDurations(new Map());
    // 切换会话重置重连计数, 让 ws onopen 后正常连接
    reconnectAttemptRef.current = 0;
    // V1.5 项-1(M3 R7): 切换会话清空子代理卡片, 由重连后 DB 轮询重建
    setSubagents({});
    // 切换历史会话: 全量加载(忽略服务端 ws_offset, 否则 offset=1 会跳过第 1 轮)
    fullReloadRef.current = true;
    setSessionModel(modelId ?? "auto");
    // 0.5.0 P6(2026-08-09): 同步 ref(不等 effect) —— 切换瞬间旧窗口事件不误收
    sessionIdRef.current = id;
    realSessionIdRef.current = id;
    setRealSessionId(id);
    setSessionId(id);
    // V1.5 项-4: 查询断点恢复信息(interrupted 会话显示"断点继续"横幅)
    setResumeInfo(null);
    void adminFetch(`http://127.0.0.1:8765/admin/sessions/${id}/resume`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data && typeof data.resumable === "boolean") {
          setResumeInfo(data);
        }
      })
      .catch(() => {
        /* 查询失败静默 */
      });
    // 2026-08-16(蒋先生反馈): 进入对话视图 —— 场景会话(skillName 非空)
    // 与主智能体(monitor, locked_skill_name 恒 NULL)都进 chat; 仅无 skill
    // 的通用会话回首页选模式(修复: 无涯会话恢复后跳主页 + AI 从头思考)。
    const isMonitor = kind === "monitor";
    // 2026-08-18(蒋先生反馈): 侧边栏点历史会话进入时同步 activeSlot ——
    // 原实现不设置, 从场景会话切回无涯历史会话时 activeSlot 仍停留在原
    // slot(≠0), 导致优化审批面板(activeSlot===0 条件)不渲染"审批栏消失"。
    // monitor → slot 0; 场景按技术标识映射 1/2/3(与 HomeView slotHasSession 一致)
    if (isMonitor) {
      activeSlotRef.current = 0;
      setActiveSlot(0);
    } else if (skillName === "office" || skillName === "data_analysis" || skillName === "frontend_design") {
      const slot = skillName === "office" ? 1 : skillName === "data_analysis" ? 2 : 3;
      activeSlotRef.current = slot;
      setActiveSlot(slot);
    }
    setView(skillName || isMonitor ? "chat" : "home");
  };

  const wsRef = useRef<WebSocket | null>(null);
  const lastTurnRef = useRef<number>(0);
  // 2026-08-12 perf: 流式 delta/thinking 累积索引 —— turn → 该 turn 当前
  // 累积中的 delta/thinking 事件 id。WS 收到增量时 O(1) 查 ref 定位 + slice
  // 单点替换, 替代原 [...prev].reverse().find() + prev.map() 的 O(4n) 全表
  // 扫描。setEvents([]) 后 ref 索引自然失效(findIndex=-1 走新增分支), 无需
  // 显式清空; turn 数量有限, Map 内存占用可忽略。
  const lastDeltaIdByTurnRef = useRef<Map<number, number>>(new Map());
  const lastThinkingIdByTurnRef = useRef<Map<number, number>>(new Map());
  // 0.5.0 P6(2026-08-09): 单 WS 复用 —— connect/handleMessage 不再依赖
  // sessionId(避免切换会话/窗口触发 WS 断开重建, 重连期间输入框不可用)。
  // sessionIdRef 恒为最新会话 id, WS 事件/重连后用 ref 读当前值发 replay。
  const sessionIdRef = useRef<number>(sessionId);
  // 懒创建回传的真实会话 id(前端随机 sid → 后端回传真实 id)
  const realSessionIdRef = useRef<number | null>(realSessionId);
  // 当前活动窗口 slot(ref 版, handleMessage 事件过滤用, 避免依赖闭包 state)
  // 注: 初始化默认 1(与 useState 默认一致), 由下方 effect 随 activeSlot 同步
  const activeSlotRef = useRef<number>(1);
  // 已发送 replay 的会话 id(避免首次挂载/重复切换时重复 replay)
  const lastReplaySessionRef = useRef<number | null>(null);
  const fullReloadRef = useRef<boolean>(false);
  // 会话切换后同步 sessionIdRef(供 connect 的 onopen/重连使用当前会话)
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);
  // 同步真实会话 id(懒创建回传后 handleMessage 过滤用)
  useEffect(() => {
    realSessionIdRef.current = realSessionId;
  }, [realSessionId]);

  const reconnectAttemptRef = useRef<number>(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 2026-08-17(实机修复 P0-1): WS 心跳 —— 断网时 TCP 挂起, onclose 不触发,
  // 状态点永远停在"已连接"。应用层每 15s ping, 35s 无任何消息(含 pong) → 强制 close
  // 触发 onclose → reconnecting(黄点脉冲)/重连。后端 main.py 已有 ping→pong 处理。
  const heartbeatTimerRef = useRef<number | null>(null);
  const lastActiveRef = useRef<number>(Date.now());
  const eventIdRef = useRef<number>(0);
  const manualCloseRef = useRef<boolean>(false);
  // 0.5.1 A-1(C-4 事件级去重): 已见事件 id 集合 + 最大 id 锚点。
  // event_id 是 react_events 全局自增 id(跨会话唯一), Set 全局安全;
  // maxEventId 供 replay 增量请求(last_event_id)。
  const seenEventIdsRef = useRef<Set<number>>(new Set());
  const maxEventIdRef = useRef<number>(0);

  // 0.5.0 P2(2026-08-08): 四窗口并发 —— 窗口状态缓存。
  // 4 个固定窗口: 0=监控(主智能体) / 1=子瞻 / 2=白圭 / 3=清和。
  // 当前渲染窗口 = 全局 state(sessionId/events/activeSkill), 切换窗口时:
  // 保存当前窗口状态到 cacheRef[activeSlot] → 恢复目标窗口状态(或懒初始化)。
  const WINDOW_SLOTS = [0, 1, 2, 3] as const;
  // slot → 场景技能映射(固定: 监控=无 skill, 其余对应三场景)
  const WINDOW_SLOT_SKILL: Record<number, string | null> = {
    0: null, // 监控窗口(主智能体, 无场景 skill)
    1: "office",
    2: "data_analysis",
    3: "frontend_design",
  };
  // 0.5.0 P4(2026-08-08 蒋先生反馈): 监控 tab 标签与主智能体名字同步(默认"主智能体")
  // 0/2/3 tab 显示 emoji + 场景中文名, 用户改名 agentName 后 0 tab 自动跟随
  const monitorLabel = `📊 ${agentName || "主智能体"}`;
  const WINDOW_TAB_LABELS: Record<number, string> = {
    0: monitorLabel,
    1: "📄 子瞻",
    2: "📈 白圭",
    3: "🎨 清和",
  };
  interface WindowSnapshot {
    sessionId: number | null;
    skill: string | null;
    events: ReactEvent[];
    input: string;
    model: string;
    lastTurn: number;
  }
  const [activeSlot, setActiveSlot] = useState<number>(1); // 默认子瞻窗口
  // 0.5.0 P6(2026-08-09): 同步活动窗口 slot(handleMessage 事件归属过滤用,
  // 避免闭包捕获旧 state; 切换函数内也主动同步, 此处 effect 兜底)
  useEffect(() => {
    activeSlotRef.current = activeSlot;
  }, [activeSlot]);
  const windowCacheRef = useRef<Record<number, WindowSnapshot>>({});
  // 0.5.0 P5(2026-08-08): 快照增删版本号 —— windowCacheRef 是 ref(不触发渲染),
  // 图标状态圆点需在快照保存/关闭后重渲染; 每次变更 ++ 强制派生计算刷新。
  const [slotsVersion, setSlotsVersion] = useState<number>(0);
  const bumpSlots = useCallback((): void => {
    setSlotsVersion((v) => v + 1);
  }, []);
  // 各 slot 活跃对话状态(红=无对话/绿=有对话): 派生自 windowCacheRef
  // 2026-08-12 13:20 修复: 快照 skill 必须与 slot 预期场景匹配才算"有对话"。
  // 原实现只查 sessionId 是否存在 —— saveWindowSnapshot 的懒初始化会为
  // 从未挂载的 slot(如默认 activeSlot=1 子瞻)凭空创建占位快照, 导致点
  // 白圭/清和/无涯后首页"子瞻"绿灯误亮。占位快照 skill=null, 与 slot
  // 预期场景(office/data_analysis/frontend_design)不匹配 → 不再误判。
  const slotHasSession = useCallback(
    (slot: number): boolean => {
      const snap = windowCacheRef.current[slot];
      if (!snap?.sessionId) return false;
      return snap.skill === WINDOW_SLOT_SKILL[slot];
    },
    []
  );
  void slotsVersion; // 依赖 slotsVersion 使组件在 bump 后重渲染(派生圆点)
  const saveWindowSnapshot = useCallback((): void => {
    if (windowCacheRef.current[activeSlot]) {
      windowCacheRef.current[activeSlot] = {
        ...windowCacheRef.current[activeSlot],
        sessionId: realSessionId ?? sessionId,
        skill: activeSkill,
        events,
        input,
        model: sessionModel,
        lastTurn: lastTurnRef.current,
      };
    } else if (view !== "home") {
      // 2026-08-12 13:20 修复: 首页(view=home)不做懒初始化 —— 打开 PA 时
      // activeSlot 默认为 1(子瞻)但用户从未进入子瞻对话, 原 else 分支会为
      // 该 slot 凭空创建占位快照(sessionId=当前会话), 导致首页"子瞻"状态
      // 圆点误亮。仅对话/历史会话视图才有窗口状态需要保存。
      windowCacheRef.current[activeSlot] = {
        sessionId: realSessionId ?? sessionId,
        skill: activeSkill,
        events,
        input,
        model: sessionModel,
        lastTurn: lastTurnRef.current,
      };
    }
  }, [activeSlot, realSessionId, sessionId, activeSkill, events, input, sessionModel, view]);
  // 0.5.0 P3: 进入监控窗口(主智能体) —— 无快照时创建/复用 monitor 会话
  const enterMonitorWindow = useCallback(async (): Promise<void> => {
    saveWindowSnapshot();
    try {
      // 已有 monitor 会话则复用第一个活跃的, 否则新建 kind=monitor
      const listResp = await adminFetch(
        "http://127.0.0.1:8765/admin/sessions?limit=50"
      );
      let monitorId: number | null = null;
      if (listResp.ok) {
        const list = await listResp.json();
        const found = (list ?? []).find(
          (s: { kind?: string; status?: string; id: number }) =>
            s.kind === "monitor" && s.status !== "archived"
        );
        if (found) monitorId = found.id;
      }
      let sid: number;
      if (monitorId) {
        sid = monitorId;
      } else {
        const resp = await adminFetch("http://127.0.0.1:8765/admin/sessions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind: "monitor", title: "系统监控" }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
        sid = data.id;
      }
      // 挂载到 slot 0 快照并切换
      windowCacheRef.current[0] = {
        sessionId: sid,
        skill: null,
        events: [],
        input: "",
        model: "auto",
        lastTurn: 0,
      };
      bumpSlots();
      setEvents([]);
      setInput("");
      setActiveSkill(null);
      lastTurnRef.current = 0;
      // 0.5.0 P6(2026-08-09): 进入 monitor 会话全量加载历史
      fullReloadRef.current = true;
      // 同步 ref(不等 effect) —— 切换瞬间旧窗口事件不误收
      sessionIdRef.current = sid;
      realSessionIdRef.current = sid;
      activeSlotRef.current = 0;
      setRealSessionId(sid);
      setSessionId(sid);
      setActiveSlot(0);
      setView("chat");
    } catch (e) {
      // eslint-disable-next-line no-alert
      toast.error(`进入监控窗口失败: ${String(e)}`);
    }
  }, [saveWindowSnapshot, bumpSlots]);

  // 切换窗口: 保存当前 → 恢复目标(有快照)或重置为新窗口
  const switchWindow = useCallback(
    (slot: number): void => {
      if (slot === activeSlot) return;
      // 0.5.0 P3: 监控窗口(0)无快照时进入自动创建 monitor 会话
      if (slot === 0 && !windowCacheRef.current[0]?.sessionId) {
        void enterMonitorWindow();
        return;
      }
      saveWindowSnapshot();
      // 2026-08-19(B 方案, 蒋先生反馈"总耗时 20 分钟"): 切换窗口清空耗时
      // 记录 —— 与 handleSwitchSession 一致。四窗口 turn 号各自从 1 递增,
      // 若不清空, turnStartTimes 的 prev.has 短路会沿用旧窗口同 turn 号的
      // start(如 10:36 子瞻 turn1 → 10:54 无涯 turn1 撞车), 耗时虚高。
      setTurnStartTimes(new Map());
      setTurnEndTimes(new Map());
      setTurnDurations(new Map());
      const snap = windowCacheRef.current[slot];
      if (snap && snap.sessionId) {
        // 恢复历史窗口状态(不重建会话)
        setEvents(snap.events);
        setInput(snap.input);
        setActiveSkill(snap.skill);
        setSessionModel(snap.model);
        lastTurnRef.current = snap.lastTurn;
        // 0.5.0 P6(2026-08-09): 恢复快照已有本地 events → 增量续传
        fullReloadRef.current = false;
        // 同步 ref(不等 effect) —— 切换瞬间旧窗口事件不误收
        sessionIdRef.current = snap.sessionId;
        realSessionIdRef.current = snap.sessionId;
        activeSlotRef.current = slot;
        setRealSessionId(snap.sessionId);
        setSessionId(snap.sessionId);
        setActiveSlot(slot);
        setView(snap.skill ? "chat" : "home");
      } else {
        // 无快照: 空窗口, 回首页选择/进入该窗口技能
        setEvents([]);
        setInput("");
        setActiveSkill(null);
        lastTurnRef.current = 0;
        // 0.5.0 P6(2026-08-09): 同步 ref —— 空窗口无会话, 事件全部忽略
        sessionIdRef.current = 0;
        realSessionIdRef.current = null;
        activeSlotRef.current = slot;
        setRealSessionId(null);
        setSessionId(0);
        setActiveSlot(slot);
        setView("home");
      }
    },
    [activeSlot, saveWindowSnapshot, enterMonitorWindow]
  );
  // 0.5.0 P2: 关闭窗口 tab → 归档该窗口会话至"历史任务" + 清空快照。
  // 归档后会话保留在 Sidebar「已归档」折叠区, 可点开继续(恢复新窗口)。
  const closeWindow = useCallback(
    (slot: number): void => {
      const snap = windowCacheRef.current[slot];
      const targetId = snap?.sessionId ?? realSessionId ?? sessionId;
      if (targetId && targetId > 0 && !(activeSlot === slot && !snap)) {
        void adminFetch(`http://127.0.0.1:8765/admin/sessions/${targetId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "archived" }),
        })
          .then((r) => {
            // 0.5.1: 归档后引导(历史树「已归档」区默认折叠, 用户易找不到
            // —— 明确告知位置, 标题已随对话内容展示)
            if (r.ok) {
              notifyUser(
                "对话已归档",
                "可在侧边栏「已归档」区找到并点击恢复该对话"
              );
            }
          })
          .catch(() => undefined);
      }
      delete windowCacheRef.current[slot];
      bumpSlots();
      // 若关闭的是当前活动窗口 → 切到相邻窗口(优先 0 监控)
      // 注: 不调用 saveWindowSnapshot —— 关闭的是当前窗口, 无需保存自身状态
      // (若此时调用会用全局 state 覆盖已删除的 slot 快照, 导致"关闭无效")
      if (activeSlot === slot) {
        const next = slot === 0 ? 1 : 0;
        const nsnap = windowCacheRef.current[next];
        setEvents(nsnap?.events ?? []);
        setInput(nsnap?.input ?? "");
        setActiveSkill(nsnap?.skill ?? null);
        lastTurnRef.current = nsnap?.lastTurn ?? 0;
        // 0.5.0 P6(2026-08-09): 同步 ref(不等 effect) —— 关闭后立即切换归属
        sessionIdRef.current = nsnap?.sessionId ?? 0;
        realSessionIdRef.current = nsnap?.sessionId ?? null;
        activeSlotRef.current = next;
        setRealSessionId(nsnap?.sessionId ?? null);
        setSessionId(nsnap?.sessionId ?? 0);
        setActiveSlot(next);
        setView(nsnap?.skill ? "chat" : "home");
      }
    },
    [activeSlot, realSessionId, sessionId, saveWindowSnapshot, bumpSlots]
  );

  // ── 发送消息到 WS ──────────────────────────────────────────────────────────
  // 2026-08-18(断点继续无响应故障): 原实现 WS 未就绪时静默丢弃消息 ——
  // 用户点"断点继续"时 WS 未 OPEN, resume 被丢弃且无任何提示, 前端停留在
  // 中断轮"思考中"视觉状态, 后端零日志(请求从未到达)。修复: 用户主动操作
  // (userAction)失败时 toast 提示; 自动消息(replay/ping)保持静默防重连期刷屏。
  const sendWs = useCallback(
    (msg: Record<string, unknown>, opts?: { userAction?: boolean }): void => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(msg));
        return;
      }
      if (opts?.userAction) {
        toast.error("连接未就绪，操作未发送。请检查左下角连接状态后重试");
      }
    },
    []
  );

  // 打断/停止: 发送 cancel 消息, 后端取消当前 turn
  const stopGeneration = useCallback((): void => {
    // 2026-08-18: 用 realSessionId 优先(与 pause/resume/regenerate 一致)——
    // 历史会话场景 sessionId 可能不是当前活动会话, 且后端 turn_cancelled
    // 响应按会话匹配, 用错 id 时 2078 守卫会忽略(停止无反馈)
    sendWs(
      { type: "cancel", session_id: realSessionId ?? sessionId },
      { userAction: true }
    );
  }, [sendWs, realSessionId, sessionId]);

  // V1.5 项-5: 流程级暂停 —— 生成中挂起(区别于停止=终止), 可继续
  const [isPaused, setIsPaused] = useState(false);
  // V1.5 项-1(ADR-012 M3): 子代理状态(WS 即时刷新, 重连/切会话从 DB 重建)
  const [subagents, setSubagents] = useState<Record<number, SubagentState>>({});
  const pauseGeneration = useCallback((): void => {
    sendWs({ type: "pause", session_id: realSessionId ?? sessionId }, { userAction: true });
  }, [sendWs, realSessionId, sessionId]);
  const resumeGeneration = useCallback((): void => {
    sendWs({ type: "resume", session_id: realSessionId ?? sessionId }, { userAction: true });
  }, [sendWs, realSessionId, sessionId]);

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
      sendWs({ type: "regenerate", session_id: realSessionId ?? sessionId, turn }, { userAction: true });
    },
    [sendWs, realSessionId, sessionId]
  );

  // V1.5 项-4: 断点恢复 —— 对 interrupted 会话发送 resume WS 消息(后端从
  // 最新 checkpoint 原地续跑中断轮); 无参数时对当前会话生效
  // 2026-08-18(故障修复): 发送前显式检查 WS 状态 —— WS 未就绪时提示而非
  // 静默发送(曾致 resume 被 sendWs 静默丢弃, 用户无反馈)
  const resumeSession = useCallback(
    (sid?: number): void => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        toast.error("连接未就绪，无法断点继续。请检查左下角连接状态后重试");
        return;
      }
      sendWs({ type: "resume", session_id: sid ?? realSessionId ?? sessionId });
      setResumeInfo(null); // 隐藏横幅, 等事件流回来
      // 2026-08-18(停止键无效): resume 后立即进入生成态 —— 输入框按钮
      // 切换为"■ 停止", 用户可随时停止; 原实现不置 isGenerating, 按钮
      // 停留"发送"模式(空输入 disabled), 生成中无法停止
      setIsGenerating(true);
    },
    [sendWs, realSessionId, sessionId]
  );

  // V1.5 项-4: 当前会话的断点恢复信息(GET /admin/sessions/{id}/resume)
  // 对话视图横幅依据: interrupted 且存在 checkpoint
  const [resumeInfo, setResumeInfo] = useState<{
    resumable: boolean;
    checkpoint_turn: number | null;
    status?: string;
  } | null>(null);

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
      // P0-3(2026-08-17): 确认前置改为玻璃弹层(requestDeleteTurn), 此处只执行删除
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
  // P0-3(2026-08-17): 删除回复 → 先弹玻璃确认
  const requestDeleteTurn = useCallback((turn: number): void => {
    setDeleteTurnConfirm(turn);
  }, []);

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
      // P0-3(2026-08-17): alert → Toast
      toast.error("图片超过 5MB 限制");
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
        // 2026-08-15 P2: 带 session_id → 文件落会话工作区 uploads(画地为牢一致)
        body: JSON.stringify({
          filename: file.name || "pasted-image.png",
          content_base64: b64,
          session_id: realSessionId ?? sessionId,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = (await resp.json()) as { name: string; path: string };
      const preview = URL.createObjectURL(file);
      setPendingImage({ name: data.name, path: data.path, preview });
    } catch (e) {
      // eslint-disable-next-line no-alert
      toast.error(`图片上传失败: ${String(e)}`);
    }
  }, [realSessionId, sessionId]);

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
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
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
    // P0-3(2026-08-17): 确认前置改为玻璃弹层
    setTruncateConfirm(afterTurn);
  }, [truncateTurn]);

  // P0-3(2026-08-17): 截断执行(弹层确认后)
  const doTruncateConfirmed = useCallback(async (afterTurn: number): Promise<void> => {
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
  }, [realSessionId, sessionId]);

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

  // V1.5 项-1(ADR-012 §3.4 R7): 子代理卡片 DB 轮询兜底(WS 断线丢事件时
  // 全量重建; 后端 watchdog 判定依赖 DB 不依赖 WS)。仅重建"仍在该会话"
  // 的子代理, 保留 WS 实时收到的最新状态。
  // 0.5.0 P6(2026-08-09): 依赖 [realSessionId, sessionId] 会随会话切换重建
  // → 被 connect 引用会破坏单 WS 复用(connect 每次重建 → 重连)。改为稳定
  // 闭包 + 用 sessionIdRef 读当前会话, 供 connect/replay effect 引用。
  const fetchSubagents = useCallback((): void => {
    void adminFetch(
      `http://127.0.0.1:8765/admin/subagents?session_id=${sessionIdRef.current}`
    )
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: any[]) => {
        if (!Array.isArray(rows) || rows.length === 0) return;
        setSubagents((prev) => {
          const next: Record<number, SubagentState> = {};
          for (const r of rows) {
            const id = Number(r.id);
            const existing = prev[id];
            next[id] = {
              id,
              taskId: r.parent_task ?? `#${id}`,
              prompt: r.prompt ?? "",
              status: r.status ?? "running",
              result: r.result ?? undefined,
              error: r.error ?? undefined,
              toolCalls: Number(r.tool_calls ?? 0),
              // 2026-08-06: 子代理独立会话 id(展开时读取完整对话流)
              subSessionId: r.sub_session_id ?? undefined,
              // WS 实时状态优先(心跳/stalled 以最近一次为准)
              lastHeartbeatTs: existing?.lastHeartbeatTs,
              stalled: existing?.stalled ?? !!r.stalled_at,
              events: existing?.events ?? [],
              createdAt: existing?.createdAt ?? Date.now(),
            };
          }
          return next;
        });
      })
      .catch(() => {
        /* 轮询失败静默(WS 事件仍可即时刷新) */
      });
  }, []);

  // V1.2-6.3: 用量统计 + 错误摘要(任务抽屉内)
  // 2026-08-18(B 方案): 后端 /usage 去计价 —— total_cost/currency 移除,
  // 新增 cached_tokens/cache_hit_rate(缓存命中率)
  const [usageData, setUsageData] = useState<{
    total_calls: number;
    total_tokens: number;
    input_tokens: number;
    output_tokens: number;
    cached_tokens?: number;
    cache_hit_rate?: number | null;
  } | null>(null);
  const [errorsData, setErrorsData] = useState<{
    total_errors: number;
    distinct_errors: number;
    top: { message: string; count: number }[];
  } | null>(null);
  const [diagBusy, setDiagBusy] = useState(false);

  // 2026-08-16(阶段1-c, G1 优化审批列表): monitor 窗口优化审批面板
  type OptimItem = {
    id: number;
    ts: string | null;
    proposal: string;
    category: string;
    status: string;
    plan: unknown[];
    result: string | null;
    session_id: number | null;
  };
  const [optimItems, setOptimItems] = useState<OptimItem[]>([]);
  const [optimBusy, setOptimBusy] = useState(false);
  const [optimError, setOptimError] = useState(false);
  // 2026-08-17(蒋先生反馈): 审批面板移到输入框上方并默认折叠 —— 无待审批
  // 时仅一行横幅; 有待审批时自动展开(审批后收起)
  const [optimOpen, setOptimOpen] = useState(false);
  const optimPending = useMemo(
    () => optimItems.filter((i) => i.status === "pending").length,
    [optimItems]
  );
  // 有待审批出现时自动展开(用户无需手动点开; 审批后回落为折叠横幅)
  useEffect(() => {
    if (optimPending > 0) setOptimOpen(true);
  }, [optimPending]);

  const loadOptimLog = useCallback(async (): Promise<void> => {
    setOptimBusy(true);
    try {
      const resp = await adminFetch(
        "http://127.0.0.1:8765/admin/optim-log?limit=20"
      );
      if (!resp.ok) {
        // 2026-08-16(阶段5 反馈修复): 失败不再静默 —— 空态显示"加载失败"
        // 而非误导性"暂无待审批"(此前 401/网络错误被吞, 用户以为无涯没提交)
        setOptimError(true);
        return;
      }
      setOptimError(false);
      const data = (await resp.json()) as OptimItem[];
      setOptimItems(Array.isArray(data) ? data : []);
    } catch {
      setOptimError(true);
    } finally {
      setOptimBusy(false);
    }
  }, []);

  // monitor 窗口打开时加载 + 会话切换后刷新; 2026-08-16: 渲染条件放宽
  // (monitor 会话内也常驻可见, 此前 !activeSkill 导致会话内面板不渲染,
  // 用户"看不到选项卡"), 且可见期间每 8s 轮询(无涯提交 optim_plan 后
  // 自动出现, 无需手动刷新)。
  useEffect(() => {
    if (activeSlot !== 0) return;
    void loadOptimLog();
    const timer = setInterval(() => void loadOptimLog(), 8000);
    return () => clearInterval(timer);
  }, [activeSlot, activeSkill, loadOptimLog, view]);

  const reviewOptim = useCallback(
    async (id: number, status: "approved" | "rejected"): Promise<void> => {
      try {
        await adminFetch(`http://127.0.0.1:8765/admin/optim-log/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status }),
        });
        await loadOptimLog();
        // 2026-08-18(蒋先生反馈): 审批后无涯不行动, 需再发一句话才执行 ——
        // 后端 review 只更新 DB, 无涯 WS 连接收不到任何通知(无涯无运行入口)。
        // 审批时用户必在无涯窗口(面板仅 activeSlot===0 渲染), 直接经 WS
        // 注入一条系统消息触发无涯立即执行 apply_optim(副作用: 对话流多一条
        // 【系统】消息, 透明可审计)。
        sendWs({
          type: "user",
          session_id: realSessionId ?? sessionId,
          content:
            status === "approved"
              ? `【系统】优化建议 #${id} 已批准，请立即执行 apply_optim 实施该方案。`
              : `【系统】优化建议 #${id} 已被拒绝，无需执行。`,
        }, { userAction: true });
      } catch {
        /* 静默 */
      }
    },
    [loadOptimLog, sendWs, realSessionId, sessionId]
  );

  // 2026-08-16(阶段1-d, G2): 会话工具装配视图(通道收敛可感知)
  type ToolsAssembly = {
    scene: string;
    kind: string;
    workspace: string;
    mcp_servers: string[];
    monitor_tools: string[];
    builtin_tools: string[];
    anchor_tools: string[];
  };
  const [toolsAssembly, setToolsAssembly] = useState<ToolsAssembly | null>(null);

  const loadToolsAssembly = useCallback(async (): Promise<void> => {
    const sid = realSessionId ?? sessionId;
    if (!sid || sid <= 0) return;
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/sessions/${sid}/tools-assembly`
      );
      if (!resp.ok) return;
      const data = (await resp.json()) as ToolsAssembly;
      setToolsAssembly(data);
    } catch {
      /* 加载失败静默 */
    }
  }, [realSessionId, sessionId]);
  useEffect(() => {
    void loadToolsAssembly();
  }, [loadToolsAssembly, view]);

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

  // 技能切换弹层: 打开时加载技能列表; 多选 → 挂载为附加技能(不新建会话)
  useEffect(() => {
    if (!skillPickerOpen) return;
    let cancelled = false;
    setPickerSelected(supplementarySkills); // 打开时预勾选已挂载
    void (async () => {
      try {
        const resp = await adminFetch("http://127.0.0.1:8765/admin/skills");
        const data = (await resp.json()) as {
          name: string; version: string; enabled: boolean;
          description?: string; display_name?: string; model_scope?: string[];
        }[];
        if (!cancelled) setAvailableSkills(Array.isArray(data) ? data : []);
      } catch {
        if (!cancelled) setAvailableSkills([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [skillPickerOpen]);

  // 2026-08-12 Phase 2: 会话附加技能加载(进入对话/切换会话时拉取已挂载列表)
  const loadSupplementarySkills = useCallback(async (): Promise<void> => {
    const sid = realSessionId ?? sessionId;
    if (!sid || sid <= 0) return;
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/sessions/${sid}/supplementary-skills`
      );
      if (!resp.ok) return;
      const data = (await resp.json()) as { skills?: { name: string }[] };
      setSupplementarySkills(
        Array.isArray(data.skills) ? data.skills.map((s) => s.name) : []
      );
    } catch {
      /* 加载失败保持现状 */
    }
  }, [realSessionId, sessionId]);
  useEffect(() => {
    void loadSupplementarySkills();
  }, [loadSupplementarySkills, view]);

  // 2026-08-12 Phase 2: 挂载附加技能(多选确认)
  const addSupplementarySkills = useCallback(
    async (names: string[]): Promise<void> => {
      const sid = realSessionId ?? sessionId;
      if (!sid || sid <= 0 || names.length === 0) return;
      try {
        const resp = await adminFetch(
          `http://127.0.0.1:8765/admin/sessions/${sid}/supplementary-skills`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ skill_names: names, added_by: "picker" }),
          }
        );
        if (!resp.ok) return;
        await loadSupplementarySkills();
      } catch {
        /* 忽略 */
      }
    },
    [realSessionId, sessionId, loadSupplementarySkills]
  );

  // 2026-08-12 Phase 2: 移除附加技能
  const removeSupplementarySkill = useCallback(
    async (name: string): Promise<void> => {
      const sid = realSessionId ?? sessionId;
      if (!sid || sid <= 0) return;
      try {
        await adminFetch(
          `http://127.0.0.1:8765/admin/sessions/${sid}/supplementary-skills/${name}`,
          { method: "DELETE" }
        );
        setSupplementarySkills((prev) => prev.filter((n) => n !== name));
      } catch {
        /* 忽略 */
      }
    },
    [realSessionId, sessionId]
  );

  // ── 处理收到的 WS 消息 ──────────────────────────────────────────────────────
  // 0.5.0 P6(2026-08-09): 后台会话事件写入该窗口快照(独立状态切片)。
  // 单 WS 复用下所有会话事件都到达同一连接; 非当前窗口的会话事件直接写入
  // windowCacheRef 对应 slot 的 events(与当前渲染同款 delta/thinking 累积),
  // 不触发当前视图渲染; 切回该窗口时 switchWindow 恢复快照即展示后台进展。
  // 注: 仅操作 ref, 不依赖 state, 引用稳定可被 handleMessage 安全捕获。
  const appendToSlotEvents = useCallback(
    (slot: number, msg: WSMessage): void => {
      const snap = windowCacheRef.current[slot];
      if (!snap || !msg.event_type || msg.turn === undefined) return;
      const turn = msg.turn as number;
      const mk = (et: EventType): ReactEvent => ({
        id: ++eventIdRef.current,
        session_id: msg.session_id ?? snap.sessionId ?? 0,
        turn,
        event_type: et,
        payload: msg.payload ?? {},
        ts: Date.now(),
      });
      // delta 流式累积: 合并到该 turn 最后一条 delta(与当前渲染一致)
      if (msg.event_type === "delta") {
        const deltaText = String(msg.payload?.content ?? "");
        if (!deltaText) return;
        const last = [...snap.events]
          .reverse()
          .find(
            (e) =>
              e.turn === turn &&
              (e.event_type === "delta" || e.event_type === "final")
          );
        if (last && last.event_type === "delta") {
          snap.events = snap.events.map((e) =>
            e.id === last.id
              ? {
                  ...e,
                  payload: {
                    turn,
                    content: String(e.payload.content ?? "") + deltaText,
                  },
                }
              : e
          );
        } else if (!(last && last.event_type === "final")) {
          snap.events = [...snap.events, mk("delta")];
        }
      } else if (msg.event_type === "thinking") {
        // 推理增量: 合并到该 turn 最后一条 thinking(与当前渲染一致)
        const reasoning = String(
          msg.payload?.reasoning ?? msg.payload?.content ?? ""
        );
        const last = [...snap.events]
          .reverse()
          .find((e) => e.turn === turn && e.event_type === "thinking");
        if (last) {
          snap.events = snap.events.map((e) =>
            e.id === last.id
              ? {
                  ...e,
                  payload: {
                    turn,
                    reasoning: String(e.payload.reasoning ?? "") + reasoning,
                  },
                }
              : e
          );
        } else {
          snap.events = [
            ...snap.events,
            mk("thinking" as EventType),
          ];
        }
      } else {
        // 其余事件(user/final/tool_call/tool_result/error/sandbox_output)直接追加
        snap.events = [...snap.events, mk(msg.event_type as EventType)];
      }
      // 更新该窗口 lastTurn(切回时 replay 从正确位置增量续传)
      if (turn > snap.lastTurn) snap.lastTurn = turn;
      // 快照仅用于切回展示, 不 bumpSlots(避免当前视图无谓重渲染)
    },
    []
  );
  const handleMessage = useCallback((msg: WSMessage): void => {
    switch (msg.type) {
      case "pong":
        break;

      case "react_event": {
        // 0.5.1 A-1(C-4 事件级去重): 已见 event_id 直接丢弃 —— 断线重连
        // 按 turn 粒度补发时, 该轮已收到的事件会全量重放; 不去重会导致
        // delta 重复累积、thinking/tool_result 重复渲染。注意: 去重必须在
        // 一切副作用(权限弹窗/快照/累积)之前, 已见事件不重复触发。
        if (typeof msg.event_id === "number") {
          if (seenEventIdsRef.current.has(msg.event_id)) return;
          seenEventIdsRef.current.add(msg.event_id);
          if (msg.event_id > maxEventIdRef.current) {
            maxEventIdRef.current = msg.event_id;
          }
        }
        // 2026-08-19(假思考检测): 每次有效事件到达刷新"最后事件时刻"。
        // ThinkingWait 据此区分"正常思考(持续有流式事件)"与"疑似卡死
        // (长时间无任何事件)"。
        setLastEventAt(Date.now());
        // 0.5.1 A: 权限确认请求 → 全局置顶弹窗(与事件归属/当前窗口无关,
        // 避免确认卡片埋在对话流内被错过 → 60s 超时 → 对话卡死)。
        // 2026-08-10 22:00: 加 !msg.replayed —— 切回历史会话时后端 replay
        // 重放的历史事件若含 tool_confirmation_required, 不应再次弹窗
        // (历史工具调用已执行完毕, 恢复会话不应重新触发权限确认)。
        if (msg.event_type === "tool_confirmation_required" && msg.payload && !msg.replayed) {
          const p = msg.payload as {
            confirmation_id?: string;
            message?: string;
            risk_level?: string;
            reason?: string;
            args_summary?: Record<string, unknown>;
            display?: { title?: string; summary?: string[]; tool_label?: string };
          };
          if (p.confirmation_id) {
            // 2026-08-15: 修正字段名不匹配(此前读 p.risk/p.args_preview,
            // 后端实际是 risk_level/args_summary → 弹窗风险/参数一直为空)
            // 2026-08-18(请求卡片点不了): 历史事件(msg.replayed)不弹窗
            // 但内嵌卡片仍渲染 —— 标记过期禁用按钮(历史确认早已无效)
            if (msg.replayed) {
              markConfirmExpired(p.confirmation_id);
            }
            const argsPreview = p.args_summary
              ? JSON.stringify(p.args_summary).slice(0, 200)
              : undefined;
            setPendingConfirm({
              confirmation_id: p.confirmation_id,
              session_id:
                (msg.session_id as number) ??
                (realSessionIdRef.current ?? sessionIdRef.current),
              message: String(p.message ?? "需要确认"),
              risk: p.risk_level,
              reason: p.reason,
              argsPreview,
              display: p.display,
            });
            // 系统通知(后台/其他窗口时也能提醒)
            notifyUser("需要你的确认", String(p.display?.title ?? p.message ?? "工具执行需确认"));
          }
        }
        // 2026-08-18(请求卡片点不了): 确认结果事件到达(用户已处理/后端
        // 超时拒绝) → 标记对应卡片"已处理"禁用按钮, 防重复操作
        if (
          msg.event_type === "tool_confirmation_result" &&
          msg.payload &&
          (msg.payload as { confirmation_id?: string }).confirmation_id
        ) {
          markConfirmExpired(
            (msg.payload as { confirmation_id?: string }).confirmation_id
          );
          setPendingConfirm(null); // 结果已出, 全局弹窗同步关闭
        }
        if (msg.event_type && msg.turn !== undefined && msg.payload) {
          // 2026-08-19: 记录该轮首个事件到达时刻(≈开始时间, 用于耗时显示)。
          // 仅当前会话记录(其他窗口快照的 turn 不渲染, 记录无意义)。
          if (
            !msg.session_id ||
            msg.session_id === (realSessionIdRef.current ?? sessionIdRef.current)
          ) {
            const firstTurn = msg.turn as number;
            setTurnStartTimes((prev) => {
              if (prev.has(firstTurn)) return prev;
              const next = new Map(prev);
              next.set(firstTurn, Date.now());
              return next;
            });
          }
          // 0.5.0 P6(2026-08-09): 单 WS 复用下的事件分发 —— WS 上所有会话的
          // 事件都会到达。当前渲染会话(realSessionId ?? sessionId)的事件走
          // 正常渲染; 其他窗口后台生成中的事件写入该窗口快照(独立状态切片,
          // 不渲染当前视图), 切回该窗口时 switchWindow 恢复快照即展示。
          const curSid = realSessionIdRef.current ?? sessionIdRef.current;
          if (msg.session_id && msg.session_id !== curSid) {
            // 属于其他窗口快照的会话(后台生成中) → 写入该窗口快照
            const targetSlot = (WINDOW_SLOTS as readonly number[]).find(
              (s) =>
                s !== activeSlotRef.current &&
                windowCacheRef.current[s]?.sessionId === msg.session_id
            );
            if (targetSlot !== undefined) {
              appendToSlotEvents(targetSlot, msg);
              return;
            }
            // 懒创建回传(B2 P1-9): 仅当前会话真实 id 尚未确认(realSessionId
            // 为 null)时才接受任意未知 sid 作为回传; 已确认后一律忽略。
            if (realSessionIdRef.current == null) {
              realSessionIdRef.current = msg.session_id;
              setRealSessionId(msg.session_id);
            } else {
              return;
            }
          }
          // 从后端回传更新真实 session_id(B2 P1-9:activate 需要真实 session)
          if (msg.session_id && msg.session_id !== sessionIdRef.current) {
            setRealSessionId(msg.session_id);
          }
          // V1.5 项-5: 流程级暂停状态(不进入事件列表, 仅切换按钮态)
          if (msg.event_type === "turn_paused") {
            setIsPaused(true);
            return;
          }
          if (msg.event_type === "turn_resumed") {
            setIsPaused(false);
            // 2026-08-19(蒋先生反馈"断点恢复无反馈/状态不同步"): 后端断点
            // 恢复成功会携带 checkpoint_turn(区别于流程级"继续")。此时:
            // ① 明确提示用户已从第 N 轮继续(此前无任何成功反馈);
            // ② 清空本地 events 并全量 replay —— 后端已回滚中断轮残留
            // (删 assistant/tool 消息 + react_events), 本地旧事件若不清理
            // 会与重跑产生的新事件(同 turn 号、新 id)叠加, 渲染错乱。
            // turn_resumed 在回滚之后发送, 此处 replay 读到的必然是干净
            // 数据(无竞态); 且 run_turn 的新事件在 turn_resumed 之后推送,
            // 前端先 replay 再追加, 顺序正确。
            if (typeof msg.checkpoint_turn === "number") {
              toast.success(`已从第 ${msg.checkpoint_turn} 轮断点继续`);
              setEvents([]);
              lastTurnRef.current = 0;
              maxEventIdRef.current = 0;
              seenEventIdsRef.current.clear();
              fullReloadRef.current = true;
              const rws = wsRef.current;
              const rSid = realSessionIdRef.current ?? sessionIdRef.current;
              if (rws && rws.readyState === WebSocket.OPEN && rSid > 0) {
                sendWs({
                  type: "replay",
                  session_id: rSid,
                  last_turn: 0,
                  full: true,
                });
              }
            }
            return;
          }
          // 2026-08-16(阶段2 反馈): 迭代上限询问 —— 长任务 20 步到达时
          // 弹确认框, 用户选择继续(扩展上限)或停止(正常收尾)。
          if (msg.event_type === "iteration_limit_reached") {
            const used = Number(msg.payload?.used ?? 0);
            const max = Number(msg.payload?.max ?? 0);
            const sid = (msg.session_id as number) ??
              (realSessionIdRef.current ?? sessionIdRef.current);
            // P0-3(2026-08-17): window.confirm → 玻璃确认弹层(异步确认, 选择在 JSX 处理)
            setIterLimitConfirm({ used, max, sid });
            return;
          }
          // 流式增量: 追加到该 turn 的最后一条 delta 事件(累积显示, 不刷爆列表)
          // 2026-08-12 perf: 用 lastDeltaIdByTurnRef O(1) 索引 + slice 单点替换,
          // 替代 [...prev].reverse().find() + prev.map() 的 O(4n) 全表扫描。
          if (msg.event_type === "delta") {
            const deltaText = String(msg.payload.content ?? "");
            if (deltaText) {
              const turn = msg.turn as number;
              const lastId = lastDeltaIdByTurnRef.current.get(turn);
              setEvents((prev) => {
                if (lastId !== undefined) {
                  // 反向查找(delta 事件通常在数组末尾, 平均 O(1) 最坏 O(n))
                  for (let i = prev.length - 1; i >= 0; i--) {
                    if (prev[i].id === lastId) {
                      const target = prev[i];
                      if (target.event_type === "delta") {
                        const next = prev.slice();
                        next[i] = {
                          ...target,
                          payload: {
                            turn,
                            content:
                              String(target.payload.content ?? "") + deltaText,
                          },
                        };
                        return next;
                      }
                      // final 已存在则忽略增量(final 为完整文本)
                      if (target.event_type === "final") return prev;
                      break;
                    }
                  }
                }
                const newId = ++eventIdRef.current;
                lastDeltaIdByTurnRef.current.set(turn, newId);
                return [
                  ...prev,
                  {
                    id: newId,
                    session_id: msg.session_id ?? sessionIdRef.current,
                    turn,
                    event_type: "delta" as EventType,
                    payload: { turn, content: deltaText },
                    ts: Date.now(),
                  },
                ];
              });
            }
            return;
          }
          // 推理增量: 逐 token thinking 事件累积合并到该 turn 最后一条
          // thinking(避免每条都追加 → 渲染抖动/多条"思考中"卡片)
          // 2026-08-12 perf: 同 delta, 用 lastThinkingIdByTurnRef O(1) 索引。
          if (msg.event_type === "thinking") {
            const reasoning = String(
              msg.payload.reasoning ?? msg.payload.content ?? ""
            );
            const turn = msg.turn as number;
            const lastId = lastThinkingIdByTurnRef.current.get(turn);
            setEvents((prev) => {
              if (lastId !== undefined) {
                for (let i = prev.length - 1; i >= 0; i--) {
                  if (prev[i].id === lastId) {
                    const target = prev[i];
                    if (target.event_type === "thinking") {
                      const next = prev.slice();
                      next[i] = {
                        ...target,
                        payload: {
                          turn,
                          reasoning:
                            String(target.payload.reasoning ?? "") + reasoning,
                        },
                      };
                      return next;
                    }
                    break;
                  }
                }
              }
              const newId = ++eventIdRef.current;
              lastThinkingIdByTurnRef.current.set(turn, newId);
              return [
                ...prev,
                {
                  id: newId,
                  session_id: msg.session_id ?? sessionIdRef.current,
                  turn,
                  event_type: "thinking" as EventType,
                  payload: { turn, reasoning },
                  ts: Date.now(),
                },
              ];
            });
            return;
          }
          const event: ReactEvent = {
            id: ++eventIdRef.current,
            session_id: msg.session_id ?? sessionIdRef.current,
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
            session_id: msg.session_id ?? sessionIdRef.current,
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
        // 0.5.0 P6(2026-08-09): 单 WS 复用下只处理当前会话的 turn_end
        // (后台窗口的 turn_end 不打断当前窗口生成态)
        if (msg.session_id && msg.session_id !== (realSessionIdRef.current ?? sessionIdRef.current)) {
          break;
        }
        // 2026-08-19: 记录每轮完成时间(turn → 毫秒时间戳), TurnCard 渲染
        // "完成于 HH:MM:SS"; 同时记录开始时间于"run_turn_start_rN"日志阶段
        // 后续可加耗时(但当前前端无 start 事件, 暂只显示完成时刻)
        if (typeof msg.turn === "number") {
          const turnNum = msg.turn;
          setTurnEndTimes((prev) => {
            const next = new Map(prev);
            next.set(turnNum, Date.now());
            return next;
          });
          // 2026-08-19(A 方案): 服务端权威耗时优先 —— turn_end 携带
          // duration_ms(后端 monotonic 计时)。前端仅回退 Date.now() 差值
          // (旧后端/无字段场景)。
          if (typeof msg.duration_ms === "number" && msg.duration_ms >= 0) {
            setTurnDurations((prev) => {
              const next = new Map(prev);
              next.set(turnNum, msg.duration_ms as number);
              return next;
            });
          }
        }
        setIsGenerating(false);
        setIsPaused(false); // V1.5 项-5: 轮次结束清除暂停态
        // V1.4-8.4: 应用在后台时系统通知(任务完成)
        void notifyUser("任务完成", "本轮对话已结束");
        break;

      // ── V1.5 项-1(ADR-012 §3.4 M3): 子代理事件(仅即时刷新 state;
      // 可靠性兜底 = fetchSubagents DB 轮询, 见 ws.onopen) ──
      // 2026-08-11 用户反馈: 子代理事件串窗(白圭的子代理出现在子瞻窗口)。
      // 后端已在 subagent_* 事件补 session_id, 前端按 session_id 过滤:
      // 非当前活动窗口的子代理事件直接丢弃(子代理 state 是全局唯一,
      // 不按窗口分片, 故只渲染当前窗口的子代理)。
      case "subagent_start": {
        if (msg.session_id && msg.session_id !== (realSessionIdRef.current ?? sessionIdRef.current)) {
          return;
        }
        if (msg.subagent_id) {
          setSubagents((prev) => ({
            ...prev,
            [msg.subagent_id as number]: createSubagent(
              msg.subagent_id as number,
              msg.task_id ?? `#${msg.subagent_id}`,
              String(msg.payload?.prompt ?? msg.prompt ?? "")
            ),
          }));
        }
        break;
      }
      case "subagent_heartbeat": {
        if (msg.session_id && msg.session_id !== (realSessionIdRef.current ?? sessionIdRef.current)) {
          return;
        }
        if (msg.subagent_id) {
          const id = msg.subagent_id as number;
          setSubagents((prev) =>
            prev[id]
              ? { ...prev, [id]: { ...prev[id], lastHeartbeatTs: Date.now() } }
              : prev
          );
        }
        break;
      }
      case "subagent_event": {
        if (msg.session_id && msg.session_id !== (realSessionIdRef.current ?? sessionIdRef.current)) {
          return;
        }
        if (msg.subagent_id && msg.event_type) {
          const id = msg.subagent_id as number;
          const et = String(msg.event_type);
          // 仅记录精简事件(tool_call/tool_result/final/error), 防事件风暴
          if (
            et === "tool_call" ||
            et === "tool_result" ||
            et === "final" ||
            et === "error"
          ) {
            setSubagents((prev) =>
              prev[id]
                ? {
                    ...prev,
                    [id]: {
                      ...prev[id],
                      toolCalls:
                        et === "tool_call"
                          ? prev[id].toolCalls + 1
                          : prev[id].toolCalls,
                      events: [
                        ...prev[id].events,
                        {
                          eventType: et,
                          payload: msg.payload ?? {},
                          ts: Date.now(),
                        },
                      ],
                    },
                  }
                : prev
            );
          }
        }
        break;
      }
      case "subagent_stalled": {
        if (msg.session_id && msg.session_id !== (realSessionIdRef.current ?? sessionIdRef.current)) {
          return;
        }
        if (msg.subagent_id) {
          const id = msg.subagent_id as number;
          setSubagents((prev) =>
            prev[id]
              ? { ...prev, [id]: { ...prev[id], stalled: true } }
              : prev
          );
        }
        break;
      }
      case "subagent_result": {
        if (msg.session_id && msg.session_id !== (realSessionIdRef.current ?? sessionIdRef.current)) {
          return;
        }
        if (msg.subagent_id) {
          const id = msg.subagent_id as number;
          setSubagents((prev) =>
            prev[id]
              ? {
                  ...prev,
                  [id]: {
                    ...prev[id],
                    status: "succeeded",
                    result: msg.result ?? "",
                    stalled: false,
                  },
                }
              : prev
          );
        }
        break;
      }
      case "subagent_error": {
        if (msg.session_id && msg.session_id !== (realSessionIdRef.current ?? sessionIdRef.current)) {
          return;
        }
        if (msg.subagent_id) {
          const id = msg.subagent_id as number;
          setSubagents((prev) =>
            prev[id]
              ? {
                  ...prev,
                  [id]: {
                    ...prev[id],
                    status:
                      msg.status === "cancelled" ? "cancelled" : "failed",
                    error: msg.error ?? msg.status ?? "failed",
                    stalled: false,
                  },
                }
              : prev
          );
        }
        break;
      }

      case "turn_cancelled":
        // 打断/停止: 后端已取消当前 turn
        // 0.5.0 P6(2026-08-09): 只处理当前会话的取消(后台窗口的 cancel 不干扰)
        if (msg.session_id && msg.session_id !== (realSessionIdRef.current ?? sessionIdRef.current)) {
          break;
        }
        setIsGenerating(false);
        setIsPaused(false); // V1.5 项-5: 终止时清除暂停态
        void notifyUser("任务已停止", "你手动停止了本轮对话");
        break;

      case "error":
        // 0.5.1: 确认相关错误(unknown confirmation_id 等超时竞态)
        // → 关闭全局确认弹窗, 避免用户对已过期确认重复操作
        // 2026-08-16(蒋先生反馈"切换 LLM 后对话框锁定无法输入"): 确认超时
        // 的 error 必须先清遮罩 —— 否则 pendingConfirm 全屏遮罩(inset:0,
        // zIndex 9999)永久覆盖输入区, 输入框"完全无法打字"。此清除必须在
        // 会话归属分发之前执行(后台会话的确认超时同样要解全局锁)。
        if (
          msg.message &&
          /confirmation|permission|timeout/i.test(String(msg.message))
        ) {
          // 2026-08-18(请求卡片点不了): 后端 error 现带 confirmation_id
          // (unknown confirmation_id 场景) —— 标记对应卡片过期禁用按钮
          markConfirmExpired(
            (msg as { confirmation_id?: string }).confirmation_id
          );
          setPendingConfirm(null);
        }
        // 0.5.0 P6(2026-08-09): 单 WS 复用下按会话归属分发 —— 当前会话的
        // 错误走正常处理; 后台窗口的错误写入该窗口快照(切回时可见)。
        if (msg.session_id && msg.session_id !== (realSessionIdRef.current ?? sessionIdRef.current)) {
          const errSlot = (WINDOW_SLOTS as readonly number[]).find(
            (s) =>
              s !== activeSlotRef.current &&
              windowCacheRef.current[s]?.sessionId === msg.session_id
          );
          if (errSlot !== undefined && msg.message) {
            const snap = windowCacheRef.current[errSlot];
            if (snap) {
              snap.events = [
                ...snap.events,
                {
                  id: ++eventIdRef.current,
                  session_id: msg.session_id,
                  turn: msg.turn ?? snap.lastTurn,
                  event_type: "error",
                  payload: { message: msg.message },
                  ts: Date.now(),
                },
              ];
            }
          }
          break;
        }
        setIsGenerating(false);
        void notifyUser("任务出错", String(msg.message ?? "未知错误"));
        if (msg.message) {
          // 2026-08-19(蒋先生反馈"断点恢复无反馈"): resume 失败醒目提示 ——
          // 用户点"断点继续"后若无 checkpoint(如全新会话/中断轮无 checkpoint),
          // 后端回 error resume_failed。此前仅插入错误事件易被忽略, 用户
          // 无法确认"断点恢复没生效"(仍停留在旧状态)。
          if (/resume_failed/i.test(msg.message)) {
            toast.error(String(msg.message));
          }
          // B2 P1-9: skill_not_found → 自动切回首页(重新选择 Skill)
          if (/skill_not_found|skill not found/i.test(msg.message)) {
            setActiveSkill(null);
            setView("home");
          }
          const event: ReactEvent = {
            id: ++eventIdRef.current,
            session_id: msg.session_id ?? sessionIdRef.current,
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
  }, [sendWs]);

  // ── 建立 WS 连接 ──────────────────────────────────────────────────────────
  const connect = useCallback((): void => {
    if (manualCloseRef.current) return;

    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("connected");
        setReconnectCount(0);
        reconnectAttemptRef.current = 0;
        // 2026-08-17(实机修复): 启动心跳 —— 15s 一次 ping; 25s 无任何消息
        // (含 pong) 视为链路失效。超时后**不依赖 close() 触发 onclose**
        // (后端进程死亡时 close 帧发不出, onclose 可能挂起数分钟),
        // 直接走既有重连链路(黄灯 + 指数退避)。
        lastActiveRef.current = Date.now();
        if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
        heartbeatTimerRef.current = window.setInterval(() => {
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            if (Date.now() - lastActiveRef.current > 25000) {
              // 静默超时(断网/TCP 挂起): 清理旧连接, 直接进入重连
              if (heartbeatTimerRef.current) {
                clearInterval(heartbeatTimerRef.current);
                heartbeatTimerRef.current = null;
              }
              const dead = wsRef.current;
              wsRef.current = null;
              if (dead) {
                dead.onclose = null; // 防止半死连接晚到 onclose 二次触发重连
                try {
                  dead.close();
                } catch {
                  /* ignore */
                }
              }
              setStatus("reconnecting");
              scheduleReconnect();
            } else {
              sendWs({ type: "ping" });
            }
          }
        }, 15000);
        // 重连后发送 replay(首次连接 last_turn=0; 切换历史会话 full=true 全量加载)
        // 0.5.0 P6(2026-08-09): 用 sessionIdRef 读当前会话(connect 不再依赖
        // sessionId, 否则切换会话会触发 close+重连, 重连期间输入框不可用)
        lastReplaySessionRef.current = sessionIdRef.current;
        sendWs({
          type: "replay",
          session_id: sessionIdRef.current,
          last_turn: lastTurnRef.current,
          full: fullReloadRef.current,
          // 0.5.1 A-1(C-4): 事件级增量锚点(已收最大 event_id, 0 表示无)
          last_event_id: maxEventIdRef.current || undefined,
        });
        // V1.5 项-1(M3 R7): 重连后从 DB 重建子代理卡片(WS 丢事件兜底)
        fetchSubagents();
      };

      ws.onmessage = (ev: MessageEvent) => {
        try {
          // 2026-08-17(实机修复): 任何消息(含 pong)都算链路活跃
          lastActiveRef.current = Date.now();
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
        if (heartbeatTimerRef.current) {
          clearInterval(heartbeatTimerRef.current);
          heartbeatTimerRef.current = null;
        }
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
    // 0.5.0 P6(2026-08-09): connect 不再依赖 sessionId —— 单 WS 复用,
    // 会话切换由 replay effect 处理, 避免每次切换断开重建连接
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sendWs, handleMessage, fetchSubagents]);

  // ── 指数退避重连 ──────────────────────────────────────────────────────────
  const scheduleReconnect = useCallback((): void => {
    if (manualCloseRef.current) return;
    const attempt = reconnectAttemptRef.current;
    // P0-1(2026-08-17): 即将进行的尝试序号同步到 state, 供侧边栏展示
    setReconnectCount(attempt + 1);
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
      // P0-3(2026-08-17): alert → Toast
      toast.error("文件超过 15MB 限制");
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
          // 2026-08-15 P2: 带 session_id → 文件落会话工作区 uploads(画地为牢一致)
          body: JSON.stringify({
            filename: file.name,
            content_base64: b64,
            session_id: realSessionId ?? sessionId,
          }),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.error ?? `HTTP ${resp.status}`);
        }
        const data = (await resp.json()) as { name: string; path: string };
        setPendingUpload({ name: data.name, path: data.path });
      } catch (e) {
        // eslint-disable-next-line no-alert
        toast.error(`上传失败: ${String(e)}`);
      }
    },
    [realSessionId, sessionId]
  );

  const sendMessage = useCallback((): void => {
    // 2026-08-19(蒋先生反馈"发送键变停止键还能发新指令"): 生成中禁止
    // 发送新消息 —— 后端 per-session 会话锁会被当前 run_turn 占用, 新
    // user_message 静默排队等锁无响应。此前仅按钮变"停止"(点击=停止),
    // 但 Enter 键/输入框仍可触发 sendMessage(三重无防御)。此处加
    // isGenerating 防御; 输入区改为可预输入但不发送(占位提示)。
    if (isGenerating) {
      toast.info("正在生成中，请等待本轮完成或点「停止」后再发送");
      return;
    }
    let content = input.trim();
    if (!content) return;
    setIsPaused(false); // V1.5 项-5: 发送新消息前清除暂停态
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
    // 2026-08-12 Phase 3: 解析 /技能名 标记 → 附带 supplementary_skills(后端挂载)
    // 仅匹配行首或空白后的 /(避免误匹配 http:// 等路径)
    const slashMentioned: string[] = [];
    {
      const re = /(?:^|\s)\/([a-z0-9_]+)/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(content)) !== null) {
        const sname = m[1].toLowerCase();
        if (
          availableSkills.some((s) => s.name === sname && s.enabled) &&
          !slashMentioned.includes(sname)
        ) {
          slashMentioned.push(sname);
        }
      }
    }
    sendWs({
      type: "user_message",
      session_id: sessionId,
      content,
      // V1.3-7.2 工作流自动化: 携带会话级自动执行配置(后端优先取显式传参)
      auto_execute: autoExec || undefined,
      max_rounds: autoExec ? autoRounds : undefined,
      // 2026-08-12 Phase 3: /召唤的附加技能
      ...(slashMentioned.length > 0
        ? { supplementary_skills: slashMentioned }
        : {}),
    }, { userAction: true });
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
  }, [input, sessionId, sendWs, pendingUpload, pendingImage, editingOriginal, realSessionId, autoExec, autoRounds, availableSkills, isGenerating]);

  // 2026-08-12 Phase 3: /召唤技能 —— 浮层过滤列表(按斜杠后关键字模糊匹配)
  const slashFilteredSkills = useMemo(() => {
    if (!slashOpen) return [] as (typeof availableSkills)[number][];
    return availableSkills
      .filter((s) => s.enabled)
      .filter((s) => {
        if (!slashQuery) return true;
        const q = slashQuery.toLowerCase();
        return (
          s.name.toLowerCase().includes(q) ||
          (s.description ?? "").toLowerCase().includes(q) ||
          (s.display_name ?? "").toLowerCase().includes(q)
        );
      })
      .slice(0, 8);
  }, [slashOpen, slashQuery, availableSkills]);

  // 2026-08-12 Phase 3: 选中技能 → 替换输入框尾部 /xx 为 /技能名
  const insertSlashSkill = useCallback((name: string): void => {
    setInput((prev) => {
      const m = /(?:^|\s)\/([a-z0-9_]*)$/.exec(prev);
      if (m) {
        const keep = prev.slice(0, m.index + m[0].length - m[1].length);
        return `${keep}${name} `;
      }
      return prev;
    });
    setSlashOpen(false);
    setSlashQuery("");
    inputRef.current?.focus();
  }, []);

  // 0.5.0 P6(2026-08-09): 单 WS 复用 —— sessionId 变化时不再断开重建连接,
  // 直接通过既有 WS 发送 replay 切换会话(后端按 session_id 路由)。
  // 效果: 切换会话/窗口零重连, 输入框立即可用(原实现每次切换都 close+connect,
  // 重连期间 status!=="connected" 导致发送按钮禁用)。
  useEffect(() => {
    if (lastReplaySessionRef.current === sessionId) return;
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN && sessionId > 0) {
      lastReplaySessionRef.current = sessionId;
      sendWs({
        type: "replay",
        session_id: sessionId,
        last_turn: lastTurnRef.current,
        full: fullReloadRef.current,
        // 0.5.1 A-1(C-4): 事件级增量锚点(已收最大 event_id, 0 表示无)
        last_event_id: maxEventIdRef.current || undefined,
      });
      fetchSubagents();
    }
  }, [sessionId, sendWs, fetchSubagents]);

  // ── 生命周期:挂载时连接,卸载时关闭 ──────────────────────────────────────
  // 0.5.0 P6(2026-08-09): 依赖去掉 sessionId —— 单 WS 复用, WS 只在挂载时
  // 建立一次; 会话/窗口切换由 replay effect 走既有连接(零重连, 输入框不中断)
  useEffect(() => {
    manualCloseRef.current = false;
    connect();
    return () => {
      manualCloseRef.current = true;
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      // 2026-08-17(实机修复): 卸载时清理心跳定时器
      if (heartbeatTimerRef.current) {
        clearInterval(heartbeatTimerRef.current);
        heartbeatTimerRef.current = null;
      }
      const ws = wsRef.current;
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
      wsRef.current = null;
    };
  }, [connect]);

  // 加载默认工作区(画地为牢选择器显示用; 会话工作区后端持久化)
  useEffect(() => {
    let cancelled = false;
    adminFetch("http://127.0.0.1:8765/admin/workspaces")
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

  // 2026-08-15 修复: 会话工作区回读 —— 切换会话/窗口时拉取该会话已保存的
  // workspace 并展示(此前仅 mount 时加载全局默认, 选定工作区从不显示,
  // 用户以为"每次启动对话跳回默认"; 实际后端 sessions.workspace 已持久化)。
  // 2026-08-15 补充: workspace 为空(NULL/默认)时也要更新为默认展示 ——
  // 否则切到未设置工作区的会话时残留上一个会话的旧值(场景工作区不同步)。
  useEffect(() => {
    const sid = realSessionId ?? sessionId;
    if (!sid) return;
    let cancelled = false;
    adminFetch(`http://localhost:8765/admin/sessions/${sid}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        const ws =
          typeof data.workspace === "string" && data.workspace
            ? data.workspace
            : null;
        setWorkspace(ws);
        setWorkspaceInput(ws ?? "");
      })
      .catch(() => {
        /* 后端未起时忽略 */
      });
    return () => {
      cancelled = true;
    };
  }, [realSessionId, sessionId]);

  // 保存会话工作区(画地为牢)
  const saveWorkspace = useCallback(async (): Promise<void> => {
    const path = workspaceInput.trim();
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/sessions/${realSessionId ?? sessionId}/workspace`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workspace: path || null }),
        }
      );
      const data = await resp.json();
      if (!resp.ok) {
        // P0-3(2026-08-17): alert → Toast
        toast.error(`工作区设置失败: ${data.error ?? data.detail ?? `HTTP ${resp.status}`}`);
        return;
      }
      setWorkspace(data.workspace ?? null);
      setWorkspacePanelOpen(false);
    } catch (err) {
      toast.error(`工作区设置失败: ${String(err)}`);
    }
  }, [realSessionId, sessionId, workspaceInput]);

  // 加载可用模型列表(模型选择器用)
  // 0.5.1(2026-08-09 蒋先生反馈): 改为每次进入对话视图/窗口切换时刷新,
  // 避免"启动后新增 provider 不出现在下拉框"(原来仅 mount 拉取一次)。
  const refreshModelOptions = useCallback(async (): Promise<void> => {
    try {
      const r = await adminFetch(
        "http://127.0.0.1:8765/admin/settings/providers"
      );
      const data = await r.json();
      const names = (data.providers ?? [])
        .filter((p: { enabled: boolean }) => p.enabled)
        .map((p: { name: string }) => p.name);
      setModelOptions(names);
    } catch {
      // 后端未就绪时静默, 选择器只显示"自动"
    }
  }, []);
  useEffect(() => {
    void refreshModelOptions();
  }, [refreshModelOptions, view]);

  // 切换会话模型选择(自动/手动)
  const changeSessionModel = async (modelId: string): Promise<void> => {
    const target = realSessionId ?? sessionId;
    if (target <= 0) return;
    try {
      const resp = await adminFetch(
        `http://127.0.0.1:8765/admin/sessions/${target}/model`,
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
      // P0-3(2026-08-17): alert → Toast
      toast.error(`切换模型失败: ${String(err)}`);
    }
  };

  // ── 渲染 ──────────────────────────────────────────────────────────────────
  // ── 渲染: FlowSpace 布局(液体背景 + 侧边栏 + 顶栏 + 内容视图) ─────────
  // V1.1 布局优化: 外层 100vw×100vh 铺满视口(overflow hidden), 内层三栏 flex
  // 2026-08-10: 左右栏固定宽度两态(展开/折叠), 移除拖拽分隔条(ResizeHandle);
  // 收起按钮在各自栏内(左栏 « / 右栏 »), 宽度不可自由拖拽
  // 0.5.0 P4(2026-08-08 蒋先生最终决定): 删除窗口 tab 条 UI(多次位置调整
  // 均不满意)。窗口切换能力保留: 侧边栏左上角 PA 图标 → 主智能体对话;
  // 首页三场景按钮 → 场景会话; 侧边栏会话列表 → 历史会话。
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
          // 2026-08-10: 栏间空隙统一为显式数值, 不再依赖 flex gap 的隐式行为。
          // 中间区 wrapper 用 margin: "0 12px" 硬性定义左右各 12px 间隙,
          // 宽度由 flex 精确分配 = 100% − 左栏220 − 右栏300 − 左间隙12 − 右间隙12。
          // 任意窗口宽度下两侧间隙恒定 12px, 与设置页/对话页共用同一容器, 状态无关。
        }}
      >
        <Sidebar
          active={view}
          onChange={setView}
          currentSessionId={realSessionId ?? sessionId}
          onSwitchSession={handleSwitchSession}
          status={status}
          width={SIDEBAR_EXPANDED_WIDTH}
          onResumeSession={(sid) => resumeSession(sid)}
          theme={theme}
          toggleTheme={toggleTheme}
          agentName={agentName}
          onRenameAgent={(n) => renameAgent(n)}
          // P0-1(2026-08-17): 重连次数 + 手动重连入口
          reconnectCount={reconnectCount}
          onReconnect={() => connect()}
          // 0.5.0 P4: PA 图标点击 → 开启主智能体对话(改名已迁设置页)
          onOpenMonitor={() => void enterMonitorWindow()}
          // 0.5.0 P5: 主智能体是否有未关闭对话(PA 图标状态圆点)
          monitorActive={slotHasSession(0)}
        />
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            minHeight: 0,
            // 2026-08-10 21:25: 左右缝隙的真正来源 —— ArtifactPanel 实际渲染在
            // 本容器(中间区 wrapper)内部而非 App 渲染容器层! 实测 DOM 链:
            //   main → wrapper → App容器;  ArtifactPanel → wrapper → App容器
            // 故左侧缝隙来自本容器 margin-left(对 Sidebar), 右侧缝隙必须由
            // 本容器 gap 提供(main ↔ ArtifactPanel 之间), 之前 gap 加在 App
            // 容器层只修了左侧, 右侧 main 右缘直接顶在右栏左缘(重叠/无缝隙)。
            // 现在 margin:"0 12px"(左缝隙) + gap:12(右缝隙), 两侧对称 12px。
            margin: "0 12px",
            gap: 12,
          }}
        >
          <main style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex", flexDirection: "column" }}>
            {view === "home" && (
              // P3-3: HomeView 懒加载 Suspense 边界
              <Suspense
                fallback={
                  <div style={{ padding: 24, fontSize: 13, color: "var(--text-tertiary)" }}>加载中…</div>
                }
              >
                <HomeView
                  onPickMode={handlePickMode}
                  activeSkill={activeSkill}
                  sessionId={realSessionId ?? sessionId}
                  theme={theme}
                  // 0.5.0 P5: 场景按钮状态圆点(绿=对话中/红=无对话)
                  slotActive={(skill) =>
                    slotHasSession(
                      skill === "office" ? 1 : skill === "data_analysis" ? 2 : 3
                    )
                  }
                />
              </Suspense>
            )}
            {/* 0.5.0 P3/P4: 监控窗口(主智能体, 无场景 skill)
                —— 2026-08-08 蒋先生反馈: 用户要的是可对话的主智能体, 不是监控面板。
                故 PA 图标进入后渲染与场景会话一致的对话视图(chat), 顶部 tab 条 +
                消息列表 + 输入框。MonitorPanel 移除(监控工具由主智能体在对话中
                主动调用 system_metrics_query/system_status 完成分析)。 */}
            {view === "chat" && (
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
                    {/* 0.5.0 M1(2026-08-08): chip 仅显示场景中文名(用户要求两字);
                        测试用 getAllByText(场景名) 接受 sidebar 重复
                        0.5.0 P6(2026-08-09): 统一渲染后主智能体(无 skill)显示主智能体名 */}
                    {renderChatAssistantName()}
                  </span>
                  <span className="fs-11 text-tertiary">
                    session={realSessionId ?? sessionId}
                  </span>
                  {/* 2026-08-12 Phase 2: 选择技能(多技能调用) —— 原"切换技能"
                      改为挂载附加技能, 不新建会话 */}
                  <button
                    onClick={() => setSkillPickerOpen(true)}
                    title="选择附加技能(可多选, 与当前技能叠加使用)"
                    style={{
                      fontSize: 12, padding: "4px 12px", borderRadius: 10,
                      border: "1px solid var(--border-strong)", background: "var(--panel-bg-solid)", cursor: "pointer",
                      color: "var(--text-primary)",
                    }}
                  >
                    ➕ 选择技能
                  </button>
                  {/* 已挂载附加技能 chip(点击移除); P1-4: 窄窗口(<1100px)折叠为最多 2 个 + "+N" */}
                  {supplementarySkills
                    .slice(
                      0,
                      typeof window !== "undefined" && window.innerWidth < 1100 ? 2 : Infinity
                    )
                    .map((sn) => {
                      const info = availableSkills.find((s) => s.name === sn);
                      return (
                        <span
                          key={sn}
                          title={`移除附加技能 ${sn}`}
                          onClick={() => void removeSupplementarySkill(sn)}
                          style={{
                            fontSize: 12, padding: "4px 10px", borderRadius: 10,
                            border: "1px solid rgba(139,92,246,0.4)",
                            background: "rgba(139,92,246,0.08)",
                            color: "var(--accent-soft-text)",
                            cursor: "pointer", flexShrink: 0, whiteSpace: "nowrap",
                          }}
                        >
                          {info?.display_name || info?.name || sn} ×
                        </span>
                      );
                    })}
                  {typeof window !== "undefined" &&
                    window.innerWidth < 1100 &&
                    supplementarySkills.length > 2 && (
                      <span
                        title={`还有 ${supplementarySkills.length - 2} 个附加技能`}
                        style={{
                          fontSize: 12, padding: "4px 10px", borderRadius: 10,
                          border: "1px solid var(--border-color)",
                          background: "var(--chip-bg)",
                          color: "var(--text-secondary)",
                          flexShrink: 0, whiteSpace: "nowrap",
                        }}
                      >
                        +{supplementarySkills.length - 2}
                      </span>
                    )}
                  <span className="flex-1" />
                  {/* P1-4(2026-08-17): 设置/任务/关闭对话 收纳进「⋯ 更多」下拉
                      场景 chip + session + 选择技能 + 技能 chips 保留平铺 */}
                  <div style={{ position: "relative", flexShrink: 0 }}>
                    <button
                      onClick={() => setMoreMenuOpen((v) => !v)}
                      title="更多操作"
                      style={{
                        fontSize: 14, padding: "2px 10px", borderRadius: 10,
                        border: "1px solid var(--border-strong)", background: "var(--panel-bg-solid)", cursor: "pointer",
                        color: "var(--text-primary)", lineHeight: 1.6,
                      }}
                    >
                      ⋯
                    </button>
                    {moreMenuOpen && (
                      <>
                        {/* 点击外部关闭下拉 */}
                        <div
                          style={{ position: "fixed", inset: 0, zIndex: 59 }}
                          onClick={() => setMoreMenuOpen(false)}
                        />
                        <div
                          style={{
                            position: "absolute",
                            top: "100%",
                            right: 0,
                            marginTop: 6,
                            width: 190,
                            background: "var(--panel-bg-solid)",
                            border: "1px solid var(--border-color)",
                            borderRadius: 12,
                            boxShadow: "0 10px 30px rgba(15,23,42,0.15)",
                            padding: 6,
                            zIndex: 60,
                            display: "flex",
                            flexDirection: "column",
                            gap: 2,
                          }}
                        >
                        {/* V1.1-3.5 会话设置(记忆开关/截断/系统提示词) */}
                        <button
                          className="pop-menu-item"
                          onClick={() => {
                            setMoreMenuOpen(false);
                            void openSettings();
                          }}
                          title="会话设置(记忆/截断/系统提示词)"
                        >
                          <span className="flex-center gap-10">
                            <span className="icon-cell">⚙</span>
                            <span>会话设置</span>
                          </span>
                          <span style={{ fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1 }}>›</span>
                        </button>
                        {/* V1.1-3.8 任务状态(轮次/工具/错误/重试) */}
                        <button
                          className="pop-menu-item"
                          onClick={() => {
                            setMoreMenuOpen(false);
                            setTasksOpen(true);
                            void loadTasks();
                          }}
                          title="任务执行状态"
                        >
                          <span className="flex-center gap-10">
                            <span className="icon-cell">📋</span>
                            <span>任务状态</span>
                          </span>
                          <span style={{ fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1 }}>›</span>
                        </button>
                        {/* 0.5.0 P5: 关闭当前对话(主动结束 → 归档历史任务, 状态圆点转红) */}
                        <button
                          className="pop-menu-item"
                          onClick={() => {
                            setMoreMenuOpen(false);
                            setCloseConfirmOpen(true);
                          }}
                          title="关闭对话(归档至历史任务)"
                          style={{ color: "var(--danger-text)" }}
                        >
                          <span className="flex-center gap-10">
                            <span className="icon-cell">🗑</span>
                            <span>关闭对话</span>
                          </span>
                          <span style={{ fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1 }}>›</span>
                        </button>
                      </div>
                      </>
                    )}
                  </div>
                </div>
                {/* V1.5 项-4: 断点恢复横幅(interrupted 会话 + 存在 checkpoint) */}
                {resumeInfo?.resumable && !isGenerating && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      margin: "0 4px 8px",
                      padding: "8px 12px",
                      borderRadius: 10,
                      background: "var(--warning-bg)",
                      border: "1px solid var(--warning-border)",
                      fontSize: 12,
                      color: "var(--warning-text)",
                      flexShrink: 0,
                    }}
                  >
                    <span>⚠️ 该会话曾被中断
                      {resumeInfo.checkpoint_turn ? `(已完成至第 ${resumeInfo.checkpoint_turn} 轮)` : ""}，可断点继续</span>
                    <span className="flex-1" />
                    <button
                      onClick={() => resumeSession()}
                      style={{
                        fontSize: 12, padding: "4px 14px", borderRadius: 10,
                        border: "1px solid #e6a23c", background: "#f7a83b",
                        color: "var(--on-accent)", cursor: "pointer", fontWeight: 600,
                      }}
                    >
                      ▶ 断点继续
                    </button>
                  </div>
                )}
                <div
                  ref={chatScrollRef}
                  onScroll={handleChatScroll}
                  style={{
                    flex: 1,
                    minHeight: 0,
                    overflowY: "auto",
                    padding: 4,
                  }}
                >
        {/* 2026-08-16(阶段1-d, G2): monitor 窗口工具装配视图 —— 展示无涯
            实际装配的工具/MCP/工作区(通道收敛可感知) */}
        {activeSlot === 0 && !activeSkill && toolsAssembly && (
          <div
            style={{
              marginBottom: 12, borderRadius: 12,
              border: "1px solid var(--border-strong)",
              background: "var(--panel-bg-solid)",
              padding: 12,
            }}
          >
            <div className="subhead">
              🧰 工具装配
              <span style={{ fontSize: 11, color: "var(--text-tertiary)", marginLeft: 8, fontWeight: 400 }}>
                {toolsAssembly.workspace ? `工作区: ${toolsAssembly.workspace}` : "无工作区"}
              </span>
            </div>
            <div style={{ fontSize: 11, lineHeight: 1.7, color: "var(--text-secondary)" }}>
              <div>
                <b>MCP 装配</b>: {(toolsAssembly.mcp_servers ?? []).length > 0
                  ? (toolsAssembly.mcp_servers ?? []).join(", ")
                  : "(无, 走全量)"}
              </div>
              <div>
                <b>锚点工具</b>: {(toolsAssembly.anchor_tools ?? []).length > 0
                  ? (toolsAssembly.anchor_tools ?? []).join(", ")
                  : "(未配置)"}
              </div>
              {(toolsAssembly.monitor_tools ?? []).length > 0 && (
                <div>
                  <b>专属工具</b>: {(toolsAssembly.monitor_tools ?? []).join(", ")}
                </div>
              )}
            </div>
          </div>
        )}
        {turnGroups.length === 0 && (
          <div style={{ color: "var(--text-tertiary)", textAlign: "center", paddingTop: 40, lineHeight: 1.8 }}>
            {/* 0.5.0 P6(2026-08-09): 统一渲染后空态按角色区分 —— 主智能体显示监控引导 */}
            {activeSlot === 0 && !activeSkill ? (
              <>
                我是{agentName || "主智能体"} —— 负责系统监控与优化。
                <br />
                你可以问"查看系统性能"、"有哪些可优化点"、或让我分析最近指标。
              </>
            ) : (
              "发送一条消息开始对话"
            )}
          </div>
        )}
        {turnGroups.map(([turn, g]) => (
          // P3-2 批次2(2026-08-17): 消息渲染拆分为 TurnCard 组件
          <TurnCard
            key={turn}
            turn={turn}
            g={g}
            isLast={turn === turnGroups[turnGroups.length - 1][0]}
            isGenerating={isGenerating}
            thinkingOpen={openThinkingTurns.has(turn)}
            onToggleThinking={() => toggleThinking(turn)}
            deleted={deletedTurns.has(turn)}
            starred={starredTurns.has(turn)}
            expiredConfirmIds={expiredConfirmIds}
            completedAt={turnEndTimes.get(turn) ?? null}
            startedAt={turnStartTimes.get(turn) ?? null}
            durationMs={turnDurations.get(turn) ?? null}
            lastEventAt={lastEventAt}
            assistantName={renderChatAssistantName()}
            onRegenerate={() => regenerateTurn(turn)}
            onToggleStar={() => void toggleStar(turn)}
            onRequestDelete={() => requestDeleteTurn(turn)}
            onCopy={(t) => void copyText(t)}
            onCopyCode={(t) => void copyCodeBlocks(t)}
            onEditOriginal={(content) => {
              setEditingOriginal(content);
              setInput(content);
              inputRef.current?.focus();
            }}
            onConfirmAction={(id, approved) => {
              // 2026-08-18(请求卡片点不了): 点击即本地标记 —— 卡片按钮
              // 立即禁用并显示"已处理", 防重复点击/超时竞态
              markConfirmExpired(id);
              setPendingConfirm(null);
              sendWs({
                type: "tool_confirmation",
                session_id: realSessionId ?? sessionId,
                confirmation_id: id,
                approved,
              }, { userAction: true });
            }}
            onDeferAction={(id) => {
              markConfirmExpired(id);
              sendWs({
                type: "approval_defer",
                session_id: realSessionId ?? sessionId,
                confirmation_id: id,
              }, { userAction: true });
            }}
          />
        ))}

        {/* V1.5 项-1(ADR-012 §3.4 M3): 子任务卡片面板(委派状态可视化) */}
        <SubagentPanel
          subagents={subagents}
          onClearFinished={() => {
            setSubagents((prev) => {
              const next: Record<number, SubagentState> = {};
              for (const [k, v] of Object.entries(prev)) {
                if (
                  v.status === "succeeded" ||
                  v.status === "failed" ||
                  v.status === "cancelled"
                ) {
                  continue;
                }
                next[Number(k)] = v;
              }
              return next;
            });
          }}
        />
      </div>

      {/* 2026-08-08: 统一输入卡片 —— 附件区/输入框/底部操作行(工作区 + 更多 +
          模型选择 + 发送)整合为一张卡片; 各元素统一 40px 高度, 底部行窄屏可换行 */}
      <div
        style={{
          marginTop: 12,
          border: "1px solid var(--border-strong)",
          borderRadius: 14,
          background: "var(--panel-bg-solid)",
          padding: 8,
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        {/* 上传文档: 选择文件 → base64 上传后端 → 发送时附带文件路径 */}
        <input
          ref={fileInputRef}
          type="file"
          style={{ display: "none" }}
          onChange={(e) => void handleFilePick(e.target.files?.[0])}
        />

        {/* 附件区(待发送文件/图片 chip) */}
        {(pendingUpload || pendingImage) && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {pendingUpload && (
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 10px",
                  borderRadius: 16,
                  background: "var(--chip-bg)",
                  border: "1px solid var(--border-color)",
                  fontSize: 12,
                  color: "var(--text-primary)",
                }}
              >
                <span>📄</span>
                <span style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {pendingUpload.name}
                </span>
                <button
                  onClick={() => setPendingUpload(null)}
                  title="移除文件"
                  style={{
                    border: "none", background: "transparent", cursor: "pointer",
                    fontSize: 12, color: "var(--text-tertiary)", padding: 0, lineHeight: 1,
                  }}
                >
                  ×
                </button>
              </div>
            )}
            {pendingImage && (
              <div style={{ position: "relative", display: "inline-flex" }}>
                <img
                  src={pendingImage.preview}
                  alt="待发送图片"
                  style={{ height: 34, borderRadius: 10, border: "1px solid var(--border-color)" }}
                />
                <button
                  onClick={() => setPendingImage(null)}
                  title="移除图片"
                  style={{
                    position: "absolute", top: -6, right: -6,
                    width: 16, height: 16, borderRadius: "50%",
                    border: "none", background: "#d32f2f", color: "#fff",
                    fontSize: 10, lineHeight: 1, cursor: "pointer", padding: 0,
                  }}
                >
                  ×
                </button>
              </div>
            )}
          </div>
        )}

        {/* 2026-08-17(蒋先生反馈): 优化审批面板移到输入框上方 + 默认折叠 ——
            原在对话区顶部, 看完对话需上翻很多; 现固定在输入框上方(看完对话
            自然看到), 无待审批时仅一行低调横幅, 有待审批时自动展开 */}
        {activeSlot === 0 && (
          <div
            style={{
              marginBottom: 10, borderRadius: 12,
              border: optimPending > 0
                ? "1px solid #e6a23c"
                : "1px solid var(--border-color)",
              background: "var(--panel-bg-solid)",
              overflow: "hidden",
              // 2026-08-18(无涯 #3 补强): 防 flex 压缩 —— 窗口高度不足时
              // 面板被压缩会把内部滚动条裁出可视区(用户反馈"无滚动条"的另一诱因)
              flexShrink: 0,
            }}
          >
            <div
              onClick={() => setOptimOpen((v) => !v)}
              style={{
                display: "flex", alignItems: "center", gap: 8,
                padding: "8px 12px", cursor: "pointer", userSelect: "none",
              }}
            >
              <span className="fs-13 fw-600">
                ⚡ 优化审批
              </span>
              <span className="fs-11 text-tertiary">
                {optimBusy
                  ? "加载中…"
                  : optimError
                    ? "加载失败"
                    : optimPending > 0
                      ? `${optimPending} 条待审批`
                      : "暂无待审批"}
              </span>
              <span className="flex-1" />
              <span className="fs-11 text-tertiary">
                {optimOpen ? "▾ 收起" : "▸ 展开"}
              </span>
            </div>
            {optimOpen && (
              // 2026-08-18(无涯 #3 修复): maxHeight+overflowY auto —— 长提案
              // 内容超出时滚动条收进面板内部(原无 maxHeight, 外层 overflow:hidden
              // 直接裁剪超长内容, 按钮不可达)
              <div style={{ padding: "0 12px 12px", maxHeight: 320, overflowY: "auto" }}>
                {optimError ? (
                  <div style={{ fontSize: 12, color: "#e5484d" }}>
                    加载失败(检查后端是否运行/管理令牌是否已配置)。
                  </div>
                ) : optimPending === 0 ? (
                  <div className="fs-12 text-tertiary">
                    暂无待审批的优化建议。可让无涯分析系统状态后提交。
                  </div>
                ) : (
                  optimItems
                    .filter((i) => i.status === "pending")
                    .map((item) => (
                      <div
                        key={item.id}
                        style={{
                          padding: "8px 10px", marginBottom: 8, borderRadius: 8,
                          border: "1px solid var(--border-color)",
                          background: "var(--panel-bg)",
                          fontSize: 12,
                        }}
                      >
                        <div className="flex-center gap-8">
                          <span style={{ fontWeight: 600 }}>#{item.id}</span>
                          <span style={{
                            fontSize: 10, padding: "1px 6px", borderRadius: 6,
                            background: "rgba(139,92,246,0.12)", color: "var(--accent-soft-text)",
                          }}>
                            {item.category || "performance"}
                          </span>
                          <span style={{ flex: 1, color: "var(--text-tertiary)", fontSize: 11 }}>
                            {item.ts ? new Date(item.ts).toLocaleString("zh-CN") : ""}
                          </span>
                        </div>
                        <div style={{ marginTop: 4, lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
                          {item.proposal}
                        </div>
                        <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                          <button
                            onClick={() => void reviewOptim(item.id, "approved")}
                            style={{
                              fontSize: 11, padding: "3px 12px", borderRadius: 8,
                              border: "none", background: "#34c759", color: "#fff",
                              cursor: "pointer", fontWeight: 600,
                            }}
                          >
                            ✓ 批准执行
                          </button>
                          <button
                            onClick={() => void reviewOptim(item.id, "rejected")}
                            style={{
                              fontSize: 11, padding: "3px 12px", borderRadius: 8,
                              border: "none", background: "var(--border-color)",
                              color: "var(--text-secondary)", cursor: "pointer",
                            }}
                          >
                            ✕ 拒绝
                          </button>
                        </div>
                      </div>
                    ))
                )}
              </div>
            )}
          </div>
        )}

        {/* 消息输入框(2026-08-08 无框化: 无边框 + 透明背景, 融入输入卡片) */}
        {/* 2026-08-12 Phase 3: / 召唤技能浮层(absolute 悬浮输入区上方); 包裹 div
            承担原 textarea 的 flex:1 布局, textarea 内部保持自适应 */}
        <div style={{ position: "relative", flex: 1, display: "flex", minWidth: 0 }}>
          {slashOpen && slashFilteredSkills.length > 0 && (
            <div
              style={{
                position: "absolute",
                bottom: "calc(100% + 8px)",
                left: 0,
                right: 0,
                zIndex: 60,
                background: "var(--panel-bg-solid)",
                border: "1px solid var(--border-color)",
                borderRadius: 12,
                boxShadow: "0 12px 32px rgba(0,0,0,0.18)",
                padding: 6,
                maxHeight: 280,
                overflowY: "auto",
              }}
            >
              <div style={{ fontSize: 10, color: "var(--text-tertiary)", padding: "4px 8px 6px", letterSpacing: "0.05em" }}>
                / 召唤技能(Enter 挂载 · ↑↓ 导航 · Esc 关闭)
              </div>
              {slashFilteredSkills.map((s, i) => (
                <div
                  key={s.name}
                  onMouseEnter={() => setSlashIndex(i)}
                  onClick={() => insertSlashSkill(s.name)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "8px 10px",
                    borderRadius: 8,
                    cursor: "pointer",
                    background: i === slashIndex ? "rgba(139,92,246,0.1)" : "transparent",
                  }}
                >
                  <span style={{ flexShrink: 0, fontSize: 14 }}>{s.display_name || s.name}</span>
                  <span className="flex-1-min0">
                    <span style={{ display: "block", fontSize: 11, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {s.description || s.name}
                    </span>
                    <span style={{ display: "block", fontSize: 10, color: "var(--text-tertiary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {s.name} · {s.description ? "" : "通用"}
                    </span>
                  </span>
                </div>
              ))}
            </div>
          )}
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => {
              const val = e.target.value;
              setInput(val);
              // / 触发浮层(仅行首或空白后的 /, 排除 http:// 等路径)
              if (/(?:^|\s)\/[a-z0-9_]*$/.test(val) && availableSkills.some((s) => s.enabled)) {
                const m = /(?:^|\s)\/([a-z0-9_]*)$/.exec(val);
                setSlashOpen(true);
                setSlashQuery(m ? m[1].toLowerCase() : "");
                setSlashIndex(0);
              } else {
                setSlashOpen(false);
              }
            }}
            onPaste={handlePaste}
            onKeyDown={(e) => {
              // 2026-08-12 Phase 3: 浮层键盘导航(优先级高于 Enter 发送)
              if (slashOpen && slashFilteredSkills.length > 0) {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setSlashIndex((i) => Math.min(slashFilteredSkills.length - 1, i + 1));
                  return;
                }
                if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setSlashIndex((i) => Math.max(0, i - 1));
                  return;
                }
                if (e.key === "Enter") {
                  e.preventDefault();
                  insertSlashSkill(slashFilteredSkills[slashIndex].name);
                  return;
                }
                if (e.key === "Escape") {
                  e.preventDefault();
                  setSlashOpen(false);
                  return;
                }
              }
              // V1.1-3.4: Enter 发送, Shift+Enter 换行(多行输入)
              // 2026-08-19: 生成中禁止 Enter 发送(见 sendMessage 防御)
              if (e.key === "Enter" && !e.shiftKey && !isGenerating) {
                e.preventDefault();
                sendMessage();
              }
            }}
            placeholder={
              // P0-1(2026-08-17): 断线/重连时明确提示原因, 而非让用户从按钮变灰推断
              // 2026-08-19: 生成中提示不可发送(输入可预输入, 发送被防御拦截)
              isGenerating
                ? "正在生成中… 可先输入, 本轮结束后发送(Enter 暂不可用)"
                : status !== "connected"
                  ? "连接已断开，正在重连…"
                  : activeSlot === 0 && !activeSkill
                    ? `向${agentName || "主智能体"}提问(如: 查看系统性能)…`
                    : "输入消息,Enter 发送,Shift+Enter 换行,输入 / 可召唤技能"
            }
            rows={2}
            style={{
              flex: 1,
              padding: "8px 4px",
              borderRadius: 10,
              border: "none",
              fontSize: 14,
              outline: "none",
              resize: "vertical",
              minHeight: 40,
              maxHeight: 160,
            fontFamily: "inherit",
            lineHeight: 1.5,
            background: "transparent",
            color: "var(--text-primary)",
          }}
        />
        </div>

        {/* 2026-08-08: 原分隔线已移除(输入区与底部操作行无框化过渡) */}

        {/* 底部操作行: 工作区 / 更多 / 模型选择 / 发送(统一 34px, 窄屏自动换行) */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {/* 2026-08-08: 工作区图标(画地为牢) —— 点击弹出设置面板(原生目录选择器) */}
          <div style={{ position: "relative" }}>
            <button
              onClick={() => {
                setWorkspaceInput(workspace ?? "");
                setWorkspacePanelOpen((v) => !v);
              }}
              title={`工作区: ${workspace ?? "（默认工作目录）"}`}
              style={{
                width: 28,
                height: 28,
                borderRadius: 7,
                border: "none",
                backgroundColor: workspace ? "rgba(16,185,129,0.15)" : "var(--button-ghost-bg)",
                color: workspace ? "#10b981" : "var(--text-primary)",
                fontSize: 15,
                lineHeight: 1,
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                transition: "all 0.15s ease",
              }}
            >
              📁
            </button>
            {workspacePanelOpen && (
              <div
                style={{
                  position: "absolute",
                  bottom: "100%",
                  left: 0,
                  marginBottom: 8,
                  width: 320,
                  background: "var(--panel-bg-solid)",
                  border: "1px solid var(--border-color)",
                  borderRadius: 12,
                  boxShadow: "0 10px 30px rgba(15,23,42,0.15)",
                  padding: 12,
                  zIndex: 60,
                }}
              >
                <div style={{ fontSize: "var(--fs-body)", fontWeight: 600, marginBottom: 4 }}>工作区设置</div>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 8 }}>
                  画地为牢: agent 操作范围限定在该目录(留空 = 默认工作目录)
                </div>
                <div
                  style={{
                    display: "flex", alignItems: "center", gap: 8, marginBottom: 8,
                    padding: "6px 10px", borderRadius: 8,
                    background: "var(--chip-bg)", fontSize: 12, color: "var(--text-secondary)",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}
                  title={workspace ?? "（默认工作目录）"}
                >
                  📁 {workspace ?? "（默认工作目录）"}
                </div>
                <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
                  <button
                    onClick={() => void (async () => {
                      try {
                        const dir = await window.pa?.pickDirectory?.();
                        if (dir) setWorkspaceInput(dir);
                      } catch {
                        /* 非 Electron 环境忽略 */
                      }
                    })()}
                    title={window.pa?.pickDirectory ? "打开系统目录选择器" : "当前环境不支持目录选择器, 请手动输入路径"}
                    style={{
                      flex: 1, padding: "6px 10px", borderRadius: 8, cursor: "pointer",
                      border: "1px solid var(--border-strong)", background: "var(--button-ghost-bg)",
                      color: "var(--text-primary)", fontSize: 12,
                    }}
                  >
                    📂 选择目录
                  </button>
                </div>
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
                  placeholder="或手动输入目录路径, 如 D:/MyProject"
                  style={{
                    width: "100%", boxSizing: "border-box",
                    padding: "6px 10px", borderRadius: 8,
                    border: "1px solid var(--border-strong)",
                    fontSize: 12, outline: "none", marginBottom: 8,
                  }}
                />
                <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                  <button
                    onClick={() => void saveWorkspace()}
                    style={{ padding: "6px 14px", borderRadius: 8, border: "none", background: "#1976d2", color: "var(--on-accent)", fontSize: 12, cursor: "pointer" }}
                  >
                    保存
                  </button>
                  <button
                    onClick={() => setWorkspacePanelOpen(false)}
                    style={{ padding: "6px 12px", borderRadius: 8, border: "1px solid var(--border-strong)", background: "var(--button-ghost-bg)", color: "var(--text-secondary)", fontSize: 12, cursor: "pointer" }}
                  >
                    关闭
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* + 号工具菜单: 集成上传文件 + 提示词模板 */}
          <div style={{ position: "relative" }}>
          <button
            onClick={() => setPlusMenuOpen((v) => !v)}
            title="更多"
            style={{
              width: 28,
              height: 28,
              borderRadius: 7,
              border: "none",
              backgroundColor: plusMenuOpen ? "rgba(139,92,246,0.18)" : "var(--button-ghost-bg)",
              color: plusMenuOpen ? "#a78bfa" : "var(--text-primary)",
              fontSize: 17,
              lineHeight: 1,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              transition: "all 0.15s ease",
            }}
          >
            +
          </button>
          {plusMenuOpen && (
            <div
              style={{
                position: "absolute",
                bottom: "100%",
                left: 0,
                marginBottom: 8,
                background: "var(--panel-bg-solid)",
                border: "1px solid var(--border-color)",
                borderRadius: 12,
                boxShadow: "0 10px 30px rgba(15,23,42,0.18)",
                padding: 8,
                display: "flex",
                flexDirection: "column",
                gap: 2,
                width: 280,
                zIndex: 50,
              }}
            >
              {/* 2026-08-08: 重构为 IDE 风格专业菜单(参考截图):
                  每项左侧图标+文字, 右侧 → 箭头; 宽菜单、舒适间距,
                  hover 高亮加深, 为后续扩展子菜单(模式/连接器/功能等)留空间。 */}
              {[
                { icon: "📎", label: "添加文件", onClick: () => { fileInputRef.current?.click(); setPlusMenuOpen(false); } },
                { icon: "📋", label: "提示词模板", onClick: () => { setTemplatesOpen(true); setPlusMenuOpen(false); } },
              ].map((item) => (
                // P1-2(2026-08-17): JS hover → CSS 伪类(style="pop-menu-item")
                <button
                  key={item.label}
                  onClick={item.onClick}
                  className="pop-menu-item"
                >
                  <span className="flex-center gap-10">
                    <span className="icon-cell">{item.icon}</span>
                    <span>{item.label}</span>
                  </span>
                  <span style={{ fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1 }}>›</span>
                </button>
              ))}
            </div>
          )}
        </div>
        {/* 2026-08-08: 原左侧工具列/输入容器已并入统一输入卡片(上方) */}

        {/* 会话级模型选择: 自动(fallback 链) / 手动锁定单模型(2026-08-08 移入输入卡片) */}
        <select
          value={sessionModel}
          onChange={(e) => void changeSessionModel(e.target.value)}
          title="选择本会话使用的模型: 自动=fallback 链降级; 手动=全程使用所选模型"
          style={{
            height: 28,
            padding: "0 8px",
            borderRadius: 7,
            border: "none",
            background: "var(--input-bg)",
            color: "var(--text-primary)",
            fontSize: 12,
            outline: "none",
            flex: "1 1 110px",
            minWidth: 110,
            maxWidth: 200,
          }}
        >
          <option value="auto">🤖 自动(fallback 链)</option>
          {modelOptions.map((m) => (
            <option key={m} value={m}>
              {m}
              {sessionModel === m ? " ✓" : ""}
            </option>
          ))}
        </select>

        <span className="flex-1" />
        <button
          onClick={isGenerating ? stopGeneration : sendMessage}
          disabled={!isGenerating && (status !== "connected" || !input.trim())}
          title={isGenerating ? "停止生成(彻底终止当前轮次)" : "发送消息"}
          style={{
            height: 28,
            padding: "0 14px",
            borderRadius: 7,
            border: "none",
            backgroundColor: isGenerating
              ? "rgba(120,53,15,0.35)"
              : (status === "connected" && input.trim() ? "#1976d2" : "#bbb"),
            color: isGenerating ? "#fcd34d" : "#fff",
            fontSize: 12,
            fontWeight: 600,
            cursor: (status === "connected" || isGenerating) ? "pointer" : "not-allowed",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            transition: "all 0.15s var(--transition-smooth)",
          }}
        >
          {isGenerating ? "■ 停止" : "发送"}
        </button>
        </div>{/* 底部操作行闭合 */}
      </div>{/* 统一输入卡片闭合 */}
    </div>
  )}
          {/* P3-3(2026-08-17): 视图懒加载 Suspense 边界 */}
          <Suspense
            fallback={
              <div style={{ padding: 24, fontSize: 13, color: "var(--text-tertiary)" }}>加载中…</div>
            }
          >
            {view === "settings" && <SettingsView sessionId={realSessionId ?? sessionId} theme={theme} />}
            {view === "knowledge" && <KnowledgeView sessionId={realSessionId ?? sessionId} />}
            {view === "memory" && (
              <MemoryView
                sessionId={realSessionId ?? sessionId}
                // V1.5 规划项-8: 记忆来源跳转(切换会话, 有 skill 直达对话视图)
                onOpenSession={(sid) => handleSwitchSession(sid)}
              />
            )}
            {view === "agents" && <AgentLibraryView onActivate={(skill) => void handlePickMode(skill)} />}
          </Suspense>
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
                background: "var(--panel-bg-solid)",
                borderRadius: 14,
                padding: "20px 24px",
                boxShadow: "0 20px 60px rgba(15,23,42,0.25)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
                <span style={{ fontSize: "var(--fs-title)", fontWeight: 700, color: "var(--text-primary)" }}>
                  ⚙ 会话设置
                </span>
                <button
                  onClick={() => setSettingsOpen(false)}
                  className="icon-btn-lg"
                >
                  ×
                </button>
              </div>

              {/* 记忆开关 */}
              <div className="mb-16">
                <div className="subhead">会话记忆</div>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
                  <input type="checkbox" checked={memoryEnabled} onChange={() => void toggleMemory()} />
                  开启记忆(自动提取并注入长期记忆)
                </label>
              </div>

              {/* V1.3-7.2 自动执行 */}
              <div className="mb-16">
                <div className="subhead">自动执行(工作流)</div>
                <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={autoExec}
                    onChange={(e) => setAutoExec(e.target.checked)}
                  />
                  开启后,发一条消息自动连续执行多轮(无需逐条追问)
                </label>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
                  <span className="fs-12 text-tertiary">最多</span>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={autoRounds}
                    onChange={(e) => setAutoRounds(Number(e.target.value))}
                    disabled={!autoExec}
                    style={{
                      width: 70, padding: "4px 8px", borderRadius: 10,
                      border: "1px solid var(--border-strong)", fontSize: 13,
                    }}
                  />
                  <span className="fs-12 text-tertiary">轮</span>
                  <button
                    onClick={() => void saveAutoExec()}
                    style={{
                      fontSize: 12, padding: "5px 14px", borderRadius: 10,
                      border: "1px solid #6d28d9", background: "var(--accent-soft-bg)",
                      color: "#5b21b6", cursor: "pointer",
                    }}
                  >
                    保存
                  </button>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 6 }}>
                  每轮模型自动继续(直到任务完成或达到轮次上限); 可在任意时刻点"停止"
                </div>
              </div>

              {/* 上下文截断 */}
              <div className="mb-16">
                <div className="subhead">上下文截断</div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span className="fs-12 text-tertiary">保留 0 ~</span>
                  <input
                    type="number"
                    min={0}
                    value={truncateTurn}
                    onChange={(e) => setTruncateTurn(Number(e.target.value))}
                    style={{
                      width: 80, padding: "4px 8px", borderRadius: 10,
                      border: "1px solid var(--border-strong)", fontSize: 13,
                    }}
                  />
                  <span className="fs-12 text-tertiary">轮</span>
                  <button
                    onClick={() => void doTruncate()}
                    style={{
                      fontSize: 12, padding: "5px 14px", borderRadius: 10,
                      border: "1px solid #d97706", background: "var(--confirmation-bg)",
                      color: "#92400e", cursor: "pointer",
                    }}
                  >
                    截断
                  </button>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 6 }}>
                  之后的对话将移出模型上下文(软删除, 可从会话导出追溯)
                </div>
              </div>

              {/* 系统提示词 */}
              <div style={{ marginBottom: 8 }}>
                <div className="subhead">系统提示词</div>
                <button
                  onClick={() => void loadSystemPrompt()}
                  style={{
                    fontSize: 12, padding: "5px 14px", borderRadius: 10,
                    border: "1px solid var(--border-strong)", background: "var(--code-bg)",
                    // 2026-08-17: 硬编码深色 → 语义 token(暗色可读)
                    color: "var(--text-primary)", cursor: "pointer", marginBottom: 8,
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
                      border: "1px solid var(--border-color)", borderRadius: 10, padding: 10,
                      color: "var(--text-primary)", background: "var(--code-bg)",
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
                background: "var(--panel-bg-solid)",
                borderRadius: 14,
                padding: "18px 22px",
                boxShadow: "0 20px 60px rgba(15,23,42,0.25)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
                <span style={{ fontSize: 15, fontWeight: 700, color: "var(--text-primary)" }}>📋 提示词模板</span>
                <button
                  onClick={() => setTemplatesOpen(false)}
                  className="icon-btn-lg"
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
                    flex: 1, padding: "6px 10px", borderRadius: 10,
                    border: "1px solid var(--border-strong)", fontSize: 12,
                  }}
                />
                <button
                  onClick={saveCurrentAsTemplate}
                  style={{
                    fontSize: 12, padding: "6px 14px", borderRadius: 10,
                    border: "1px solid #6d28d9", background: "var(--accent-soft-bg)",
                    color: "#5b21b6", cursor: "pointer", whiteSpace: "nowrap",
                  }}
                >
                  + 保存当前输入
                </button>
              </div>

              {/* 模板列表 */}
              {templates.length === 0 && (
                <div style={{ fontSize: 12, color: "var(--text-tertiary)", textAlign: "center", padding: "16px 0" }}>
                  暂无模板。在输入区写好内容后点"+ 保存当前输入"。
                </div>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {templates.map((t) => (
                  <div
                    key={t.name}
                    style={{
                      display: "flex", alignItems: "center", gap: 8,
                      padding: "8px 10px", borderRadius: 10,
                      background: "var(--accent-soft-bg)", fontSize: 12,
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
                      <div style={{ fontSize: 10, color: "var(--text-tertiary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
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
                background: "var(--panel-bg-solid)",
                borderRadius: 14,
                padding: "20px 24px",
                boxShadow: "0 20px 60px rgba(15,23,42,0.25)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
                <span style={{ fontSize: "var(--fs-title)", fontWeight: 700 }}>
                  {ICON_TASKS} 任务执行状态
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
                  className="icon-btn-lg"
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
                    border: "1px solid var(--border-color)",
                    borderRadius: 10,
                    padding: 8,
                    marginBottom: 12,
                    background: "var(--code-bg)",
                  }}
                >
                  {eventsLoading && (
                    <div style={{ fontSize: 12, color: "var(--text-tertiary)", padding: 8 }}>加载中…</div>
                  )}
                  {!eventsLoading && (!eventsLog || eventsLog.length === 0) && (
                    <div style={{ fontSize: 12, color: "var(--text-tertiary)", padding: 8 }}>暂无事件记录</div>
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
                          color: "var(--text-tertiary)",
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
                              ? (errorCategoryColor(ev.summary) ?? "#dc2626")
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
                          // 2026-08-17: 硬编码深色 → 语义 token
                          color: "var(--text-secondary)",
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
                    border: "1px solid var(--border-color)",
                    borderRadius: 10,
                    padding: 12,
                    marginBottom: 12,
                    background: "var(--code-bg)",
                    display: "flex",
                    flexDirection: "column",
                    gap: 10,
                  }}
                >
                  {diagBusy && (
                    <div className="fs-12 text-tertiary">加载中…</div>
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
                            label: "缓存命中率",
                            value: usageData.cache_hit_rate != null
                              ? `${(usageData.cache_hit_rate * 100).toFixed(1)}%`
                              : "--",
                          },
                        ].map((s) => (
                          <div key={s.label} style={{ background: "var(--panel-bg-solid)", borderRadius: 10, padding: "8px 10px" }}>
                            <div className="fs-10 text-tertiary">{s.label}</div>
                            <div style={{ fontSize: "var(--fs-subtitle)", fontWeight: 700, color: "var(--text-primary)" }}>{s.value}</div>
                          </div>
                        ))}
                      </div>
                      <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 4 }}>
                        输入 {usageData.input_tokens.toLocaleString()} · 输出 {usageData.output_tokens.toLocaleString()}
                        {usageData.cached_tokens != null && (
                          <> · 缓存 {usageData.cached_tokens.toLocaleString()}</>
                        )}
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
                        <div className="fs-11 text-tertiary">暂无错误记录 🎉</div>
                      )}
                    </div>
                  )}
                  {!diagBusy && !usageData && !errorsData && (
                    <div className="fs-12 text-tertiary">暂无诊断数据</div>
                  )}
                </div>
              )}

              {!tasksData && !tasksLoading && (
                <div style={{ fontSize: 12, color: "var(--text-tertiary)", padding: "20px 0", textAlign: "center" }}>
                  暂无任务数据
                </div>
              )}

              {tasksData && tasksData.total_turns === 0 && (
                <div style={{ fontSize: 12, color: "var(--text-tertiary)", padding: "20px 0", textAlign: "center" }}>
                  还没有执行轮次
                </div>
              )}

              <div className="flex-col gap-8">
                {(tasksData?.turns ?? []).map((t) => {
                  const toolCalls = t.events.tool_call ?? 0;
                  const hasError = !!t.error;
                  return (
                    <div
                      key={t.turn}
                      style={{
                        border: "1px solid var(--border-color)",
                        borderRadius: 10,
                        padding: "10px 14px",
                        background: hasError ? "var(--error-bg)" : "var(--surface-1)",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <span style={{ fontSize: "var(--fs-body)", fontWeight: 700 }}>第 {t.turn} 轮</span>
                        {toolCalls > 0 && (
                          <span className="fs-11 text-tertiary">
                            🔧 工具调用 {toolCalls} 次
                          </span>
                        )}
                        {(t.events.thinking ?? 0) > 0 && (
                          <span className="fs-11 text-tertiary">
                            💭 推理 {(t.events.thinking ?? 0)} 段
                          </span>
                        )}
                        {(t.events.tool_result ?? 0) > 0 && (
                          <span className="fs-11 text-tertiary">
                            ✅ 结果 {(t.events.tool_result ?? 0)} 个
                          </span>
                        )}
                        {t.last_ts && (
                          <span style={{ fontSize: 10, color: "var(--text-tertiary)", marginLeft: "auto" }}>
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
        <ArtifactPanel
          open={artifactsOpen}
          artifacts={artifacts}
          onToggle={() => setArtifactsOpen(!artifactsOpen)}
          width={PANEL_EXPANDED_WIDTH}
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
                {activeSkill
                  ? `当前主技能「${renderChatAssistantName()}」已激活; 勾选附加技能后与主技能叠加使用(同一问题可调用多个技能)`
                  : "勾选技能作为附加能力, 与当前对话叠加使用"}
              </div>
              {(() => {
                // 2026-08-08: 按会话生效模型过滤 model_scope 限定技能;
                // auto=fallback 链时取第一个可用 provider 作为生效模型。
                const effectiveModel =
                  sessionModel === "auto" ? (modelOptions[0] ?? "") : sessionModel;
                const m = effectiveModel.toLowerCase();
                const matches = (scope?: string[]): boolean =>
                  !scope || scope.length === 0 || scope.some((s) => m.includes(s.toLowerCase()));
                const enabled = availableSkills.filter((s) => s.enabled);
                const dsSkills = enabled.filter((s) => matches(s.model_scope));
                const genSkills = enabled.filter((s) => !matches(s.model_scope));
                // 2026-08-16(§14 场景归类): 当前场景允许的类目过滤 ——
                // monitor(activeSlot 0)或场景技能由 scene_skills 常量决定;
                // 未锁定/无配置 = 全部允许(向后兼容)。
                const sceneKey =
                  activeSlot === 0 && !activeSkill ? "monitor" : activeSkill;
                const allowedCats =
                  (sceneKey && SCENE_ALLOWED_CATEGORIES[sceneKey]) || null;
                const inAllowed = (s: (typeof enabled)[number]): boolean =>
                  !allowedCats ||
                  allowedCats.includes((s.scenario ?? "").trim());
                const groups: { label: string; list: typeof enabled; locked: boolean }[] = [];
                if (dsSkills.length > 0)
                  groups.push({
                    label: `🎯 当前模型专属 (${effectiveModel || "auto"})`,
                    list: dsSkills, locked: false,
                  });
                if (genSkills.length > 0) groups.push({ label: "通用技能", list: genSkills, locked: false });
                // §14: 非当前场景类目技能单独分组(灰显, 不可勾选)
                const lockedSkills = enabled.filter((s) => !inAllowed(s));
                if (lockedSkills.length > 0)
                  groups.push({
                    label: `🔒 不属于当前场景 (${sceneDisplayName(sceneKey)})`,
                    list: lockedSkills, locked: true,
                  });
                return groups.map((g) => (
                  <div key={g.label} style={{ marginBottom: 10 }}>
                    <div style={{ fontSize: 11, color: "var(--text-tertiary)", margin: "4px 2px 6px" }}>
                      {g.label}
                    </div>
                    {g.list.map((s) => {
                      const isMain = activeSkill === s.name;
                      const checked = pickerSelected.includes(s.name);
                      const locked = g.locked || !inAllowed(s);
                      return (
                        <label
                          key={s.name}
                          style={{
                            display: "flex", alignItems: "center", gap: 10,
                            width: "100%", padding: "10px 14px", marginBottom: 8,
                            borderRadius: 10, border: "1px solid var(--border-strong)",
                            background: checked ? "rgba(139,92,246,0.08)" : "var(--panel-bg-solid)",
                            fontSize: 14,
                            cursor: isMain || locked ? "not-allowed" : "pointer",
                            textAlign: "left", opacity: locked ? 0.5 : 1,
                          }}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={isMain || locked}
                            onChange={() => {
                              if (isMain || locked) return;
                              setPickerSelected((prev) =>
                                prev.includes(s.name)
                                  ? prev.filter((n) => n !== s.name)
                                  : [...prev, s.name]
                              );
                            }}
                          />
                          <span className="flex-1-min0">
                            <span style={{ display: "block", fontWeight: 600 }}>
                              {s.name}
                              {isMain && (
                                <span style={{ fontSize: 10, marginLeft: 6, padding: "1px 6px", borderRadius: 8, background: "rgba(139,92,246,0.12)", color: "var(--accent-soft-text)" }}>
                                  主技能
                                </span>
                              )}
                              {s.scenario && (
                                <span style={{ fontSize: 10, marginLeft: 6, padding: "1px 6px", borderRadius: 8, background: "rgba(100,116,139,0.1)", color: "var(--text-tertiary)" }}>
                                  {s.scenario}
                                </span>
                              )}
                            </span>
                            {s.description && (
                              <span style={{ display: "block", fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>
                                {s.description}
                              </span>
                            )}
                          </span>
                          <span style={{ fontSize: 11, color: "var(--text-tertiary)", flexShrink: 0 }}>v{s.version}</span>
                        </label>
                      );
                    })}
                  </div>
                ));
              })()}
              {availableSkills.filter((s) => s.enabled).length === 0 && (
                <div style={{ fontSize: 13, color: "var(--text-tertiary)", padding: 16, textAlign: "center" }}>
                  暂无可用技能
                </div>
              )}
              <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                <button
                  onClick={() => {
                    const toAdd = pickerSelected.filter((n) => n !== activeSkill);
                    void addSupplementarySkills(toAdd);
                    setSkillPickerOpen(false);
                  }}
                  style={{
                    flex: 1, padding: "8px", borderRadius: 10, border: "none",
                    background: "var(--gradient-indigo)", color: "var(--on-accent)",
                    cursor: "pointer", fontSize: "var(--fs-body)", fontWeight: 600,
                  }}
                >
                  挂载选中技能
                </button>
                <button
                  onClick={() => setSkillPickerOpen(false)}
                  style={{ padding: "8px 20px", borderRadius: 10, border: "none", background: "var(--border-color)", cursor: "pointer", fontSize: 13 }}
                >
                  取消
                </button>
              </div>
            </div>
          </div>
        )}
        </div>
        {/* 0.5.1 A: 权限确认全局弹窗(置顶, 任何窗口可见) */}
        {pendingConfirm && (
          <div
            style={{
              position: "fixed",
              inset: 0,
              zIndex: 9999,
              background: "rgba(0,0,0,0.45)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
            onClick={() => {
              // 2026-08-16(蒋先生反馈"切换 LLM 后对话框锁定无法输入"):
              // 确认弹窗残留(超时/断连未收到清除事件)会以全屏遮罩永久覆盖
              // 输入区。点击遮罩背景 = 拒绝该确认并关闭(与"拒绝"按钮同语义,
              // 后端 60s 超时自动拒绝兜底, 此处仅解 UI 锁死)。
              sendWs({
                type: "tool_confirmation",
                session_id: pendingConfirm.session_id,
                confirmation_id: pendingConfirm.confirmation_id,
                approved: false,
              }, { userAction: true });
              setPendingConfirm(null);
            }}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                width: 460,
                maxWidth: "92vw",
                borderRadius: 16,
                background: "var(--panel-bg-solid)",
                border: "1px solid var(--border-strong)",
                boxShadow: "0 12px 48px rgba(0,0,0,0.35)",
                padding: 20,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 8,
                }}
              >
                <span style={{ fontSize: 15, fontWeight: 700 }}>
                  ⚠️ 需要你的确认
                </span>
                <span
                  style={{
                    fontSize: 11,
                    color: "var(--text-tertiary)",
                    marginLeft: "auto",
                  }}
                >
                  {confirmCountdown}s 后超时
                </span>
              </div>
              {/* 2026-08-15: 优先渲染人性化描述(title/summary), 老事件回退 message */}
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 600,
                  color: "var(--text-primary)",
                  marginBottom: 6,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {pendingConfirm.display?.title ?? pendingConfirm.message}
              </div>
              {pendingConfirm.display?.summary &&
                pendingConfirm.display.summary.length > 0 && (
                  <div
                    style={{
                      fontSize: 12,
                      color: "var(--text-secondary)",
                      marginBottom: 8,
                      lineHeight: 1.7,
                    }}
                  >
                    {pendingConfirm.display.summary.map((line, i) => (
                      <div key={i}>· {line}</div>
                    ))}
                  </div>
                )}
              {pendingConfirm.risk && (
                <div
                  style={{
                    fontSize: 11,
                    // 2026-08-17: 硬编码琥珀 → 语义 token
                    color: "var(--warning-text)",
                    marginBottom: 4,
                  }}
                >
                  风险级别:{" "}
                  {pendingConfirm.risk === "high"
                    ? "高"
                    : pendingConfirm.risk === "low"
                      ? "低"
                      : "中"}
                </div>
              )}
              {pendingConfirm.reason && (
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--text-tertiary)",
                    marginBottom: 4,
                  }}
                >
                  原因: {pendingConfirm.reason}
                </div>
              )}
              {pendingConfirm.argsPreview && (
                <details
                  style={{ margin: "0 0 8px", fontSize: 11 }}
                >
                  <summary
                    style={{ cursor: "pointer", color: "var(--text-tertiary)" }}
                  >
                    技术详情(原始参数)
                  </summary>
                  <pre
                    style={{
                      margin: "6px 0 0",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-all",
                      fontSize: 11,
                      // 2026-08-17: 硬编码琥珀 → 语义 token(亮暗双值)
                      color: "var(--warning-text)",
                      fontFamily: "Consolas, monospace",
                      background: "var(--panel-bg-hover)",
                      borderRadius: 8,
                      padding: 8,
                      maxHeight: 160,
                      overflow: "auto",
                    }}
                  >
                    {pendingConfirm.argsPreview}
                  </pre>
                </details>
              )}
              <div
                style={{
                  display: "flex",
                  gap: 8,
                  justifyContent: "flex-end",
                  marginTop: 14,
                }}
              >
                <button
                  onClick={() => {
                    // 2026-08-18: 点击即标记(卡片禁用), 防弹窗关闭后
                    // 内嵌卡片仍可重复操作
                    markConfirmExpired(pendingConfirm.confirmation_id);
                    sendWs({
                      type: "tool_confirmation",
                      session_id: pendingConfirm.session_id,
                      confirmation_id: pendingConfirm.confirmation_id,
                      approved: false,
                    }, { userAction: true });
                    setPendingConfirm(null);
                  }}
                  style={{
                    background: "var(--border-color)",
                    color: "var(--text-primary)",
                    border: "none",
                    borderRadius: 10,
                    padding: "6px 18px",
                    fontSize: 13,
                    cursor: "pointer",
                  }}
                >
                  拒绝
                </button>
                <button
                  onClick={() => {
                    markConfirmExpired(pendingConfirm.confirmation_id);
                    sendWs({
                      type: "tool_confirmation",
                      session_id: pendingConfirm.session_id,
                      confirmation_id: pendingConfirm.confirmation_id,
                      approved: true,
                    }, { userAction: true });
                    setPendingConfirm(null);
                  }}
                  style={{
                    background: "#16a34a",
                    color: "#fff",
                    border: "none",
                    borderRadius: 10,
                    padding: "6px 18px",
                    fontSize: 13,
                    cursor: "pointer",
                  }}
                >
                  同意执行
                </button>
              </div>
            </div>
          </div>
        )}

        {/* P0-3(2026-08-17): 全局 Toast 通知(右上角, 3s 自动消失) */}
        <ToastHost />

        {/* P0-3(2026-08-17): 玻璃确认弹层(替代 window.confirm) */}
        <ConfirmDialog
          open={closeConfirmOpen}
          title="关闭当前对话"
          body="将归档至历史任务, 可随时恢复。"
          confirmText="关闭"
          danger
          onConfirm={() => {
            setCloseConfirmOpen(false);
            closeWindow(activeSlot);
          }}
          onCancel={() => setCloseConfirmOpen(false)}
        />
        <ConfirmDialog
          open={deleteTurnConfirm !== null}
          title="删除这条回复?"
          body="软删除, 不影响会话其余内容。"
          confirmText="删除"
          danger
          onConfirm={() => {
            const turn = deleteTurnConfirm;
            setDeleteTurnConfirm(null);
            if (turn !== null) void deleteTurnMessage(turn);
          }}
          onCancel={() => setDeleteTurnConfirm(null)}
        />
        <ConfirmDialog
          open={truncateConfirm !== null}
          title="截断上下文"
          body={
            truncateConfirm !== null
              ? `保留 0~${truncateConfirm} 轮, 之后的对话将被移出上下文(可从会话导出追溯)。确定?`
              : ""
          }
          confirmText="截断"
          danger
          onConfirm={() => {
            const afterTurn = truncateConfirm;
            setTruncateConfirm(null);
            if (afterTurn !== null) void doTruncateConfirmed(afterTurn);
          }}
          onCancel={() => setTruncateConfirm(null)}
        />
        <ConfirmDialog
          open={iterLimitConfirm !== null}
          title="已达步数上限"
          body={
            iterLimitConfirm !== null
              ? `本轮已达步数上限(${iterLimitConfirm.used}/${iterLimitConfirm.max} 步)。\n\n长任务可继续执行(每次扩展 10 步)。是否继续?`
              : ""
          }
          confirmText="继续执行"
          cancelText="停止"
          onConfirm={() => {
            const c = iterLimitConfirm;
            setIterLimitConfirm(null);
            if (c) sendWs({ type: "continue_iteration", session_id: c.sid }, { userAction: true });
          }}
          onCancel={() => {
            const c = iterLimitConfirm;
            setIterLimitConfirm(null);
            if (c) sendWs({ type: "stop_iteration", session_id: c.sid }, { userAction: true });
          }}
        />
      </div>
    </div>
  );
}
