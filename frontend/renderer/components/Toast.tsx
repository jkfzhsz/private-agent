// P0-3(2026-08-17): 全局 Toast 通知
// 审计 I3: 各视图行内 error/uploadMsg 散装实现 → 统一右上角滑入, 3s 自动消失。
// 用法: import { toast } from "./Toast"; toast.success("已保存"); toast.error("失败");
//       <ToastHost /> 在 App 根部渲染一次。
import { useEffect, useState } from "react";

export type ToastKind = "success" | "error" | "info";

interface ToastItem {
  id: number;
  kind: ToastKind;
  text: string;
}

let nextId = 1;
let listeners: ((t: ToastItem) => void)[] = [];

function push(kind: ToastKind, text: string): void {
  const item = { id: nextId++, kind, text };
  for (const l of listeners) l(item);
}

export const toast = {
  success: (text: string) => push("success", text),
  error: (text: string) => push("error", text),
  info: (text: string) => push("info", text),
};

const KIND_META: Record<ToastKind, { bg: string; color: string; icon: string }> = {
  success: { bg: "var(--success-bg)", color: "var(--success-text)", icon: "✓" },
  error: { bg: "var(--error-bg)", color: "var(--danger-text)", icon: "✕" },
  info: { bg: "var(--info-bg)", color: "var(--info-text)", icon: "ℹ" },
};

export function ToastHost(): JSX.Element {
  const [items, setItems] = useState<ToastItem[]>([]);

  useEffect(() => {
    const handler = (t: ToastItem): void => {
      setItems((prev) => [...prev, t]);
      // 3s 自动消失
      window.setTimeout(() => {
        setItems((prev) => prev.filter((i) => i.id !== t.id));
      }, 3000);
    };
    listeners.push(handler);
    return () => {
      listeners = listeners.filter((l) => l !== handler);
    };
  }, []);

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        position: "fixed",
        top: 16,
        right: 16,
        zIndex: 10000,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        pointerEvents: "none",
      }}
    >
      {items.map((t) => {
        const meta = KIND_META[t.kind];
        return (
          <div
            key={t.id}
            style={{
              pointerEvents: "auto",
              display: "flex",
              alignItems: "center",
              gap: 8,
              maxWidth: 360,
              padding: "10px 14px",
              borderRadius: 12,
              background: meta.bg,
              color: meta.color,
              border: "1px solid var(--border-color)",
              boxShadow: "0 8px 24px rgba(15,23,42,0.12)",
              fontSize: 13,
              lineHeight: 1.5,
              animation: "toast-slide-in 0.3s var(--transition-smooth) both",
            }}
          >
            <span style={{ fontWeight: 700, flexShrink: 0 }}>{meta.icon}</span>
            <span style={{ wordBreak: "break-word" }}>{t.text}</span>
          </div>
        );
      })}
    </div>
  );
}
