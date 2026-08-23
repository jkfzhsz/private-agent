// Phase 1.5 - ArtifactPanel 右侧产物预览栏(V1.1 改造: 双 Tab)
// Tab1 "产物": 当前会话工具调用产生的图片/文件
// Tab2 "文件": 工作区文件浏览(FilePanel embedded, 不占用对话界面)
import { useState } from "react";

import FilePanel from "./FilePanel";

// 2026-08-10: 右侧面板固定宽度两态(展开/折叠), 取消外部拖拽调宽
// 与左栏 Sidebar 对称: 折叠宽度均为 44px
// 2026-08-10 21:15: 展开宽度 300→280 —— 蒋先生反馈右栏过宽, 收窄后
// 中间区随之变宽 20px, 与右栏之间的视觉空间更开阔(几何缝隙保持 12px)
export const PANEL_EXPANDED_WIDTH = 280;
export const PANEL_COLLAPSED_WIDTH = 44;

export interface Artifact {
  type: "image" | "file";
  url: string;
  name: string;
}

export default function ArtifactPanel({
  open,
  artifacts,
  onToggle,
  width = PANEL_EXPANDED_WIDTH,
}: {
  open: boolean;
  artifacts: Artifact[];
  onToggle: () => void;
  /** 2026-08-10: 展开宽度由 App 以固定常量传入(不可拖拽); 折叠为 PANEL_COLLAPSED_WIDTH */
  width?: number;
}): JSX.Element {
  const [tab, setTab] = useState<"artifacts" | "files">("artifacts");

  return (
    <aside
      className="glass-sidebar"
      style={{
        width: open ? width : PANEL_COLLAPSED_WIDTH,
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        height: "100%",
        boxSizing: "border-box",
        padding: open ? "24px 12px 16px" : "20px 8px 16px",
        transition: "width 0.3s var(--transition-smooth), padding 0.3s var(--transition-smooth)",
        overflow: "hidden",
      }}
    >
      {/* 折叠控制条: 按钮置于左侧(与左栏按钮镜像对称, 同一水平高度) */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          minHeight: 28,
          marginBottom: open ? 12 : 12,
          whiteSpace: "nowrap",
        }}
      >
        <button
          onClick={onToggle}
          style={{
            width: 28,
            height: 28,
            borderRadius: 8,
            border: "1px solid rgba(148,163,184,0.15)",
            background: "var(--panel-bg)",
            cursor: "pointer",
            color: "var(--text-secondary)",
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
          title={open ? "收起侧栏" : "展开侧栏"}
        >
          {open ? "»" : "«"}
        </button>
        {open && (
          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            {(
              [
                { key: "artifacts", label: "产物" },
                { key: "files", label: "文件" },
              ] as const
            ).map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                style={{
                  fontSize: 12,
                  padding: "3px 12px",
                  borderRadius: 20,
                  border: "1px solid",
                  borderColor: tab === t.key ? "rgba(139,92,246,0.5)" : "rgba(148,163,184,0.3)",
                  background: tab === t.key ? "rgba(139,92,246,0.1)" : "transparent",
                  // 2026-08-08: 激活态用 var(--text-primary)(亮色深/暗色浅)而非硬编码深紫;
                  // 暗色主题下深紫字在紫色背景上对比度极低, 看起来像"残留黑字"
                  color: tab === t.key ? "var(--text-primary)" : "var(--text-tertiary)",
                  fontWeight: tab === t.key ? 600 : 400,
                  cursor: "pointer",
                }}
              >
                {t.label}
                {t.key === "artifacts" && artifacts.length > 0 && (
                  <span style={{ marginLeft: 4, fontSize: 10 }}>{artifacts.length}</span>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {!open ? (
        <div
          style={{
            writingMode: "vertical-rl",
            fontSize: 12,
            color: "var(--text-tertiary)",
            margin: "auto 0",
            letterSpacing: "0.1em",
          }}
        >
          产物 / 文件
        </div>
      ) : tab === "files" ? (
        /* V1.1 反馈①: 工作区文件并入产物栏, 不占用对话界面 */
        <FilePanel embedded />
      ) : (
        <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 10 }}>
          {artifacts.length === 0 && (
            <div style={{ fontSize: 12, color: "var(--text-tertiary)", textAlign: "center", padding: "24px 0", lineHeight: 1.7 }}>
              暂无产物
              <br />
              运行工具后生成的图片 / 文件会显示在这里
            </div>
          )}
          {artifacts.map((a) => (
            <div
              key={a.url}
              style={{
                borderRadius: "var(--radius-sm)",
                background: "var(--panel-bg)",
                overflow: "hidden",
                border: "1px solid rgba(148,163,184,0.12)",
              }}
            >
              {a.type === "image" ? (
                <img
                  src={a.url}
                  alt={a.name}
                  style={{ width: "100%", display: "block", aspectRatio: "4/3", objectFit: "cover" }}
                />
              ) : (
                <div
                  style={{
                    padding: "10px 12px",
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    fontSize: 12,
                    color: "var(--text-primary)",
                  }}
                >
                  <span style={{ flexShrink: 0 }}>📄</span>
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {a.name}
                  </span>
                </div>
              )}
              <a
                href={a.url}
                download={a.name}
                style={{
                  display: "block",
                  textAlign: "center",
                  fontSize: 11,
                  color: "#8b5cf6",
                  padding: "6px 0",
                  textDecoration: "none",
                  borderTop: "1px solid rgba(148,163,184,0.12)",
                }}
              >
                下载 {a.name}
              </a>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}
