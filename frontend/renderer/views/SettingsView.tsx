// Phase 1 Task 12 + 1.5 + 16 - 设置视图
// 模型 provider 可编辑(地址/模型/开关/API Key/测试连通性) + MCP servers 增删测 + 主题壁纸
import { useCallback, useEffect, useRef, useState } from "react";

import { adminFetch, getAdminToken, isAdminTokenConfigured, setAdminToken } from "../utils/apiClient";

const API_BASE = "http://localhost:8765/admin";
const FILES_BASE = "http://127.0.0.1:8765/files/outputs";

interface ProviderInfo {
  name: string;
  enabled: boolean;
  model_name: string | null;
  base_url: string | null;
  api_key_configured: boolean;
  limits?: { max_input_tokens?: number; max_output_tokens?: number; max_turns?: number };
}

interface McpServer {
  id: string;
  type: string;
  command?: string;
  args?: string[];
  url?: string;
  tags?: string[];
  enabled?: boolean;
  // V2 P2: 装配到对话开关(默认 true; false 时工具不进对话)
  assemble?: boolean;
}

export default function SettingsView({ sessionId = 1 }: { sessionId?: number }): JSX.Element {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [fallbackChain, setFallbackChain] = useState<string[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [protocol, setProtocol] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async (): Promise<void> => {
    try {
      const [provResp, mcpResp] = await Promise.all([
        adminFetch(`${API_BASE}/settings/providers`),
        adminFetch(`${API_BASE}/mcp/servers`),
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

  const handleDeleteProvider = async (name: string): Promise<void> => {
    try {
      const resp = await adminFetch(`${API_BASE}/settings/providers/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail ?? `HTTP ${resp.status}`);
      await load();
    } catch (err) {
      window.alert(`删除失败: ${String(err)}`);
    }
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 16, overflowY: "auto", paddingRight: 4 }}>
      <div className="glass-panel animate-in delay-1" style={{ padding: "20px 24px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
          模型提供商
        </div>
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 16 }}>
          可新增/编辑/删除模型(任意 OpenAI 兼容服务) · 编辑可配置参数上限(输入/输出/轮次) · 降级链: {fallbackChain.join(" → ") || "—"}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {providers.map((p) => (
            <ProviderRow key={p.name} provider={p} onSaved={load} onDelete={handleDeleteProvider} />
          ))}
          {providers.length === 0 && (
            <div style={{ fontSize: 13, color: "var(--text-tertiary)", padding: "16px 0", textAlign: "center" }}>
              ⚠️ 尚未配置任何模型。<b>在下方向"添加模型"填入你的模型服务即可开始对话</b>
              (任意 OpenAI 兼容服务: 名称 + Base URL + 模型名 + API Key)
            </div>
          )}
          <ProviderAddForm onAdded={load} />
        </div>
      </div>

      <div className="glass-panel animate-in delay-2" style={{ padding: "20px 24px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
          MCP 服务
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

      {/* §6.14 [MVP] 沙箱配置管理 UI */}
      <SandboxSection />

      {error && <div style={{ fontSize: 12, color: "var(--danger-text)" }}>加载失败: {error}</div>}

      {/* 阶段二批次 1: admin 鉴权 token 管理 */}
      <SecuritySection />

      {/* 阶段三批次 1(T1.2): 会话级权限模式切换(使用当前会话 id) */}
      <PermissionModeSection sessionId={sessionId} />

      {/* 主题壁纸 */}
      <WallpaperSection />

      {/* 技能管理: 列表 + 上传新技能 */}
      <SkillsSection />

      {/* 关于与更新 */}
      <UpdateSection />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// 安全管理: admin 控制面鉴权 token(X-Admin-Token)
// Electron 桌面版由主进程从 backend/.env 自动注入, 无需配置;
// 浏览器(dev)模式需在此录入 backend/.env 中的 PA_ADMIN_TOKEN 值。
// ──────────────────────────────────────────────────────────────────────────────

function SecuritySection(): JSX.Element {
  const [tokenInput, setTokenInput] = useState(getAdminToken());
  const [status, setStatus] = useState<string>(
    isAdminTokenConfigured()
      ? "已配置(控制面请求自动携带 token)"
      : "未配置: 浏览器模式需录入 token, 否则控制面请求返回 401",
  );

  // 任一请求 401 时提示(adminFetch 派发 pa:auth-required)
  useEffect(() => {
    const onAuthRequired = (): void => {
      setStatus("⚠️ 控制面请求被拒(401): 请录入正确的 admin token(backend/.env 的 PA_ADMIN_TOKEN)");
    };
    window.addEventListener("pa:auth-required", onAuthRequired);
    return () => window.removeEventListener("pa:auth-required", onAuthRequired);
  }, []);

  const save = (): void => {
    const t = tokenInput.trim();
    setAdminToken(t);
    setStatus(t ? "已保存并生效(控制面请求将自动携带)" : "已清空 token");
  };

  return (
    <div className="glass-panel animate-in delay-1" style={{ padding: "20px 24px" }}>
      <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
        安全管理
      </div>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 12 }}>
        控制面鉴权 token(X-Admin-Token) · 桌面版自动注入无需配置 · 浏览器(dev)模式手动录入
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input
          type="password"
          value={tokenInput}
          onChange={(e) => setTokenInput(e.target.value)}
          placeholder="粘贴 PA_ADMIN_TOKEN(backend/.env)"
          style={{
            flex: 1,
            padding: "8px 12px",
            borderRadius: 8,
            border: "1px solid var(--border)",
            background: "var(--panel-bg)",
            color: "var(--text-primary)",
            fontSize: 13,
            fontFamily: "monospace",
          }}
        />
        <button
          onClick={save}
          style={{
            padding: "8px 16px",
            borderRadius: 8,
            border: "none",
            background: "var(--accent)",
            color: "#fff",
            fontSize: 13,
            cursor: "pointer",
          }}
        >
          保存
        </button>
      </div>
      <div style={{ fontSize: 12, marginTop: 8, color: "var(--text-tertiary)" }}>{status}</div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// 权限模式(阶段三批次1 T1.2): 会话级权限模式切换(default/plan/acceptEdits/cautious/deny_all)
// ──────────────────────────────────────────────────────────────────────────────

function PermissionModeSection({ sessionId }: { sessionId: number }): JSX.Element {
  const [mode, setMode] = useState<string>("default");
  const [descriptions, setDescriptions] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<string>("");

  const load = useCallback(async (): Promise<void> => {
    try {
      const resp = await adminFetch(`${API_BASE}/settings/permission?session_id=${sessionId}`);
      if (resp.ok) {
        const data = await resp.json();
        setMode(data.mode ?? "default");
        setDescriptions(data.mode_descriptions ?? {});
      }
    } catch {
      setStatus("⚠️ 权限模式加载失败");
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const apply = async (m: string): Promise<void> => {
    try {
      const resp = await adminFetch(`${API_BASE}/settings/permission`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, mode: m }),
      });
      if (resp.ok) {
        setMode(m);
        setStatus(`✅ 已切换为 ${m} 模式(下轮对话生效)`);
      } else {
        setStatus(`⚠️ 切换失败: HTTP ${resp.status}`);
      }
    } catch {
      setStatus("⚠️ 切换失败: 网络错误");
    }
  };

  const allModes = ["default", "plan", "acceptEdits", "cautious", "deny_all"];

  return (
    <div className="glass-panel animate-in delay-1" style={{ padding: "20px 24px" }}>
      <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
        权限模式
      </div>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 12 }}>
        会话级工具权限策略 · 阶段三(人在环中) · 模式切换后下轮对话生效
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
        {allModes.map((m) => (
          <button
            key={m}
            onClick={() => void apply(m)}
            style={{
              padding: "6px 14px",
              borderRadius: 8,
              // 2026-08-04 修复: --accent 未定义导致选中按钮白底白字,
              // 改用 --gradient-indigo(蓝紫渐变) + 加粗 + #fff 文字,对比清晰。
              border: m === mode ? "2px solid var(--gradient-indigo)" : "1px solid var(--border)",
              background: m === mode ? "var(--gradient-indigo)" : "var(--panel-bg)",
              color: m === mode ? "#fff" : "var(--text-primary)",
              fontSize: 12,
              cursor: "pointer",
              fontWeight: m === mode ? 600 : 400,
            }}
          >
            {m}
          </button>
        ))}
      </div>
      <div style={{ fontSize: 12, marginTop: 10, color: "var(--text-tertiary)" }}>
        {descriptions[mode] ?? mode}
      </div>
      {status && <div style={{ fontSize: 12, marginTop: 6, color: "var(--text-tertiary)" }}>{status}</div>}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// 技能管理: 展示全部技能(含未启用) + 上传新技能(skill.yaml + system_prompt)
// ──────────────────────────────────────────────────────────────────────────────

interface SkillInfo {
  name: string;
  version: string;
  description: string;
  enabled: boolean;
}

function SkillsSection(): JSX.Element {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [name, setName] = useState("");
  const [skillYaml, setSkillYaml] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  // 2026-08-04: zip 一键上传
  const [zipBusy, setZipBusy] = useState(false);
  const [zipMsg, setZipMsg] = useState<string | null>(null);
  const zipRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const resp = await adminFetch("http://127.0.0.1:8765/admin/skills");
      const data = await resp.json();
      setSkills(Array.isArray(data) ? data : []);
    } catch {
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 2026-08-04: 选择 zip 即上传(简单方式, 无需手填 yaml)
  const uploadZip = async (e: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setZipMsg("请选择 .zip 压缩包");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      setZipMsg("压缩包超过 50MB");
      return;
    }
    setZipBusy(true);
    setZipMsg("上传中...");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const resp = await adminFetch("http://127.0.0.1:8765/admin/skills/upload-zip", {
        method: "POST",
        body: fd,
      });
      const data = await resp.json();
      if (!resp.ok) {
        const detail = data.detail ?? data.error ?? `HTTP ${resp.status}`;
        throw new Error(String(detail));
      }
      // 2026-08-04: 支持集合包/素材库自动技能化返回(skills 数组)
      if (Array.isArray(data.skills) && data.skills.length > 0) {
        const names = data.skills.map((s: { name: string }) => s.name).join(", ");
        setZipMsg(`✅ 已导入 ${data.skills.length} 个技能: ${names}`);
      } else {
        setZipMsg(`✅ 技能「${data.name}」上传成功(${data.files} 个文件)`);
      }
      await load();
    } catch (err) {
      setZipMsg(`上传失败: ${String(err)}`);
    } finally {
      setZipBusy(false);
    }
  };

  const upload = async (): Promise<void> => {
    setMsg(null);
    if (!name.trim() || !skillYaml.trim()) {
      setMsg("请填写技能名称和 skill.yaml 内容");
      return;
    }
    try {
      const resp = await adminFetch("http://127.0.0.1:8765/admin/skills/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          skill_yaml: skillYaml,
          system_prompt: systemPrompt,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error ?? `HTTP ${resp.status}`);
      }
      setMsg(`技能 ${data.name} 上传成功`);
      setName("");
      setSkillYaml("");
      setSystemPrompt("");
      await load();
    } catch (e) {
      setMsg(`上传失败: ${String(e)}`);
    }
  };

  return (
    <div className="glass-panel" style={{ padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div style={{ fontSize: 15, fontWeight: 600 }}>技能管理</div>
        <button className="btn-secondary" style={{ fontSize: 12, padding: "4px 12px" }} onClick={() => void load()}>
          {loading ? "刷新中..." : "刷新"}
        </button>
      </div>

      {/* 已安装技能 */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
        {skills.map((s) => (
          <span
            key={s.name}
            style={{
              fontSize: 12, padding: "4px 12px", borderRadius: 12,
              border: s.enabled ? "1px solid #4caf50" : "1px solid #ccc",
              color: s.enabled ? "#2e7d32" : "var(--text-tertiary)",
              background: s.enabled ? "rgba(76,175,80,0.08)" : "transparent",
            }}
          >
            {s.name} v{s.version} {s.enabled ? "· 已启用" : "· 未启用"}
          </span>
        ))}
        {skills.length === 0 && (
          <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>暂无技能</div>
        )}
      </div>

      {/* 2026-08-04: zip 一键上传(简单方式) */}
      <div
        style={{
          display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
          padding: "12px 14px", borderRadius: 10,
          background: "rgba(129,140,248,0.06)", border: "1px dashed rgba(129,140,248,0.4)",
          marginBottom: 14,
        }}
      >
        <label
          style={{
            fontSize: 13, fontWeight: 600, cursor: zipBusy ? "not-allowed" : "pointer",
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "8px 16px", borderRadius: 8,
            background: "var(--gradient-indigo)", color: "#fff",
          }}
        >
          📦 上传技能压缩包(zip)
          <input
            ref={zipRef}
            type="file"
            accept=".zip"
            style={{ display: "none" }}
            disabled={zipBusy}
            onChange={(e) => void uploadZip(e)}
          />
        </label>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          选 zip 即自动解析安装(skill.yaml + system_prompt.md + references/ 等全部文件)
        </span>
        {zipMsg && (
          <span style={{ fontSize: 12, color: zipMsg.startsWith("✅") ? "var(--success-text)" : zipMsg.startsWith("上传中") ? "var(--text-secondary)" : "#d32f2f" }}>
            {zipMsg}
          </span>
        )}
      </div>

      {/* 上传新技能(高级: 手动填写) */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>高级: 手动填写 skill.yaml</div>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="技能名称(小写字母/数字/下划线, 如 my_skill)"
          style={{ padding: "8px 10px", borderRadius: 6, border: "1px solid #ddd", fontSize: 13 }}
        />
        <textarea
          value={skillYaml}
          onChange={(e) => setSkillYaml(e.target.value)}
          placeholder="skill.yaml 内容(需含 name 字段)"
          rows={5}
          style={{ padding: "8px 10px", borderRadius: 6, border: "1px solid #ddd", fontSize: 12, fontFamily: "monospace" }}
        />
        <textarea
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder="system_prompt.md 内容(可选)"
          rows={3}
          style={{ padding: "8px 10px", borderRadius: 6, border: "1px solid #ddd", fontSize: 12 }}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button className="btn-primary" style={{ padding: "8px 18px", fontSize: 13 }} onClick={() => void upload()}>
            上传技能
          </button>
          {msg && <span style={{ fontSize: 12, color: msg.startsWith("技能") ? "#4caf50" : "#d32f2f" }}>{msg}</span>}
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// 关于与更新: 版本信息 + 检查更新
// ──────────────────────────────────────────────────────────────────────────────

function UpdateSection(): JSX.Element {
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const runCheck = async (): Promise<void> => {
    setChecking(true);
    setResult(null);
    try {
      const r = (await window.pa?.checkForUpdates?.()) as {
        hasUpdate?: boolean;
        currentVersion?: string;
        latestVersion?: string;
        releaseUrl?: string;
        notes?: string;
        failed?: boolean;
      };
      if (!r) {
        setResult("无法检查更新(请在打包版中使用)");
      } else if (r.failed) {
        setResult(`检查失败: ${r.notes || "未知错误"}`);
      } else if (r.hasUpdate) {
        setResult(
          `发现新版本 ${r.latestVersion}(当前 ${r.currentVersion})${r.releaseUrl ? `\n下载: ${r.releaseUrl}` : ""}`
        );
      } else {
        setResult(`当前已是最新版本 v${r.currentVersion}${r.latestVersion ? `(远端 ${r.latestVersion})` : ""}`);
      }
    } catch (e) {
      setResult(`检查更新出错: ${String(e)}`);
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="glass-panel" style={{ padding: 16 }}>
      <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>关于与更新</div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <button
          className="btn-primary"
          style={{ padding: "8px 18px", fontSize: 13 }}
          onClick={() => void runCheck()}
          disabled={checking}
        >
          {checking ? "检查中..." : "检查更新"}
        </button>
        <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
          当前版本 v{window.pa?.versions?.app || "0.1.0"}
        </span>
      </div>
      {result && (
        <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", marginTop: 10, color: "var(--text-secondary)" }}>
          {result}
        </pre>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Provider 行(可编辑 + 测试)
// ──────────────────────────────────────────────────────────────────────────────

function ProviderRow({
  provider,
  onSaved,
  onDelete,
}: {
  provider: ProviderInfo;
  onSaved: () => void;
  onDelete: (name: string) => void;
}): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [baseUrl, setBaseUrl] = useState(provider.base_url ?? "");
  const [modelName, setModelName] = useState(provider.model_name ?? "");
  const [enabled, setEnabled] = useState(provider.enabled);
  const [apiKey, setApiKey] = useState("");
  const [maxInput, setMaxInput] = useState(provider.limits?.max_input_tokens ?? 8192);
  const [maxOutput, setMaxOutput] = useState(provider.limits?.max_output_tokens ?? 2048);
  const [maxTurns, setMaxTurns] = useState(provider.limits?.max_turns ?? 20);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const beginEdit = (): void => {
    setBaseUrl(provider.base_url ?? "");
    setModelName(provider.model_name ?? "");
    setEnabled(provider.enabled);
    setApiKey("");
    setMaxInput(provider.limits?.max_input_tokens ?? 8192);
    setMaxOutput(provider.limits?.max_output_tokens ?? 2048);
    setMaxTurns(provider.limits?.max_turns ?? 20);
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
      body.max_input_tokens = maxInput;
      body.max_output_tokens = maxOutput;
      body.max_turns = maxTurns;
      const resp = await adminFetch(`${API_BASE}/settings/providers/${provider.name}`, {
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
      const resp = await adminFetch(`${API_BASE}/settings/providers/${provider.name}/test`, {
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
            <button
              className="btn-ghost"
              style={{ fontSize: 12, padding: "5px 10px", color: "var(--danger-text)" }}
              onClick={() => {
                if (window.confirm(`确定删除模型 provider「${provider.name}」？删除后需重新配置才能使用。`)) {
                  onDelete(provider.name);
                }
              }}
              title="删除此模型"
            >
              删除
            </button>
          </div>
        )}
      </div>

      {/* 测试/保存结果提示(放编辑区外: 非编辑模式的"测试"按钮结果也可见) */}
      {msg && (
        <div
          style={{
            fontSize: 12,
            marginTop: 8,
            color: msg.startsWith("✅")
              ? "var(--success-text)"
              : msg.startsWith("❌")
                ? "var(--danger-text)"
                : "var(--text-secondary)",
          }}
        >
          {msg}
        </div>
      )}

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
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>参数上限</span>
            <input
              type="number"
              min={256}
              value={maxInput}
              onChange={(e) => setMaxInput(Number(e.target.value))}
              title="最大输入 tokens"
              style={{ width: 90, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
            />
            <input
              type="number"
              min={64}
              value={maxOutput}
              onChange={(e) => setMaxOutput(Number(e.target.value))}
              title="最大输出 tokens"
              style={{ width: 90, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
            />
            <input
              type="number"
              min={1}
              value={maxTurns}
              onChange={(e) => setMaxTurns(Number(e.target.value))}
              title="最大轮次"
              style={{ width: 70, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
            />
            <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>输入/输出/轮次</span>
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
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// 新增 Provider 表单
// ──────────────────────────────────────────────────────────────────────────────

function ProviderAddForm({ onAdded }: { onAdded: () => void }): JSX.Element {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [modelName, setModelName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [maxInput, setMaxInput] = useState(8192);
  const [maxOutput, setMaxOutput] = useState(2048);
  const [maxTurns, setMaxTurns] = useState(20);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const submit = async (): Promise<void> => {
    if (!name.trim() || !baseUrl.trim() || !modelName.trim()) {
      setMsg("请填写名称、API 地址和模型名");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const body: Record<string, unknown> = {
        name: name.trim(),
        base_url: baseUrl.trim(),
        model_name: modelName.trim(),
        enabled,
        max_input_tokens: maxInput,
        max_output_tokens: maxOutput,
        max_turns: maxTurns,
      };
      if (apiKey.trim()) body.api_key = apiKey.trim();
      const resp = await adminFetch(`${API_BASE}/settings/providers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail ?? `HTTP ${resp.status}`);
      setName("");
      setBaseUrl("");
      setModelName("");
      setApiKey("");
      setMsg("已添加(自动加入降级链)");
      onAdded();
    } catch (err) {
      setMsg(`添加失败: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        className="btn-ghost"
        style={{ fontSize: 12, padding: "8px 14px", borderStyle: "dashed", alignSelf: "flex-start" }}
        onClick={() => setOpen(true)}
      >
        ＋ 添加模型
      </button>
    );
  }

  const inputStyle = {
    flex: 1, padding: "6px 10px", borderRadius: 6,
    border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)",
  } as const;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "12px 14px", borderRadius: "var(--radius-sm)", background: "rgba(255,255,255,0.4)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>名称</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="如 deepseek-flash / glm-4（仅字母/数字/下划线/连字符）" style={inputStyle} />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>API 地址</span>
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://.../v1" style={inputStyle} />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>模型名</span>
        <input value={modelName} onChange={(e) => setModelName(e.target.value)} placeholder="model-name" style={inputStyle} />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>API Key</span>
        <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="可选, 留空稍后录入" style={inputStyle} />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>参数上限</span>
        <input type="number" min={256} value={maxInput} onChange={(e) => setMaxInput(Number(e.target.value))} title="最大输入 tokens" style={{ width: 90, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }} />
        <input type="number" min={64} value={maxOutput} onChange={(e) => setMaxOutput(Number(e.target.value))} title="最大输出 tokens" style={{ width: 90, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }} />
        <input type="number" min={1} value={maxTurns} onChange={(e) => setMaxTurns(Number(e.target.value))} title="最大轮次" style={{ width: 70, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }} />
        <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>输入/输出/轮次</span>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          启用并加入降级链
        </label>
        <button className="btn-primary" style={{ fontSize: 12, padding: "6px 14px" }} onClick={() => void submit()} disabled={busy}>
          添加
        </button>
        <button className="btn-ghost" style={{ fontSize: 12, padding: "6px 12px" }} onClick={() => setOpen(false)} disabled={busy}>
          取消
        </button>
        {msg && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{msg}</span>}
      </div>
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
      const resp = await adminFetch(`${API_BASE}/settings/mcp/${encodeURIComponent(server.id)}/test`, {
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
      await adminFetch(`${API_BASE}/settings/mcp/${encodeURIComponent(server.id)}`, { method: "DELETE" });
      onChange();
    } catch (err) {
      setMsg(`删除失败: ${String(err)}`);
      setBusy(false);
    }
  };

  // V2 P2: "装配到对话"开关 —— 关闭后该 server 工具不进对话, 但配置保留
  const toggleAssemble = async (): Promise<void> => {
    setBusy(true);
    setMsg(null);
    try {
      const next = !(server.assemble !== false);
      const resp = await adminFetch(
        `${API_BASE}/settings/mcp/${encodeURIComponent(server.id)}/assemble`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ assemble: next }),
        }
      );
      if (!resp.ok) {
        setMsg("切换失败");
        return;
      }
      onChange();
    } catch (err) {
      setMsg(`切换失败: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  // 2026-08-04 设置页补齐: "启用"开关 —— enabled=false 时 server 整体停用
  const toggleEnabled = async (): Promise<void> => {
    setBusy(true);
    setMsg(null);
    try {
      const next = !(server.enabled !== false);
      const resp = await adminFetch(
        `${API_BASE}/settings/mcp/${encodeURIComponent(server.id)}/enabled`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: next }),
        }
      );
      if (!resp.ok) {
        setMsg("切换失败");
        return;
      }
      onChange();
    } catch (err) {
      setMsg(`切换失败: ${String(err)}`);
    } finally {
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
      {/* 2026-08-04 补齐: 启用/禁用开关 */}
      <label
        style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--text-secondary)", flexShrink: 0, cursor: "pointer" }}
        title={server.enabled !== false ? "已启用" : "已停用(整体不生效)"}
      >
        <input
          type="checkbox"
          checked={server.enabled !== false}
          onChange={() => void toggleEnabled()}
          disabled={busy}
        />
        启用
      </label>
      <label
        style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--text-secondary)", flexShrink: 0, cursor: "pointer" }}
        title={server.assemble !== false ? "工具已装配进对话, 模型可直接调用" : "已关闭: 该 server 工具不进入对话"}
      >
        <input
          type="checkbox"
          checked={server.assemble !== false}
          onChange={() => void toggleAssemble()}
          disabled={busy}
        />
        装配到对话
      </label>
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
  const [mode, setMode] = useState<"form" | "json">("form");
  const [type, setType] = useState<"http" | "stdio">("http");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [jsonText, setJsonText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const importJson = async (): Promise<void> => {
    if (!jsonText.trim()) {
      setMsg("请粘贴 JSON 配置");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/settings/mcp/import-json`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config_json: jsonText }),
      });
      const data = await resp.json();
      if (!data.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      const names = (data.imported ?? []).map((s: { id: string }) => s.id).join(", ");
      setJsonText("");
      setMsg(`✅ 已导入 ${data.count} 个: ${names}(重启后端后生效)`);
      onAdded();
    } catch (err) {
      setMsg(`导入失败: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

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
      if (authToken.trim()) {
        body.auth_token = authToken.trim();
      }
      const resp = await adminFetch(`${API_BASE}/settings/mcp`, {
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
      setAuthToken("");
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
      {/* 模式切换: 表单 / JSON 导入 */}
      <div style={{ display: "flex", gap: 4, marginBottom: 2 }}>
        {(["form", "json"] as const).map((m) => (
          <button
            key={m}
            className="btn-ghost"
            style={{
              fontSize: 11, padding: "4px 12px",
              background: mode === m ? "var(--gradient-indigo)" : "rgba(255,255,255,0.5)",
              color: mode === m ? "#fff" : "var(--text-primary)",
              border: "none",
            }}
            onClick={() => setMode(m)}
          >
            {m === "form" ? "表单填写" : "JSON 导入"}
          </button>
        ))}
        <span style={{ fontSize: 11, color: "var(--text-tertiary)", alignSelf: "center", marginLeft: 8 }}>
          {mode === "json" ? "支持 Claude Desktop 格式 mcpServers" : "逐项填写"}
        </span>
      </div>

      {mode === "json" ? (
        <>
          <textarea
            value={jsonText}
            onChange={(e) => setJsonText(e.target.value)}
            placeholder={'{\n  "mcpServers": {\n    "my-server": {\n      "url": "http://127.0.0.1:3000/mcp",\n      "headers": { "Authorization": "Bearer xxx" }\n    }\n  }\n}'}
            spellCheck={false}
            style={{ minHeight: 130, fontFamily: "monospace", fontSize: 11, padding: "8px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", background: "rgba(255,255,255,0.6)", resize: "vertical" }}
          />
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button className="btn-primary" style={{ fontSize: 12, padding: "6px 14px" }} onClick={() => void importJson()} disabled={busy}>
              导入
            </button>
            <button className="btn-ghost" style={{ fontSize: 12, padding: "6px 12px" }} onClick={() => setOpen(false)}>
              取消
            </button>
            {msg && <span style={{ fontSize: 12, color: msg.startsWith("✅") ? "var(--success-text)" : "var(--text-secondary)" }}>{msg}</span>}
          </div>
        </>
      ) : (
        <>
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
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56, flexShrink: 0 }}>API Key</span>
        <input
          type="password"
          value={authToken}
          onChange={(e) => setAuthToken(e.target.value)}
          placeholder="可选, 服务器要求 Bearer 认证时填写(AES 加密存储)"
          style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
        />
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button className="btn-primary" style={{ fontSize: 12, padding: "6px 14px" }} onClick={() => void submit()} disabled={busy}>
          添加
        </button>
        <button className="btn-ghost" style={{ fontSize: 12, padding: "6px 12px" }} onClick={() => setOpen(false)}>
          取消
        </button>
        {msg && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{msg}</span>}
      </div>
        </>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// 沙箱配置管理(§6.14 [MVP] 蓝图: GET/PUT config + POST test)
// ──────────────────────────────────────────────────────────────────────────────

interface SandboxConfig {
  enabled?: boolean;
  retention_days?: number;
  limits?: {
    cpu_timeout_sec?: number;
    memory_limit_mb?: number;
    disk_limit_mb?: number;
    network_enabled?: boolean;
  };
  security?: {
    code_scan_enabled?: boolean;
    env_sanitization_enabled?: boolean;
  };
}

function SandboxSection(): JSX.Element {
  const [cfg, setCfg] = useState<SandboxConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    try {
      const resp = await adminFetch(`${API_BASE}/settings/sandbox`);
      const data = await resp.json();
      setCfg(data);
      setMsg(null);
    } catch (e) {
      setMsg(`加载沙箱配置失败: ${String(e)}`);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (): Promise<void> => {
    if (!cfg) return;
    setBusy(true);
    try {
      const resp = await adminFetch(`${API_BASE}/settings/sandbox`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: cfg.enabled,
          cpu_timeout_sec: cfg.limits?.cpu_timeout_sec,
          memory_limit_mb: cfg.limits?.memory_limit_mb,
          disk_limit_mb: cfg.limits?.disk_limit_mb,
          network_enabled: cfg.limits?.network_enabled,
          code_scan_enabled: cfg.security?.code_scan_enabled,
          env_sanitization_enabled: cfg.security?.env_sanitization_enabled,
          retention_days: cfg.retention_days,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail ?? `HTTP ${resp.status}`);
      setMsg("✅ 沙箱配置已保存(下次执行生效)");
    } catch (e) {
      setMsg(`保存失败: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const runTest = async (): Promise<void> => {
    setBusy(true);
    setTestResult("测试中...");
    try {
      const resp = await adminFetch(`${API_BASE}/settings/sandbox/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: "import time\nprint('sandbox ok, time:', time.strftime('%H:%M:%S'))",
          language: "python",
        }),
      });
      const data = await resp.json();
      setTestResult(
        data.ok
          ? `✅ 执行成功 (exit=${data.exit_code}, ${data.duration_ms}ms)\n${data.stdout || ""}${data.stderr ? `\n[stderr] ${data.stderr}` : ""}`
          : `❌ 执行失败 (exit=${data.exit_code})\n${data.stderr || data.stdout || ""}`
      );
    } catch (e) {
      setTestResult(`测试请求失败: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-panel animate-in delay-3" style={{ padding: "20px 24px" }}>
      <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
        沙箱配置
      </div>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 16 }}>
        §6.14 代码执行沙箱参数(内存/超时/磁盘/网络/扫描) · 修改后下次执行生效 · 可用"测试执行"验证
      </div>

      {!cfg ? (
        <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>加载中...</div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
              启用沙箱
              <input
                type="checkbox"
                checked={cfg.enabled ?? true}
                onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })}
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
              内存上限 MB
              <input
                type="number"
                value={cfg.limits?.memory_limit_mb ?? 512}
                onChange={(e) =>
                  setCfg({ ...cfg, limits: { ...cfg.limits, memory_limit_mb: Number(e.target.value) } })
                }
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
              超时秒
              <input
                type="number"
                value={cfg.limits?.cpu_timeout_sec ?? 300}
                onChange={(e) =>
                  setCfg({ ...cfg, limits: { ...cfg.limits, cpu_timeout_sec: Number(e.target.value) } })
                }
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
              磁盘上限 MB
              <input
                type="number"
                value={cfg.limits?.disk_limit_mb ?? 100}
                onChange={(e) =>
                  setCfg({ ...cfg, limits: { ...cfg.limits, disk_limit_mb: Number(e.target.value) } })
                }
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
              网络
              <input
                type="checkbox"
                checked={cfg.limits?.network_enabled ?? false}
                onChange={(e) =>
                  setCfg({ ...cfg, limits: { ...cfg.limits, network_enabled: e.target.checked } })
                }
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
              代码扫描
              <input
                type="checkbox"
                checked={cfg.security?.code_scan_enabled ?? true}
                onChange={(e) =>
                  setCfg({ ...cfg, security: { ...cfg.security, code_scan_enabled: e.target.checked } })
                }
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
              环境变量脱敏
              <input
                type="checkbox"
                checked={cfg.security?.env_sanitization_enabled ?? true}
                onChange={(e) =>
                  setCfg({ ...cfg, security: { ...cfg.security, env_sanitization_enabled: e.target.checked } })
                }
              />
            </label>
            <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
              工作目录保留天数
              <input
                type="number"
                value={cfg.retention_days ?? 7}
                onChange={(e) => setCfg({ ...cfg, retention_days: Number(e.target.value) })}
              />
            </label>
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
            <button onClick={() => void save()} disabled={busy} style={btnStyle}>
              {busy ? "保存中..." : "保存配置"}
            </button>
            <button onClick={() => void runTest()} disabled={busy} style={btnStyle}>
              {busy ? "测试中..." : "测试执行"}
            </button>
          </div>

          {msg && <div style={{ fontSize: 12, marginTop: 10 }}>{msg}</div>}
          {testResult && (
            <pre style={{ fontSize: 12, marginTop: 10, whiteSpace: "pre-wrap", background: "var(--panel-bg, rgba(0,0,0,0.04))", padding: 10, borderRadius: 8 }}>
              {testResult}
            </pre>
          )}
        </>
      )}
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  fontSize: 13,
  padding: "6px 14px",
  borderRadius: 8,
  border: "1px solid var(--border-color, rgba(128,128,128,0.3))",
  background: "var(--panel-bg, rgba(255,255,255,0.6))",
  cursor: "pointer",
};

// ──────────────────────────────────────────────────────────────────────────────
// 主题壁纸板块(Phase 1.5)
// ──────────────────────────────────────────────────────────────────────────────

function WallpaperSection(): JSX.Element {
  const [wallpaper, setWallpaper] = useState<string | null>(null);
  const [wpType, setWpType] = useState<"image" | "video">("image");
  const [fit, setFit] = useState<"cover" | "contain">("cover");
  const [posX, setPosX] = useState(50);
  const [posY, setPosY] = useState(50);
  const [scale, setScale] = useState(100);
  const [rotate, setRotate] = useState(0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // 2026-08-04 加固: video/img 加载失败时显示提示(原代码静默失败用户看不到原因)
  const [loadError, setLoadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoadError(null);
    try {
      const resp = await adminFetch(`${API_BASE}/wallpaper`);
      const data = await resp.json();
      if (data.wallpaper) {
        // 用 127.0.0.1 避免 Electron IPv6(::1) 解析坑; 加时间戳防浏览器缓存
        const url = `${FILES_BASE}/${data.wallpaper.split("/").pop()}?t=${Date.now()}`;
        setWallpaper(url);
        setWpType(data.type === "video" ? "video" : "image");
      } else {
        setWallpaper(null);
      }
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
      const resp = await adminFetch(`${API_BASE}/wallpaper/style`, {
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
    const isVideo = file.type === "video/mp4" || file.type === "video/webm";
    const limit = isVideo ? 50 * 1024 * 1024 : 6 * 1024 * 1024;
    if (file.size > limit) {
      setMsg(isVideo ? "视频超过 50MB, 请压缩后再试" : "图片超过 6MB, 请压缩后再试");
      return;
    }
    if (!isVideo && !/^image\/(png|jpeg|webp)$/.test(file.type)) {
      setMsg("仅支持 PNG / JPG / WebP 图片或 MP4 / WebM 视频");
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
      const resp = await adminFetch(`${API_BASE}/wallpaper`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data_url: dataUrl }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      // 换图后 URL 必须不同, 否则 <img>/<video> 复用浏览器缓存的旧背景
      setWallpaper(
        data.wallpaper
          ? `${FILES_BASE}/${data.wallpaper.split("/").pop()}?t=${Date.now()}`
          : null
      );
      setWpType(data.type === "video" ? "video" : "image");
      setMsg(data.type === "video" ? "动态背景已更新, 首页将循环播放" : "壁纸已更新, 首页将使用新背景");
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
      await adminFetch(`${API_BASE}/wallpaper`, { method: "DELETE" });
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
        设置首页顶部背景: 静态图 (PNG / JPG / WebP, ≤6MB) 或动态视频
        (MP4 / WebM, ≤50MB, 首页自动循环播放); 可调整显示位置与填充方式
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
          {wallpaper && wpType === "video" && (
            <video
              src={wallpaper}
              autoPlay
              loop
              muted
              playsInline
              onError={() =>
                setLoadError(`视频加载失败: ${wallpaper}(检查后端 sidecar 是否运行)`)
              }
              onLoadedData={() => setLoadError(null)}
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
          {wallpaper && wpType === "image" && (
            <img
              src={wallpaper}
              alt="当前壁纸"
              onError={() =>
                setLoadError(`图片加载失败: ${wallpaper}(检查后端 sidecar 是否运行)`)
              }
              onLoad={() => setLoadError(null)}
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
              {wallpaper
                ? wpType === "video"
                  ? "更换视频"
                  : "更换壁纸"
                : "上传背景"}
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,video/mp4,video/webm"
                style={{ display: "none" }}
                onChange={(e) => void onFileChange(e)}
              />
            </label>
            {wallpaper && (
              <button className="btn-ghost" onClick={() => void remove()} disabled={busy}>
                移除背景
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
          {/* 2026-08-04: 资源加载失败时直接显示原因,避免静默空白 */}
          {loadError && (
            <div style={{ fontSize: 11, color: "#d32f2f", marginTop: 4 }}>{loadError}</div>
          )}
        </div>
      </div>
    </div>
  );
}
