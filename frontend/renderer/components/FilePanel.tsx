// V1.1-3.7 文件管理闭环 - 工作区文件面板
// 树形浏览 + 文本预览 + 新建/重命名/删除/下载/上传
import { useCallback, useEffect, useState } from "react";

import { adminFetch } from "../utils/apiClient";

const API_BASE = "http://127.0.0.1:8765/admin";

interface TreeNode {
  name: string;
  path: string;
  type: "dir" | "file";
  size?: number;
  children?: TreeNode[];
}

export default function FilePanel({ embedded = false }: { embedded?: boolean }): JSX.Element {
  const [tree, setTree] = useState<TreeNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ path: string; type: string; content?: string; size?: number } | null>(null);
  const [activePath, setActivePath] = useState<string>(""); // 当前操作目录(新建文件的目标位置)

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError("");
    try {
      const resp = await adminFetch(`${API_BASE}/files/tree`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setTree(data.tree);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleDir = (path: string): void => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const openFile = async (path: string): Promise<void> => {
    setSelectedPath(path);
    setActivePath(path.split("/").slice(0, -1).join("/"));
    try {
      const resp = await adminFetch(`${API_BASE}/files/content?path=${encodeURIComponent(path)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setPreview(await resp.json());
    } catch (e) {
      setPreview({ path, type: "text", content: `加载失败: ${String(e)}` });
    }
  };

  const newFolder = async (): Promise<void> => {
    const dir = activePath || "";
    const name = window.prompt("新建文件夹名", "新文件夹");
    if (!name) return;
    const full = dir ? `${dir}/${name}` : name;
    try {
      const resp = await adminFetch(`${API_BASE}/files/mkdir`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: full }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      if (dir) setExpanded((prev) => new Set(prev).add(dir));
      void load();
    } catch (e) {
      setError(`新建失败: ${String(e)}`);
    }
  };

  const rename = async (path: string): Promise<void> => {
    const name = path.split("/").pop() ?? "";
    const next = window.prompt("重命名为(可含路径)", name);
    if (!next) return;
    try {
      const resp = await adminFetch(`${API_BASE}/files/rename`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, to_path: next }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      void load();
    } catch (e) {
      setError(`重命名失败: ${String(e)}`);
    }
  };

  const remove = async (path: string): Promise<void> => {
    if (!window.confirm(`删除 ${path}? (仅文件或空目录可删)`)) return;
    try {
      const resp = await adminFetch(`${API_BASE}/files/delete?path=${encodeURIComponent(path)}`, {
        method: "DELETE",
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.error ?? `HTTP ${resp.status}`);
      }
      void load();
    } catch (e) {
      setError(`删除失败: ${String(e)}`);
    }
  };

  const download = (path: string): void => {
    window.open(`${API_BASE}/files/download?path=${encodeURIComponent(path)}`, "_blank");
  };

  // V1.3-7.4: 解压压缩包(工作区内 zip/tar.gz/tgz)
  const extractArchive = async (path: string): Promise<void> => {
    const toDir = window.prompt(
      `解压 "${path}" 到目标目录(留空 = 压缩包所在目录):`,
      path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : ""
    );
    if (toDir === null) return;
    setError("");
    try {
      const resp = await adminFetch(`${API_BASE}/files/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archive: path, to_dir: toDir.trim() || undefined }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setError(`解压完成: ${data.extracted} 个文件`);
      void load();
    } catch (e) {
      setError(`解压失败: ${String(e)}`);
    }
  };

  // V1.3-7.4: 批量打包下载(整个工作区)
  const downloadAllZip = (): void => {
    window.open(`${API_BASE}/files/download_zip?paths=.&name=workspace`, "_blank");
  };

  const uploadFile = async (file: File | undefined | null): Promise<void> => {
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
      const resp = await adminFetch(`${API_BASE}/files/upload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, content_base64: b64 }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      void load();
    } catch (e) {
      setError(`上传失败: ${String(e)}`);
    }
  };

  const renderNode = (node: TreeNode, depth: number): JSX.Element => {
    const isDir = node.type === "dir";
    const isExpanded = expanded.has(node.path);
    const isSelected = selectedPath === node.path;
    return (
      <div key={node.path || "/"}>
        <div
          onClick={() => (isDir ? toggleDir(node.path) : void openFile(node.path))}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "4px 6px",
            paddingLeft: 8 + depth * 14,
            borderRadius: 6,
            cursor: "pointer",
            fontSize: 12,
            color: isSelected ? "var(--text-primary)" : "var(--text-secondary)",
            background: isSelected ? "rgba(139,92,246,0.1)" : "transparent",
            fontWeight: isSelected ? 600 : 400,
          }}
          title={node.path || "/"}
        >
          <span style={{ width: 14, textAlign: "center", flexShrink: 0 }}>
            {isDir ? (isExpanded ? "▾" : "▸") : ""}
          </span>
          <span style={{ flexShrink: 0 }}>{isDir ? "📁" : "📄"}</span>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>
            {node.name || "/"}
          </span>
          {node.size != null && node.type === "file" && (
            <span style={{ fontSize: 10, color: "var(--text-tertiary)", flexShrink: 0 }}>
              {(node.size / 1024).toFixed(node.size > 10240 ? 0 : 1)}KB
            </span>
          )}
        </div>
        {isDir && isExpanded && node.children?.map((c) => renderNode(c, depth + 1))}
      </div>
    );
  };

  return (
    <div
      style={
        embedded
          ? {
              flex: 1,
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
              minWidth: 0,
            }
          : {
              width: 300,
              flexShrink: 0,
              borderLeft: "1px solid var(--border)",
              background: "rgba(255,255,255,0.45)",
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
              backdropFilter: "blur(20px)",
            }
      }
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 12px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 700 }}>📂 工作区文件</span>
        <div style={{ display: "flex", gap: 4 }}>
          <button
            onClick={() => void newFolder()}
            title="新建文件夹(到当前选中目录)"
            style={toolBtnStyle}
          >
            ＋目录
          </button>
          <button onClick={() => void load()} title="刷新" style={toolBtnStyle}>
            ⟳
          </button>
          <button onClick={downloadAllZip} title="打包下载整个工作区(zip)" style={toolBtnStyle}>
            ⬇zip
          </button>
          <label title="上传文件(到 uploads/)" style={{ ...toolBtnStyle, cursor: "pointer" }}>
            ↑
            <input
              type="file"
              style={{ display: "none" }}
              onChange={(e) => void uploadFile(e.target.files?.[0])}
            />
          </label>
        </div>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: 6 }}>
        {loading && <div style={{ fontSize: 12, color: "var(--text-tertiary)", padding: 8 }}>加载中…</div>}
        {error && <div style={{ fontSize: 11, color: "var(--danger-text)", padding: 8 }}>{error}</div>}
        {!loading && tree && renderNode(tree, 0)}
        {!loading && !tree && !error && (
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", padding: 8 }}>工作区为空</div>
        )}
      </div>

      {preview && (
        <div
          style={{
            borderTop: "1px solid var(--border)",
            maxHeight: 240,
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              padding: "6px 12px",
              fontSize: 11,
              color: "var(--text-tertiary)",
              borderBottom: "1px solid var(--border)",
            }}
          >
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {preview.path} {preview.type === "binary" ? `(二进制 ${preview.size ?? 0}B)` : ""}
            </span>
            {preview.type === "text" && (
              <button onClick={() => void navigator.clipboard.writeText(preview.content ?? "")} style={toolBtnStyle}>
                复制
              </button>
            )}
            <button onClick={() => download(preview.path)} style={toolBtnStyle}>
              下载
            </button>
            {/\.(zip|tar\.gz|tgz)$/i.test(preview.path) && (
              <button onClick={() => void extractArchive(preview.path)} style={toolBtnStyle}>
                解压
              </button>
            )}
            <button onClick={() => void rename(preview.path)} style={toolBtnStyle}>
              改名
            </button>
            <button onClick={() => void remove(preview.path)} style={{ ...toolBtnStyle, color: "var(--danger-text)" }}>
              删
            </button>
            <button onClick={() => setPreview(null)} style={toolBtnStyle}>
              ×
            </button>
          </div>
          {preview.type === "text" ? (
            <pre
              style={{
                margin: 0,
                padding: "8px 12px",
                overflow: "auto",
                fontSize: 11,
                fontFamily: "Consolas, monospace",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
                color: "var(--text-primary)",
                flex: 1,
              }}
            >
              {preview.content}
            </pre>
          ) : (
            <div style={{ padding: "12px 16px", fontSize: 12, color: "var(--text-tertiary)" }}>
              二进制文件无法预览, 请下载查看。
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const toolBtnStyle: React.CSSProperties = {
  border: "1px solid var(--border)",
  background: "rgba(255,255,255,0.6)",
  color: "var(--text-secondary)",
  borderRadius: 6,
  fontSize: 11,
  padding: "2px 8px",
  cursor: "pointer",
  lineHeight: 1.5,
};
