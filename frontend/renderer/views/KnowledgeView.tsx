// Phase 1 Task 12 - 知识库视图
// 统计展示 + 文档上传(接 /admin/knowledge/stats + /admin/knowledge/upload)
import { useCallback, useEffect, useState } from "react";

const API_BASE = "http://localhost:8765/admin";

interface KbStats {
  total_documents: number;
  total_片段s: number;
  scenarios: Record<string, { docs: number; 片段s: number }>;
}

export default function KnowledgeView({
  sessionId,
}: {
  sessionId: number;
}): JSX.Element {
  const [stats, setStats] = useState<KbStats | null>(null);
  const [statsError, setStatsError] = useState("");
  const [filename, setFilename] = useState("");
  const [content, setContent] = useState("");
  const [scenario, setScenario] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const loadStats = useCallback(async (): Promise<void> => {
    try {
      const resp = await fetch(`${API_BASE}/knowledge/stats`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setStats(await resp.json());
      setStatsError("");
    } catch (e) {
      setStatsError(String(e));
    }
  }, []);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  const doUpload = async (): Promise<void> => {
    if (!filename.trim() || !content.trim()) {
      setUploadMsg({ ok: false, text: "请填写文件名和内容" });
      return;
    }
    setUploading(true);
    setUploadMsg(null);
    try {
      const params = new URLSearchParams({
        session_id: String(sessionId),
        filename: filename.trim(),
        content,
      });
      if (scenario.trim()) params.set("scenario", scenario.trim());
      const resp = await fetch(
        `${API_BASE}/knowledge/upload?${params.toString()}`,
        { method: "POST" }
      );
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setUploadMsg({
        ok: true,
        text: `上传成功: 文档 #${data.doc_id}, 生成 ${data.片段s} 个 片段`,
      });
      setContent("");
      setFilename("");
      void loadStats();
    } catch (e) {
      setUploadMsg({ ok: false, text: `上传失败: ${String(e)}` });
    } finally {
      setUploading(false);
    }
  };

  const scenarioEntries = stats ? Object.entries(stats.scenarios ?? {}) : [];

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16, overflowY: "auto", paddingRight: 4 }}>
      {/* 统计卡 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 12 }}>
        <div className="stat-card animate-in delay-1">
          <div>
            <div className="stat-label" style={{ fontSize: 12, color: "var(--text-tertiary)", fontWeight: 500 }}>
              文档总数
            </div>
            <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.03em", color: "var(--text-primary)" }}>
              {stats?.total_documents ?? "—"}
            </div>
          </div>
        </div>
        <div className="stat-card animate-in delay-2">
          <div>
            <div className="stat-label" style={{ fontSize: 12, color: "var(--text-tertiary)", fontWeight: 500 }}>
              片段总数
            </div>
            <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.03em", color: "var(--text-primary)" }}>
              {stats?.total_片段s ?? "—"}
            </div>
          </div>
        </div>
        <div className="stat-card animate-in delay-3">
          <div>
            <div className="stat-label" style={{ fontSize: 12, color: "var(--text-tertiary)", fontWeight: 500 }}>
              场景
            </div>
            <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.03em", color: "var(--text-primary)" }}>
              {scenarioEntries.length}
            </div>
          </div>
        </div>
      </div>

      {statsError && (
        <div style={{ fontSize: 12, color: "var(--danger-text)" }}>统计加载失败: {statsError}</div>
      )}

      <div className="glass-panel animate-in delay-2" style={{ padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em" }}>上传文档</span>
          <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>session={sessionId}</span>
        </div>
        <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
          <input
            className="flow-input"
            style={{ flex: 1 }}
            placeholder="文件名(如 report.md / data.csv)"
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
          />
          <input
            className="flow-input"
            style={{ width: 180 }}
            placeholder="场景(可选)"
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
          />
        </div>
        <textarea
          className="flow-input"
          style={{ width: "100%", minHeight: 120, resize: "vertical", fontFamily: "var(--font-mono)", fontSize: 13 }}
          placeholder="粘贴文档内容(支持 Markdown / 纯文本 / 代码)"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
          <button className="btn-primary" onClick={() => void doUpload()} disabled={uploading}>
            {uploading ? "上传中…" : "上传到知识库"}
          </button>
          {uploadMsg && (
            <span style={{ fontSize: 12, color: uploadMsg.ok ? "var(--success-text)" : "var(--danger-text)" }}>
              {uploadMsg.text}
            </span>
          )}
        </div>
      </div>

      {scenarioEntries.length > 0 && (
        <div className="glass-panel animate-in delay-3" style={{ padding: "20px 24px" }}>
          <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 16 }}>场景分布</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {scenarioEntries.map(([name, s]) => (
              <div
                key={name}
                style={{
                  display: "flex", alignItems: "center", gap: 12,
                  padding: "10px 14px", borderRadius: "var(--radius-sm)",
                  background: "rgba(255,255,255,0.5)",
                }}
              >
                <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{name}</span>
                <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  {s.docs} 文档 · {s.片段s} 片段
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
