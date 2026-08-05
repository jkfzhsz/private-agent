// Phase 1 Task 12 + V1.2-6.4 - 知识库视图
// 库列表(scenario 分组/文档数/删除) + 文件上传入库 + 文本上传(保留)
import { useCallback, useEffect, useRef, useState } from "react";

import { adminFetch } from "../utils/apiClient";

const API_BASE = "http://127.0.0.1:8765/admin";

interface KbBase {
  scenario: string;
  documents: { doc_id: number; source: string; created_at: string | null }[];
  chunks: number;
}

interface KbOverview {
  total_documents: number;
  total_chunks: number;
  bases: KbBase[];
}

export default function KnowledgeView({
  sessionId,
}: {
  sessionId: number;
}): JSX.Element {
  const [kb, setKb] = useState<KbOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [filename, setFilename] = useState("");
  const [content, setContent] = useState("");
  const [scenario, setScenario] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError("");
    try {
      const resp = await adminFetch(`${API_BASE}/knowledge`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setKb(await resp.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // V1.2-6.4: 文件上传入库(base64 → 文本 → 切片向量化)
  const uploadFile = async (file: File | undefined | null): Promise<void> => {
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) {
      setUploadMsg({ ok: false, text: "文件超过 10MB 限制" });
      return;
    }
    setUploading(true);
    setUploadMsg(null);
    try {
      const buf = await file.arrayBuffer();
      let binary = "";
      const bytes = new Uint8Array(buf);
      const chunk = 0x8000;
      for (let i = 0; i < bytes.length; i += chunk) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
      }
      const b64 = btoa(binary);
      const body: Record<string, unknown> = {
        filename: file.name,
        content_base64: b64,
      };
      if (scenario.trim()) body.scenario = scenario.trim();
      const resp = await adminFetch(`${API_BASE}/knowledge/upload-file`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setUploadMsg({ ok: true, text: `文件入库: ${data.filename} → ${data.chunks} 个片段` });
      void load();
    } catch (e) {
      setUploadMsg({ ok: false, text: `上传失败: ${String(e)}` });
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  // 文本粘贴上传(保留)
  const doTextUpload = async (): Promise<void> => {
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
      const resp = await adminFetch(`${API_BASE}/knowledge/upload?${params.toString()}`, {
        method: "POST",
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setUploadMsg({ ok: true, text: `上传成功: 文档 #${data.doc_id}, 生成 ${data.chunks} 个片段` });
      setContent("");
      setFilename("");
      void load();
    } catch (e) {
      setUploadMsg({ ok: false, text: `上传失败: ${String(e)}` });
    } finally {
      setUploading(false);
    }
  };

  // V1.2-6.4: 删除库(软删)
  const removeBase = async (sc: string): Promise<void> => {
    if (!window.confirm(`删除知识库 "${sc}"? 其中全部文档将被移出检索(可追溯)。`)) return;
    try {
      const resp = await adminFetch(`${API_BASE}/knowledge/${encodeURIComponent(sc)}`, {
        method: "DELETE",
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setUploadMsg({ ok: true, text: `已删除库 ${sc}` });
      void load();
    } catch (e) {
      setUploadMsg({ ok: false, text: `删除失败: ${String(e)}` });
    }
  };

  // V1.3-7.3: 检索测试
  const [testQuery, setTestQuery] = useState("");
  const [testScenario, setTestScenario] = useState("");
  const [testTopK, setTestTopK] = useState(5);
  const [testResults, setTestResults] = useState<
    { chunk_id: number | null; text: string; score: number; source: string; doc_type: string }[]
  >([]);
  const [testing, setTesting] = useState(false);
  const [testMsg, setTestMsg] = useState<string | null>(null);

  const runSearchTest = async (): Promise<void> => {
    const query = testQuery.trim();
    if (!query) {
      setTestMsg("请输入检索查询");
      return;
    }
    setTesting(true);
    setTestMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/knowledge/search_test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          scenario: testScenario.trim() || undefined,
          top_k: Math.min(20, Math.max(1, testTopK)),
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setTestResults(data.results ?? []);
      if ((data.results ?? []).length === 0) setTestMsg("未命中(可降低 top_k 或检查库内容)");
    } catch (e) {
      setTestMsg(`检索失败: ${String(e)}`);
    } finally {
      setTesting(false);
    }
  };

  // V1.3-7.3: 切片配置 + 重索引
  const [chunking, setChunking] = useState<Record<string, { chunk_size: number; chunk_overlap: number }>>({});
  const [cfgLoading, setCfgLoading] = useState(false);
  const [cfgMsg, setCfgMsg] = useState<string | null>(null);
  const [reindexing, setReindexing] = useState<string | null>(null);

  const loadConfig = useCallback(async (): Promise<void> => {
    setCfgLoading(true);
    try {
      const resp = await adminFetch(`${API_BASE}/knowledge/config`);
      if (resp.ok) {
        const data = await resp.json();
        setChunking(data.chunking ?? {});
      }
    } catch {
      /* 静默 */
    } finally {
      setCfgLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  const saveConfig = async (): Promise<void> => {
    setCfgMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/knowledge/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chunking }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setCfgMsg("切片配置已保存(下次上传/重索引生效)");
    } catch (e) {
      setCfgMsg(`保存失败: ${String(e)}`);
    }
  };

  const doReindex = async (sc: string): Promise<void> => {
    if (!window.confirm(`重索引 "${sc}": 全部片段将按当前切片配置重新切分+向量化。确定?`)) return;
    setReindexing(sc);
    setCfgMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/knowledge/reindex`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: sc }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setCfgMsg(`重索引完成: ${data.documents} 文档 → ${data.chunks} 片段`);
      void load();
    } catch (e) {
      setCfgMsg(`重索引失败: ${String(e)}`);
    } finally {
      setReindexing(null);
    }
  };

  const chunkNum = (dt: string, key: "chunk_size" | "chunk_overlap", v: number): void => {
    setChunking((prev) => ({
      ...prev,
      [dt]: { ...(prev[dt] ?? { chunk_size: 400, chunk_overlap: 50 }), [key]: Number(v) || 0 },
    }));
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16, overflowY: "auto", paddingRight: 4 }}>
      {/* 统计卡 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 12 }}>
        <div className="stat-card animate-in delay-1">
          <div>
            <div className="stat-label" style={{ fontSize: 12, color: "var(--text-tertiary)", fontWeight: 500 }}>知识库</div>
            <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.03em", color: "var(--text-primary)" }}>
              {kb?.bases?.length ?? "—"}
            </div>
          </div>
        </div>
        <div className="stat-card animate-in delay-2">
          <div>
            <div className="stat-label" style={{ fontSize: 12, color: "var(--text-tertiary)", fontWeight: 500 }}>文档总数</div>
            <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.03em", color: "var(--text-primary)" }}>
              {kb?.total_documents ?? "—"}
            </div>
          </div>
        </div>
        <div className="stat-card animate-in delay-3">
          <div>
            <div className="stat-label" style={{ fontSize: 12, color: "var(--text-tertiary)", fontWeight: 500 }}>片段总数</div>
            <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.03em", color: "var(--text-primary)" }}>
              {kb?.total_chunks ?? "—"}
            </div>
          </div>
        </div>
      </div>

      {error && <div style={{ fontSize: 12, color: "var(--danger-text)" }}>加载失败: {error}</div>}
      {uploadMsg && (
        <div style={{ fontSize: 12, color: uploadMsg.ok ? "var(--success-text)" : "var(--danger-text)" }}>
          {uploadMsg.text}
        </div>
      )}

      {/* V1.2-6.4: 上传区(文件 + 文本) */}
      <div className="glass-panel animate-in delay-2" style={{ padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
          <span style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em" }}>上传文档</span>
          <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>session={sessionId}</span>
        </div>
        <div style={{ display: "flex", gap: 12, marginBottom: 12, alignItems: "center", flexWrap: "wrap" }}>
          <label
            style={{
              fontSize: 13, fontWeight: 600, cursor: "pointer",
              padding: "8px 16px", borderRadius: 8,
              background: "var(--gradient-indigo)", color: "#fff",
            }}
          >
            📄 上传文件(txt/md/csv/代码等)
            <input
              ref={fileRef}
              type="file"
              style={{ display: "none" }}
              onChange={(e) => void uploadFile(e.target.files?.[0])}
            />
          </label>
          <input
            className="flow-input"
            style={{ width: 200 }}
            placeholder="目标知识库(可选, 如 office)"
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
          />
        </div>
        <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 8 }}>
          文本文件直接入库(utf-8/gbk); PDF 等二进制请先转文本
        </div>
        <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
          <input
            className="flow-input"
            style={{ flex: 1 }}
            placeholder="文件名(如 report.md / data.csv)"
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
          />
        </div>
        <textarea
          className="flow-input"
          style={{ width: "100%", minHeight: 100, resize: "vertical", fontFamily: "var(--font-mono)", fontSize: 13 }}
          placeholder="粘贴文档内容(支持 Markdown / 纯文本 / 代码)"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
          <button className="btn-primary" onClick={() => void doTextUpload()} disabled={uploading}>
            {uploading ? "上传中…" : "上传到知识库"}
          </button>
        </div>
      </div>

      {/* V1.2-6.4: 库列表 */}
      <div className="glass-panel animate-in delay-3" style={{ padding: "20px 24px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 16 }}>
          知识库列表
        </div>
        {!loading && (!kb || kb.bases.length === 0) && (
          <div style={{ fontSize: 13, color: "var(--text-tertiary)", padding: "20px 0", textAlign: "center" }}>
            暂无知识库。上传文档后按场景自动分组显示。
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {(kb?.bases ?? []).map((b) => (
            <div
              key={b.scenario}
              style={{
                display: "flex", alignItems: "center", gap: 12,
                padding: "12px 14px", borderRadius: "var(--radius-sm)",
                background: "rgba(255,255,255,0.5)",
              }}
            >
              <span style={{ fontSize: 15 }}>📚</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{b.scenario}</div>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                  {b.documents.length} 文档 · {b.chunks} 片段
                </div>
              </div>
              <div
                style={{
                  flexShrink: 0,
                  fontSize: 10, color: "var(--text-tertiary)",
                  maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}
                title={b.documents.map((d) => d.source).join(", ")}
              >
                {b.documents.map((d) => d.source).join(", ") || "—"}
              </div>
              <button
                onClick={() => void doReindex(b.scenario)}
                title="按当前切片配置重切分+重向量化"
                disabled={reindexing === b.scenario}
                style={{
                  flexShrink: 0, fontSize: 12, padding: "4px 10px",
                  border: "1px solid rgba(109,40,217,0.3)", borderRadius: 6,
                  background: "rgba(237,233,254,0.6)", color: "#5b21b6",
                  cursor: "pointer",
                }}
              >
                {reindexing === b.scenario ? "重索引中…" : "🔄 重索引"}
              </button>
              <button
                onClick={() => void removeBase(b.scenario)}
                title="删除此知识库(软删)"
                style={{
                  flexShrink: 0, fontSize: 12, padding: "4px 10px",
                  border: "1px solid rgba(220,38,38,0.3)", borderRadius: 6,
                  background: "rgba(254,226,226,0.4)", color: "#dc2626",
                  cursor: "pointer",
                }}
              >
                🗑 删除
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* V1.3-7.3: 检索测试 */}
      <div className="glass-panel animate-in delay-3" style={{ padding: "20px 24px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 12 }}>
          检索测试
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
          <input
            className="flow-input"
            style={{ flex: 1, minWidth: 200 }}
            placeholder="输入查询, 如: 量子比特"
            value={testQuery}
            onChange={(e) => setTestQuery(e.target.value)}
          />
          <input
            className="flow-input"
            style={{ width: 140 }}
            placeholder="库(可选)"
            value={testScenario}
            onChange={(e) => setTestScenario(e.target.value)}
            list="kb-scenarios"
          />
          <datalist id="kb-scenarios">
            {(kb?.bases ?? []).map((b) => (
              <option key={b.scenario} value={b.scenario} />
            ))}
          </datalist>
          <input
            type="number"
            min={1}
            max={20}
            value={testTopK}
            onChange={(e) => setTestTopK(Number(e.target.value))}
            title="top_k"
            style={{
              width: 60, padding: "6px 8px", borderRadius: 6, fontSize: 12,
              border: "1px solid rgba(148,163,184,0.3)", background: "rgba(255,255,255,0.6)",
            }}
          />
          <button className="btn-primary" onClick={() => void runSearchTest()} disabled={testing}>
            {testing ? "检索中…" : "🔍 检索测试"}
          </button>
        </div>
        {testMsg && <div style={{ fontSize: 12, color: testMsg.startsWith("失败") ? "#dc2626" : "#059669", marginBottom: 8 }}>{testMsg}</div>}
        {testResults.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {testResults.map((r, i) => (
              <div
                key={`${r.chunk_id ?? i}-${i}`}
                style={{
                  padding: "10px 12px", borderRadius: 8,
                  background: "rgba(255,255,255,0.5)", fontSize: 12,
                }}
              >
                <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
                  <span style={{ fontSize: 11, fontWeight: 600, color: "#5b21b6" }}>score {r.score}</span>
                  <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{r.source} · {r.doc_type}</span>
                </div>
                <div style={{ color: "var(--text-primary)", lineHeight: 1.6, wordBreak: "break-word" }}>{r.text}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* V1.3-7.3: 切片配置 */}
      <div className="glass-panel animate-in delay-3" style={{ padding: "20px 24px" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em" }}>切片参数</div>
          <button
            className="btn-primary"
            style={{ padding: "6px 16px", fontSize: 12 }}
            onClick={() => void saveConfig()}
            disabled={cfgLoading}
          >
            保存配置
          </button>
        </div>
        {cfgMsg && <div style={{ fontSize: 12, color: cfgMsg.startsWith("失败") ? "#dc2626" : "#059669", marginBottom: 8 }}>{cfgMsg}</div>}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
          {Object.keys(chunking).map((dt) => (
            <div
              key={dt}
              style={{
                padding: "10px 12px", borderRadius: 8,
                background: "rgba(255,255,255,0.5)", fontSize: 12,
                display: "flex", flexDirection: "column", gap: 6,
              }}
            >
              <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{dt}</span>
              <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
                size
                <input
                  type="number"
                  min={1}
                  value={String(chunking[dt]?.chunk_size ?? "")}
                  onChange={(e) => chunkNum(dt, "chunk_size", Number(e.target.value))}
                  style={{
                    width: 64, padding: "3px 6px", borderRadius: 6, fontSize: 12,
                    border: "1px solid rgba(148,163,184,0.3)",
                  }}
                />
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
                overlap
                <input
                  type="number"
                  min={0}
                  value={String(chunking[dt]?.chunk_overlap ?? "")}
                  onChange={(e) => chunkNum(dt, "chunk_overlap", Number(e.target.value))}
                  style={{
                    width: 64, padding: "3px 6px", borderRadius: 6, fontSize: 12,
                    border: "1px solid rgba(148,163,184,0.3)",
                  }}
                />
              </label>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
