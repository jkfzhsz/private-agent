// P0-3(2026-08-17): 通用玻璃确认弹层
// 审计 I3: window.confirm(原生) → 与权限确认弹窗同风格的玻璃弹层。
// 样式对齐 App.tsx 权限确认弹窗(pendingConfirm): 半透明遮罩 + 面板 + 双按钮。
// 用法: <ConfirmDialog open={...} title="关闭对话" body="将归档至历史任务, 可随时恢复。"
//        confirmText="关闭" danger onConfirm={...} onCancel={...} />
import { useEffect } from "react";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  body?: string;
  confirmText?: string;
  cancelText?: string;
  /** 确认按钮红色(danger 操作) */
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  body,
  confirmText = "确认",
  cancelText = "取消",
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps): JSX.Element | null {
  // Esc 关闭(仅 open 时监听)
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        background: "rgba(0,0,0,0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 400,
          maxWidth: "92vw",
          borderRadius: 16,
          background: "var(--panel-bg-solid)",
          border: "1px solid var(--border-strong)",
          boxShadow: "0 12px 48px rgba(0,0,0,0.35)",
          padding: 20,
          animation: "flow-slide-up 0.25s var(--transition-smooth) both",
        }}
      >
        <div style={{ fontSize: 15, fontWeight: 700, color: "var(--text-primary)", marginBottom: 8 }}>
          {title}
        </div>
        {body && (
          <div
            style={{
              fontSize: 13,
              color: "var(--text-secondary)",
              lineHeight: 1.7,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              marginBottom: 16,
            }}
          >
            {body}
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button
            onClick={onCancel}
            style={{
              padding: "7px 16px",
              borderRadius: 10,
              border: "1px solid var(--border-color)",
              background: "var(--button-ghost-bg)",
              color: "var(--text-primary)",
              fontSize: 13,
              cursor: "pointer",
            }}
          >
            {cancelText}
          </button>
          <button
            onClick={onConfirm}
            style={{
              padding: "7px 16px",
              borderRadius: 10,
              border: "none",
              background: danger
                ? "linear-gradient(135deg, #f87171, #ef4444)"
                : "var(--gradient-indigo)",
              color: "var(--on-accent)",
              fontSize: 13,
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
