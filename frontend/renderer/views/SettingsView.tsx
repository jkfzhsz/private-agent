// Phase 1 Task 12 + 1.5 - 设置视图
// 模型 provider 状态 + MCP servers + 主题壁纸(上传/移除)
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
          API Key 通过环境变量 PA_{"{NAME}"}_API_KEY 配置(不显示明文);降级链: {fallbackChain.join(" → ") || "—"}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {providers.map((p) => (
            <div
              key={p.name}
              style={{
                display: "flex", alignItems: "center", gap: 12,
                padding: "12px 14px", borderRadius: "var(--radius-sm)",
                background: "rgba(255,255,255,0.5)",
              }}
            >
              <span
                style={{
                  width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
                  background: p.enabled ? "var(--success-text)" : "#cbd5e1",
                }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 14, fontWeight: 600 }}>{p.name}</span>
                  <span
                    style={{
                      fontSize: 11, padding: "1px 8px", borderRadius: 10,
                      background: p.api_key_configured ? "var(--success-bg)" : "#f1f5f9",
                      color: p.api_key_configured ? "var(--success-text)" : "var(--text-tertiary)",
                    }}
                  >
                    {p.api_key_configured ? "Key 已配置" : "Key 未配置"}
                  </span>
                  {!p.enabled && (
                    <span style={{ fontSize: 11, padding: "1px 8px", borderRadius: 10, background: "#fef3c7", color: "#d97706" }}>
                      已禁用
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {p.model_name ?? "—"} · {p.base_url ?? "—"}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="glass-panel animate-in delay-2" style={{ padding: "20px 24px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
          MCP Servers
        </div>
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 16 }}>
          协议版本: {protocol || "—"} · 配置源: config.yaml → tools.mcp.servers
        </div>
        {mcpServers.length === 0 ? (
          <div style={{ fontSize: 13, color: "var(--text-tertiary)", padding: "20px 0", textAlign: "center" }}>
            当前未配置 MCP server。在 config.yaml 的 tools.mcp.servers 中添加后重启生效。
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {mcpServers.map((s) => (
              <div
                key={s.id}
                style={{
                  display: "flex", alignItems: "center", gap: 12,
                  padding: "12px 14px", borderRadius: "var(--radius-sm)",
                  background: "rgba(255,255,255,0.5)",
                }}
              >
                <span style={{ fontSize: 13, fontWeight: 600, flex: 1 }}>{s.id}</span>
                <span style={{ fontSize: 11, padding: "1px 8px", borderRadius: 10, background: "var(--info-bg)", color: "var(--info-text)" }}>
                  {s.type}
                </span>
                <span style={{ fontSize: 12, color: "var(--text-tertiary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 260 }}>
                  {s.url || [s.command, ...(s.args ?? [])].join(" ")}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {error && <div style={{ fontSize: 12, color: "var(--danger-text)" }}>加载失败: {error}</div>}

      {/* 主题壁纸 */}
      <WallpaperSection />
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
