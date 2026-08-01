// Phase 1.5 - ArtifactPanel 右侧产物预览栏
// 展示当前会话工具调用产生的图片/文件产物, 可展开收起
export interface Artifact {
  type: "image" | "file";
  url: string;
  name: string;
}

export default function ArtifactPanel({
  open,
  artifacts,
  onToggle,
}: {
  open: boolean;
  artifacts: Artifact[];
  onToggle: () => void;
}): JSX.Element {
  return (
    <aside
      className="glass-sidebar"
      style={{
        width: open ? 260 : 44,
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
            background: "rgba(255,255,255,0.5)",
            cursor: "pointer",
            color: "var(--text-secondary)",
            fontSize: 13,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexShrink: 0,
          }}
          title={open ? "收起产物栏" : "展开产物栏"}
        >
          {open ? "»" : "«"}
        </button>
        {open && (
          <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)", letterSpacing: "-0.02em" }}>
            产物
            {artifacts.length > 0 && (
              <span style={{ marginLeft: 6, fontSize: 11, color: "var(--text-tertiary)", fontWeight: 400 }}>
                {artifacts.length} 项
              </span>
            )}
          </span>
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
          产物预览
        </div>
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
                background: "rgba(255,255,255,0.5)",
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
