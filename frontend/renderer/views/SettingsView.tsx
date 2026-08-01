// Phase 1 Task 12 + 1.5 + 16 - 设置视图
// 模型 provider 可编辑(地址/模型/开关/API Key/测试连通性) + MCP servers 增删测 + 主题壁纸
import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE = "http://localhost:8765/admin";
const FILES_BASE = "http://127.0.0.1:8765/files/outputs";

interface ProviderInfo {
  name: string;
  enabled: boolean;
  model_name: string | null;
  base_url: string | null;
  api_key_configured: boolean;
}

interface McpServer {
  id: string;
  type: string;
  command?: string;
  args?: string[];
  url?: string;
  tags?: string[];
  enabled?: boolean;
}

export default function SettingsView(): JSX.Element {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [fallbackChain, setFallbackChain] = useState<string[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [protocol, setProtocol] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async (): Promise<void> => {
    try {
      const [provResp, mcpResp] = await Promise.all([
        fetch(`${API_BASE}/settings/providers`),
        fetch(`${API_BASE}/mcp/servers`),
      ]);
      const provData = await provResp.json();
      const mcpData = await mcpResp.json();
      setProviders(provData.providers ?? []);
      setFallbackChain(provData.fallback_chain ?? []);
      setMcpServers(mcpData.servers ?? []);
      setProtocol(mcpData.protocol_version ?? "");
      setError("");
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16, overflowY: "auto", paddingRight: 4 }}>
      <div className="glass-panel animate-in delay-1" style={{ padding: "20px 24px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
          模型 Provider
        </div>
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 16 }}>
          可编辑地址/模型/开关/API Key, 支持连通性测试; 降级链: {fallbackChain.join(" → ") || "—"}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {providers.map((p) => (
            <ProviderRow key={p.name} provider={p} onSaved={load} />
          ))}
        </div>
      </div>

      <div className="glass-panel animate-in delay-2" style={{ padding: "20px 24px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
          MCP Servers
        </div>
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 16 }}>
          协议版本: {protocol || "—"} · 可新增/删除/测试连通性(改动重启后端后生效)
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {mcpServers.map((s) => (
            <McpRow key={s.id} server={s} onChange={load} />
          ))}
          {mcpServers.length === 0 && (
            <div style={{ fontSize: 13, color: "var(--text-tertiary)", padding: "16px 0", textAlign: "center" }}>
              当前未配置 MCP server
            </div>
          )}
          <McpAddForm onAdded={load} />
        </div>
      </div>

      {error && <div style={{ fontSize: 12, color: "var(--danger-text)" }}>加载失败: {error}</div>}

      {/* 主题壁纸 */}
      <WallpaperSection />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Provider 行(可编辑 + 测试)
// ──────────────────────────────────────────────────────────────────────────────

function ProviderRow({
  provider,
  onSaved,
}: {
  provider: ProviderInfo;
  onSaved: () => void;
}): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [baseUrl, setBaseUrl] = useState(provider.base_url ?? "");
  const [modelName, setModelName] = useState(provider.model_name ?? "");
  const [enabled, setEnabled] = useState(provider.enabled);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const beginEdit = (): void => {
    setBaseUrl(provider.base_url ?? "");
    setModelName(provider.model_name ?? "");
    setEnabled(provider.enabled);
    setApiKey("");
    setMsg(null);
    setEditing(true);
  };

  const save = async (): Promise<void> => {
    setBusy(true);
    setMsg(null);
    try {
      const body: Record<string, unknown> = {};
      if (baseUrl.trim()) body.base_url = baseUrl.trim();
      if (modelName.trim()) body.model_name = modelName.trim();
      body.enabled = enabled;
      if (apiKey.trim()) body.api_key = apiKey.trim();
      const resp = await fetch(`${API_BASE}/settings/providers/${provider.name}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail ?? `HTTP ${resp.status}`);
      setMsg("已保存");
      setEditing(false);
      onSaved();
    } catch (err) {
      setMsg(`保存失败: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const test = async (): Promise<void> => {
    setBusy(true);
    setMsg(null);
    try {
      const resp = await fetch(`${API_BASE}/settings/providers/${provider.name}/test`, {
        method: "POST",
      });
      const data = await resp.json();
      setMsg(
        data.ok
          ? `✅ 连通正常: ${data.sample ?? ""}`
          : `❌ ${data.error ?? "测试失败"}`
      );
    } catch (err) {
      setMsg(`测试失败: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        padding: "12px 14px",
        borderRadius: "var(--radius-sm)",
        background: "rgba(255,255,255,0.5)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span
          style={{
            width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
            background: provider.enabled ? "var(--success-text)" : "#cbd5e1",
          }}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 600 }}>{provider.name}</span>
            <span
              style={{
                fontSize: 11, padding: "1px 8px", borderRadius: 10,
                background: provider.api_key_configured ? "var(--success-bg)" : "#f1f5f9",
                color: provider.api_key_configured ? "var(--success-text)" : "var(--text-tertiary)",
              }}
            >
              {provider.api_key_configured ? "Key 已配置" : "Key 未配置"}
            </span>
            {!provider.enabled && (
              <span style={{ fontSize: 11, padding: "1px 8px", borderRadius: 10, background: "#fef3c7", color: "#d97706" }}>
                已禁用
              </span>
            )}
          </div>
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {provider.model_name ?? "—"} · {provider.base_url ?? "—"}
          </div>
        </div>
        {!editing && (
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn-ghost" style={{ fontSize: 12, padding: "5px 10px" }} onClick={beginEdit}>
              编辑
            </button>
            <button className="btn-ghost" style={{ fontSize: 12, padding: "5px 10px" }} onClick={() => void test()} disabled={busy}>
              测试
            </button>
          </div>
        )}
      </div>

      {editing && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10, paddingTop: 10, borderTop: "1px solid rgba(148,163,184,0.15)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>API 地址</span>
            <input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://..."
              style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
            />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>模型名</span>
            <input
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              placeholder="model-name"
              style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
            />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>API Key</span>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={provider.api_key_configured ? "已配置(留空不修改)" : "输入新 Key"}
              style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
            />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              启用
            </label>
            <button className="btn-primary" style={{ fontSize: 12, padding: "6px 14px" }} onClick={() => void save()} disabled={busy}>
              保存
            </button>
            <button className="btn-ghost" style={{ fontSize: 12, padding: "6px 12px" }} onClick={() => setEditing(false)} disabled={busy}>
              取消
            </button>
            <button className="btn-ghost" style={{ fontSize: 12, padding: "6px 12px" }} onClick={() => void test()} disabled={busy}>
              测试连通性
            </button>
          </div>
          {msg && <div style={{ fontSize: 12, color: msg.startsWith("✅") ? "var(--success-text)" : msg.startsWith("❌") ? "var(--danger-text)" : "var(--text-secondary)" }}>{msg}</div>}
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// MCP Server 行(测试 + 删除)
// ──────────────────────────────────────────────────────────────────────────────

function McpRow({
  server,
  onChange,
}: {
  server: McpServer;
  onChange: () => void;
}): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const test = async (): Promise<void> => {
    setBusy(true);
    setMsg(null);
    try {
      const resp = await fetch(`${API_BASE}/settings/mcp/${encodeURIComponent(server.id)}/test`, {
        method: "POST",
      });
      const data = await resp.json();
      setMsg(
        data.ok
          ? `✅ 连接正常 (${data.server_info || data.protocol || "ok"})`
          : `❌ ${data.error ?? "测试失败"}`
      );
    } catch (err) {
      setMsg(`测试失败: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (): Promise<void> => {
    setBusy(true);
    setMsg(null);
    try {
      await fetch(`${API_BASE}/settings/mcp/${encodeURIComponent(server.id)}`, { method: "DELETE" });
      onChange();
    } catch (err) {
      setMsg(`删除失败: ${String(err)}`);
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", borderRadius: "var(--radius-sm)", background: "rgba(255,255,255,0.5)" }}>
      <span style={{ fontSize: 13, fontWeight: 600, flexShrink: 0 }}>{server.id}</span>
      <span style={{ fontSize: 11, padding: "1px 8px", borderRadius: 10, background: "var(--info-bg)", color: "var(--info-text)", flexShrink: 0 }}>
        {server.type}
      </span>
      <span style={{ fontSize: 12, color: "var(--text-tertiary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, minWidth: 0 }}>
        {server.url || [server.command, ...(server.args ?? [])].join(" ")}
      </span>
      <button className="btn-ghost" style={{ fontSize: 12, padding: "5px 10px", flexShrink: 0 }} onClick={() => void test()} disabled={busy}>
        测试
      </button>
      <button className="btn-ghost" style={{ fontSize: 12, padding: "5px 10px", flexShrink: 0, color: "var(--danger-text)" }} onClick={() => void remove()} disabled={busy}>
        删除
      </button>
      {msg && <span style={{ fontSize: 11, color: msg.startsWith("✅") ? "var(--success-text)" : "var(--danger-text)" }}>{msg}</span>}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// MCP 新增表单
// ──────────────────────────────────────────────────────────────────────────────

function McpAddForm({ onAdded }: { onAdded: () => void }): JSX.Element {
  const [open, setOpen] = useState(false);
  const [type, setType] = useState<"http" | "stdio">("http");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const submit = async (): Promise<void> => {
    if (!name.trim()) {
      setMsg("请填写 server 名称");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const body: Record<string, unknown> = {
        name: name.trim(),
        type,
      };
      if (type === "http") {
        if (!url.trim()) {
          setMsg("请填写 URL");
          setBusy(false);
          return;
        }
        body.url = url.trim();
      } else {
        if (!command.trim()) {
          setMsg("请填写启动命令");
          setBusy(false);
          return;
        }
        body.command = command.trim();
        body.args = args.split(/\s+/).filter(Boolean);
      }
      const resp = await fetch(`${API_BASE}/settings/mcp`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail ?? `HTTP ${resp.status}`);
      setName("");
      setUrl("");
      setCommand("");
      setArgs("");
      setMsg("已添加(重启后端后生效)");
      onAdded();
    } catch (err) {
      setMsg(`添加失败: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button className="btn-ghost" style={{ fontSize: 12, padding: "8px 14px", alignSelf: "flex-start" }} onClick={() => setOpen(true)}>
        + 添加 MCP Server
      </button>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "12px 14px", borderRadius: "var(--radius-sm)", background: "rgba(255,255,255,0.4)" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56, flexShrink: 0 }}>名称</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="server 名称(唯一)"
          style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
        />
        <div style={{ display: "flex", gap: 4 }}>
          {(["http", "stdio"] as const).map((t) => (
            <button
              key={t}
              className="btn-ghost"
              style={{
                fontSize: 11, padding: "4px 10px",
                background: type === t ? "var(--gradient-indigo)" : "rgba(255,255,255,0.5)",
                color: type === t ? "#fff" : "var(--text-primary)",
                border: "none",
              }}
              onClick={() => setType(t)}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      {type === "http" ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56, flexShrink: 0 }}>URL</span>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://127.0.0.1:port/mcp"
            style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
          />
        </div>
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56, flexShrink: 0 }}>命令</span>
            <input
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              placeholder="npx"
              style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
            />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56, flexShrink: 0 }}>参数</span>
            <input
              value={args}
              onChange={(e) => setArgs(e.target.value)}
              placeholder="空格分隔, 如 -y @modelcontextprotocol/server-filesystem C:/tmp"
              style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
            />
          </div>
        </>
      )}
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button className="btn-primary" style={{ fontSize: 12, padding: "6px 14px" }} onClick={() => void submit()} disabled={busy}>
          添加
        </button>
        <button className="btn-ghost" style={{ fontSize: 12, padding: "6px 12px" }} onClick={() => setOpen(false)}>
          取消
        </button>
        {msg && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{msg}</span>}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// 主题壁纸板块(Phase 1.5)
// ──────────────────────────────────────────────────────────────────────────────

function WallpaperSection(): JSX.Element {
  const [wallpaper, setWallpaper] = useState<string | null>(null);
  const [fit, setFit] = useState<"cover" | "contain">("cover");
  const [posX, setPosX] = useState(50);
  const [posY, setPosY] = useState(50);
  const [scale, setScale] = useState(100);
  const [rotate, setRotate] = useState(0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async (): Promise<void> => {
    try {
      const resp = await fetch(`${API_BASE}/wallpaper`);
      const data = await resp.json();
      setWallpaper(
        data.wallpaper
          ? `${FILES_BASE}/${data.wallpaper.split("/").pop()}?t=${Date.now()}`
          : null
      );
      if (data.style) {
        setFit(data.style.fit === "contain" ? "contain" : "cover");
        setPosX(Number(data.style.position_x) || 50);
        setPosY(Number(data.style.position_y) || 50);
        setScale(Number(data.style.scale) || 100);
        setRotate(Number(data.style.rotate) || 0);
      }
    } catch {
      setWallpaper(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const saveStyle = async (): Promise<void> => {
    setBusy(true);
    setMsg(null);
    try {
      const resp = await fetch(`${API_BASE}/wallpaper/style`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          position_x: posX,
          position_y: posY,
          fit,
          scale,
          rotate,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setMsg("显示样式已保存");
    } catch (err) {
      setMsg(`保存失败: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const onFileChange = async (
    e: React.ChangeEvent<HTMLInputElement>
  ): Promise<void> => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (file.size > 6 * 1024 * 1024) {
      setMsg("图片超过 6MB, 请压缩后再试");
      return;
    }
    if (!/^image\/(png|jpeg|webp)$/.test(file.type)) {
      setMsg("仅支持 PNG / JPG / WebP");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error("读取失败"));
        reader.readAsDataURL(file);
      });
      const resp = await fetch(`${API_BASE}/wallpaper`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data_url: dataUrl }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      // 换图后 URL 必须不同, 否则 <img> 复用浏览器缓存的旧图
      setWallpaper(
        data.wallpaper
          ? `${FILES_BASE}/${data.wallpaper.split("/").pop()}?t=${Date.now()}`
          : null
      );
      setMsg("壁纸已更新, 首页将使用新背景");
    } catch (err) {
      setMsg(`上传失败: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (): Promise<void> => {
    setBusy(true);
    setMsg(null);
    try {
      await fetch(`${API_BASE}/wallpaper`, { method: "DELETE" });
      setWallpaper(null);
      setMsg("已恢复默认背景");
    } catch {
      setMsg("移除失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-panel animate-in delay-3" style={{ padding: "20px 24px" }}>
      <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
        主题壁纸
      </div>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 16 }}>
        设置首页顶部背景图 (PNG / JPG / WebP, ≤6MB); 可调整显示位置与填充方式
      </div>
      <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>
        <div
          style={{
            width: 240,
            height: 136,
            borderRadius: "var(--radius-sm)",
            overflow: "hidden",
            background:
              "linear-gradient(135deg, #eef1f8 0%, #e6ebf6 45%, #ece7f7 100%)",
            flexShrink: 0,
            border: "1px solid rgba(148,163,184,0.15)",
            position: "relative",
          }}
        >
          {wallpaper && (
            <img
              src={wallpaper}
              alt="当前壁纸"
              style={{
                width: "100%",
                height: "100%",
                objectFit: fit,
                objectPosition: `${posX}% ${posY}%`,
                transform: `scale(${scale / 100}) rotate(${rotate}deg)`,
                display: "block",
              }}
            />
          )}
          {!wallpaper && (
            <div
              style={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 12,
                color: "var(--text-tertiary)",
              }}
            >
              默认渐变
            </div>
          )}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10, flex: 1, minWidth: 260 }}>
          <div style={{ display: "flex", gap: 8 }}>
            {/* label 原生关联 file input, 比 JS .click() 更可靠 */}
            <label
              className="btn-primary"
              style={{
                cursor: busy ? "not-allowed" : "pointer",
                opacity: busy ? 0.6 : 1,
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              {wallpaper ? "更换壁纸" : "上传壁纸"}
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                style={{ display: "none" }}
                onChange={(e) => void onFileChange(e)}
              />
            </label>
            {wallpaper && (
              <button className="btn-ghost" onClick={() => void remove()} disabled={busy}>
                移除壁纸
              </button>
            )}
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56 }}>填充方式</span>
            <button
              className="btn-ghost"
              style={{
                padding: "6px 14px",
                fontSize: 12,
                background: fit === "cover" ? "var(--gradient-indigo)" : "rgba(255,255,255,0.5)",
                color: fit === "cover" ? "#fff" : "var(--text-primary)",
                border: "none",
              }}
              onClick={() => setFit("cover")}
            >
              铺满
            </button>
            <button
              className="btn-ghost"
              style={{
                padding: "6px 14px",
                fontSize: 12,
                background: fit === "contain" ? "var(--gradient-indigo)" : "rgba(255,255,255,0.5)",
                color: fit === "contain" ? "#fff" : "var(--text-primary)",
                border: "none",
              }}
              onClick={() => setFit("contain")}
            >
              完整显示
            </button>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56 }}>水平位置</span>
            <input
              type="range"
              min={0}
              max={100}
              value={posX}
              onChange={(e) => setPosX(Number(e.target.value))}
              style={{ flex: 1 }}
            />
            <span style={{ fontSize: 12, color: "var(--text-tertiary)", width: 34, textAlign: "right" }}>{posX}%</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56 }}>垂直位置</span>
            <input
              type="range"
              min={0}
              max={100}
              value={posY}
              onChange={(e) => setPosY(Number(e.target.value))}
              style={{ flex: 1 }}
            />
            <span style={{ fontSize: 12, color: "var(--text-tertiary)", width: 34, textAlign: "right" }}>{posY}%</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56 }}>缩放</span>
            <input
              type="range"
              min={50}
              max={200}
              value={scale}
              onChange={(e) => setScale(Number(e.target.value))}
              style={{ flex: 1 }}
            />
            <span style={{ fontSize: 12, color: "var(--text-tertiary)", width: 34, textAlign: "right" }}>{scale}%</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56 }}>旋转</span>
            {[0, 90, 180, 270].map((deg) => (
              <button
                key={deg}
                className="btn-ghost"
                style={{
                  padding: "6px 12px",
                  fontSize: 12,
                  background:
                    rotate === deg
                      ? "var(--gradient-indigo)"
                      : "rgba(255,255,255,0.5)",
                  color: rotate === deg ? "#fff" : "var(--text-primary)",
                  border: "none",
                }}
                onClick={() => setRotate(deg)}
              >
                {deg === 0 ? "0°" : `${deg}°`}
              </button>
            ))}
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>横竖屏调整</span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button className="btn-ghost" onClick={() => void saveStyle()} disabled={busy}>
              保存样式
            </button>
            {msg && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{msg}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
