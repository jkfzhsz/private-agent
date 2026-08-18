// P3-2 批次1(2026-08-17): 从 App.tsx 拆出的工具函数与小组件
// 目标: App.tsx 5169 行 → ≤1500(P3-2 验收)。本批拆出渲染层工具:
// 场景名映射/图片路径解析/消息格式化/错误着色/系统通知/思考中/操作按钮。
import { memo, useEffect, useState } from "react";
import { deAIfy } from "./deAIfy";
import { ICON_THINKING } from "./icons";

// ──────────────────────────────────────────────────────────────────────────────
// 场景/路径工具
// ──────────────────────────────────────────────────────────────────────────────

// 0.5.0 M1(2026-08-08): 场景技术标识 → 中文名显示映射(与后端 skill.yaml
// scene_name 同步; activeSkill 存技术标识, 显示层映射为子瞻/白圭/清和)。
export const SCENE_NAME_MAP: Record<string, string> = {
  office: "子瞻",
  data_analysis: "白圭",
  frontend_design: "清和",
};

const IMAGE_PATH_RE = /(?:^|[^\w/])((?:\/?outputs\/)?[\w\-\u4e00-\u9fff]+\.(?:png|jpg|jpeg|gif|svg|webp))/gi;

const FILES_BASE = "http://127.0.0.1:8765/files/outputs";

export const sceneDisplayName = (skill: string | null): string => {
  if (!skill) return "未锁定场景";
  return SCENE_NAME_MAP[skill] ?? skill;
};

export function extractImagePaths(text: string): string[] {
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

export function imagePathToUrl(path: string): string {
  // 取 outputs/ 之后的部分作为 filename,拼接后端文件服务绝对地址
  // (vite 5173 下相对路径会请求前端自身导致 404)
  // 2026-08-10 22:00: [\w\-\.] → [\w\-\u4e00-\u9fff] 支持中文文件名
  const match = path.match(/outputs\/([\w\-\u4e00-\u9fff]+)$/i);
  const filename = match ? match[1] : path.replace(/^\/?outputs\//, "");
  return `${FILES_BASE}/${filename}`;
}

export function getSessionIdFromUrl(): number {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("session_id");
  if (raw) {
    const n = Number.parseInt(raw, 10);
    if (!Number.isNaN(n) && n > 0) return n;
  }
  // 首次连接生成一个随机 session_id(占位,实际由后端创建 session 后回传)
  return Math.floor(Math.random() * 100000) + 1;
}

// V1.4-8.4 系统通知(任务完成/失败): 仅应用在后台时提醒, 避免前台打扰
export function notifyUser(title: string, body: string): void {
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

// ──────────────────────────────────────────────────────────────────────────────
// 消息格式化
// ──────────────────────────────────────────────────────────────────────────────

export function formatPayload(eventType: string, payload: Record<string, unknown>): string {
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
      // 2026-08-17(P0-4/P1-1 实机修复): 不再预 deAIfy —— 保留原始 markdown,
      // 由 renderFinalText 在渲染层做"结构化 + 去 AI 味"(预剥离会导致表格/代码围栏
      // 在进入渲染管线前已被清除, 结构化渲染拿到的是纯文本)。
      return String(payload.content ?? "");
    case "delta":
      // delta 为流式增量, 保持 deAIfy(流式期间显示干净文本; 完成后 final 接管)
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

// 0.5.1(2026-08-10 蒋先生要求"错误零静默"): 错误分类着色 —— 按消息前缀
// 【设定问题】橙 / 【程序异常】红 / 【能力边界】蓝, 让用户一眼区分问题归因
export function errorCategoryColor(message?: unknown): string | null {
  const msg = String(message ?? "");
  if (msg.includes("【设定问题】")) return "#d97706";
  if (msg.includes("【程序异常】")) return "#dc2626";
  if (msg.includes("【能力边界】")) return "#2563eb";
  return null;
}

// ──────────────────────────────────────────────────────────────────────────────
// 渲染小组件
// ──────────────────────────────────────────────────────────────────────────────

// P1-2(2026-08-17): "思考中"等待指示 —— 三点跳动动画 + 已等待秒数 + >60s 重试提示
// 审计 I4: 原静态文案在长等待时界面僵死; 活性反馈 + 时间度量帮助用户判断是否卡死。
export function ThinkingWait(): JSX.Element {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => setSecs((s) => s + 1), 1000);
    return () => window.clearInterval(t);
  }, []);
  const dots = [0, 1, 2].map((i) => (
    <span
      key={i}
      style={{
        display: "inline-block",
        width: 3,
        height: 3,
        marginRight: 3,
        borderRadius: "50%",
        background: "currentColor",
        verticalAlign: "middle",
        animation: `thinking-bounce 1.2s ease-in-out ${i * 0.18}s infinite`,
      }}
    />
  ));
  return (
    <div style={{ color: "var(--text-tertiary)", fontSize: 13, lineHeight: 1.6 }}>
      <span style={{ verticalAlign: "middle" }}>{ICON_THINKING} 思考中</span>
      <span style={{ display: "inline-flex", verticalAlign: "middle", marginLeft: 4 }}>{dots}</span>
      <span style={{ marginLeft: 6 }}>已等待 {secs}s</span>
      {secs >= 60 && (
        <span style={{ color: "var(--warning-text)", marginLeft: 8 }}>
          · 已超 1 分钟，可点「停止」重试
        </span>
      )}
    </div>
  );
}

// V1.1-3.3 消息操作条按钮
// P3-1(2026-08-17): React.memo —— 长对话每次输入触发全量重渲染时,
// 操作条按钮 props 不变则跳过重渲染(默认浅比较, label/title/onClick 稳定)
export const MsgActionBtn = memo(function MsgActionBtn({
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
    // P1-2(2026-08-17): JS hover → CSS 伪类(.msg-action-btn), 硬编码色 → 语义 token
    <button
      onClick={onClick}
      title={title}
      className={`msg-action-btn${danger ? " danger" : ""}`}
    >
      {label}
    </button>
  );
});
