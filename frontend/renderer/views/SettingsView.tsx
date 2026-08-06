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
  // V1.4-8.2: 分组元数据
  group?: string | null;
  sort_order?: number;
  kind?: string; // cloud | local
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
  // V1.2-6.2: 环境变量(stdio 子进程注入)
  env?: Record<string, string>;
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
          {/* V1.4-8.2: 按 group 分组 + sort_order 排序渲染 */}
          {(() => {
            const groups = new Map<string, ProviderInfo[]>();
            for (const p of [...providers].sort(
              (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0)
            )) {
              const g = (p.group || "").trim() || "未分组";
              if (!groups.has(g)) groups.set(g, []);
              groups.get(g)!.push(p);
            }
            return Array.from(groups.entries()).map(([gname, list]) => (
              <div key={gname}>
                <div
                  style={{
                    fontSize: 12, fontWeight: 600, color: "var(--text-tertiary)",
                    margin: "8px 0 6px", letterSpacing: "0.02em",
                  }}
                >
                  {gname} ({list.length})
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {list.map((p) => (
                    <ProviderRow key={p.name} provider={p} onSaved={load} onDelete={handleDeleteProvider} />
                  ))}
                </div>
              </div>
            ));
          })()}
          {providers.length === 0 && (
            <div style={{ fontSize: 13, color: "var(--text-tertiary)", padding: "16px 0", textAlign: "center" }}>
              ⚠️ 尚未配置任何模型。<b>在下方向"添加模型"填入你的模型服务即可开始对话</b>
              (任意 OpenAI 兼容服务: 名称 + Base URL + 模型名 + API Key)
            </div>
          )}
          <ProviderAddForm onAdded={load} />
        </div>
      </div>

      {/* 2026-08-06: 数据库连接配置(打包版首次使用必配; 密码仅存本地 .env) */}
      <DatabaseSection />

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

      {/* V1.3-7.2 工作流自动化: Hooks 配置 */}
      <HooksSection />

      {/* V1.4-8.1 数据管理: 备份/还原/批量导出 */}
      <DataSection />

      {/* V1.4-8.3 系统设置: 日志/代理/缓存/master key */}
      <SystemSection />

      {/* 关于与更新 */}
      <UpdateSection />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// 数据库连接配置(2026-08-06 打包版首启能力)
// 打包版 backend 只读(resourcesPath), .env 被排除; 数据库密码与密钥
// (PA_DB_PASSWORD / PA_MASTER_KEY / PA_ADMIN_TOKEN)统一写入 Electron 用户配置
// %APPDATA%\Private Agent\backend.env —— 保存后需重启应用生效(连接池启动时创建)。
// ──────────────────────────────────────────────────────────────────────────────

function DatabaseSection(): JSX.Element {
  const [host, setHost] = useState("");
  const [port, setPort] = useState("5432");
  const [name, setName] = useState("");
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [masterKey, setMasterKey] = useState("");
  // 2026-08-06: 当前生效主密钥(只读展示, 供备份/迁移 —— 升级/重装不丢,
  // 无需人工记忆)
  const [currentMasterKey, setCurrentMasterKey] = useState("");
  const [envFile, setEnvFile] = useState("");
  const [passwordConfigured, setPasswordConfigured] = useState(false);
  const [dbReachable, setDbReachable] = useState<boolean | null>(null);
  const [status, setStatus] = useState("加载中…");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void adminFetch(`${API_BASE}/settings/database`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) {
          setStatus("加载失败(后端未连接?)");
          return;
        }
        setHost(d.host ?? "");
        setPort(String(d.port ?? 5432));
        setName(d.name ?? "");
        setUser(d.user ?? "");
        setEnvFile(d.env_file ?? "");
        setCurrentMasterKey(d.master_key ?? "");
        setPasswordConfigured(Boolean(d.password_configured));
        setDbReachable(Boolean(d.db_reachable));
        setStatus(
          (d.password_configured ? "数据库密码已配置 ✓" : "数据库密码未配置 ⚠️") +
            (d.master_key_configured ? " · 密钥稳定 ✓" : " · 密钥未配置 ⚠️")
        );
      })
      .catch(() => setStatus("加载失败(后端未连接?)"));
  }, []);

  const save = async (): Promise<void> => {
    // 2026-08-06: 密码已配置时可留空(不修改); 未配置且为空 → 阻止
    if (!password.trim() && !passwordConfigured) {
      window.alert("请输入数据库密码(首次配置必填; 已配置后可留空不修改)");
      return;
    }
    setBusy(true);
    try {
      const resp = await adminFetch(`${API_BASE}/settings/database`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host: host.trim(),
          port: Number.parseInt(port, 10) || 5432,
          name: name.trim(),
          user: user.trim(),
          password: password.trim() || undefined,
          master_key: masterKey.trim() || undefined,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setEnvFile(data.env_file ?? envFile);
      setPassword("");
      setStatus(`✅ ${data.message ?? "已保存"} → ${data.env_file ?? ""}`);
    } catch (e) {
      setStatus(`保存失败: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-panel animate-in delay-1" style={{ padding: "20px 24px" }}>
      <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
        🗄️ 数据库
      </div>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 12 }}>
        PostgreSQL 连接配置(打包版首次使用必配; 密码仅写入本地用户配置, 不回显)
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 8, marginBottom: 8 }}>
        {[
          { label: "主机", value: host, set: setHost, ph: "127.0.0.1" },
          { label: "端口", value: port, set: setPort, ph: "5432" },
          { label: "库名", value: name, set: setName, ph: "private_agent" },
          { label: "用户", value: user, set: setUser, ph: "postgres" },
        ].map((f) => (
          <label key={f.label} style={{ fontSize: 12, color: "var(--text-secondary)", display: "flex", flexDirection: "column", gap: 4 }}>
            {f.label}
            <input
              value={f.value}
              placeholder={f.ph}
              onChange={(e) => f.set(e.target.value)}
              style={{
                fontSize: 13, padding: "6px 10px", borderRadius: 8,
                border: "1px solid rgba(148,163,184,0.4)", background: "#fff",
                color: "var(--text-primary)", outline: "none",
              }}
            />
          </label>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
        <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            密码
            {/* 2026-08-06: 密码配置状态徽标(空框不再误导) */}
            <span
              style={{
                fontSize: 10, padding: "1px 8px", borderRadius: 8,
                background: passwordConfigured ? "#d1fae5" : "#fef3c7",
                color: passwordConfigured ? "#047857" : "#b45309",
                fontWeight: 600,
              }}
            >
              {passwordConfigured ? "✓ 已配置" : "未配置"}
            </span>
          </span>
          <input
            type="password"
            value={password}
            placeholder={
              passwordConfigured
                ? "已配置(留空保存 = 不修改密码)"
                : "PostgreSQL 密码(首次配置必填)"
            }
            onChange={(e) => setPassword(e.target.value)}
            style={{
              fontSize: 13, padding: "6px 10px", borderRadius: 8,
              border: "1px solid rgba(148,163,184,0.4)", background: "#fff",
              color: "var(--text-primary)", outline: "none",
            }}
          />
        </label>
        <button
          onClick={() => void save()}
          disabled={busy}
          style={{
            marginTop: 20, fontSize: 13, padding: "6px 18px", borderRadius: 8,
            border: "1px solid #6366f1", background: "#6366f1", color: "#fff",
            cursor: busy ? "not-allowed" : "pointer", fontWeight: 600, opacity: busy ? 0.6 : 1,
          }}
        >
          {busy ? "保存中…" : "保存"}
        </button>
      </div>
      <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "flex", flexDirection: "column", gap: 4, marginBottom: 4 }}>
        AES 主密钥(可选)
        <input
          type="password"
          value={masterKey}
          placeholder="PA_MASTER_KEY(64位hex, 留空自动生成; 保留旧环境密钥以解密已存 API Key)"
          onChange={(e) => setMasterKey(e.target.value)}
          style={{
            fontSize: 13, padding: "6px 10px", borderRadius: 8,
            border: "1px solid rgba(148,163,184,0.4)", background: "#fff",
            color: "var(--text-primary)", outline: "none", fontFamily: "var(--font-mono)",
          }}
        />
      </label>
      {currentMasterKey && (
        <div
          style={{
            fontSize: 12, color: "var(--text-secondary)", display: "flex",
            alignItems: "center", gap: 8, marginBottom: 4,
          }}
        >
          <span style={{ flexShrink: 0 }}>🔑 当前主密钥(本机已持久化, 升级/重装不丢):</span>
          <code
            style={{
              flex: 1, fontSize: 11, fontFamily: "var(--font-mono)",
              background: "#f8fafc", border: "1px solid #e2e8f0",
              borderRadius: 6, padding: "4px 8px", overflow: "hidden",
              textOverflow: "ellipsis", whiteSpace: "nowrap",
            }}
            title={currentMasterKey}
          >
            {currentMasterKey}
          </code>
          <button
            onClick={() => {
              void navigator.clipboard.writeText(currentMasterKey);
              setStatus("主密钥已复制(迁移/备份用)");
            }}
            style={{
              fontSize: 11, padding: "4px 10px", borderRadius: 6,
              border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer",
              color: "#475569", flexShrink: 0,
            }}
          >
            复制
          </button>
        </div>
      )}
      <div style={{ fontSize: 12, marginTop: 4, color: status.startsWith("保存失败") ? "var(--danger-text)" : "var(--text-tertiary)" }}>
        {status}
      </div>
      {/* 2026-08-06: 数据库连接状态(加载时探测; 未配置/未重启时不可达属正常) */}
      {dbReachable !== null && (
        <div
          style={{
            fontSize: 11, marginTop: 4,
            color: dbReachable ? "#047857" : "#b45309",
            fontWeight: 500,
          }}
        >
          {dbReachable
            ? "🟢 数据库连接正常(后端已连上 PostgreSQL)"
            : "🟠 数据库未连接(首次配置后需重启应用生效; 或密码/主机有误)"}
        </div>
      )}
      <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 6 }}>
        ⚠️ 保存后需重启应用生效(后端数据库连接池在启动时创建)
        {envFile ? ` · 配置位置: ${envFile}` : ""}
      </div>
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
  // V1.2-6.1: 提示词编辑器
  const [promptEditor, setPromptEditor] = useState<{
    name: string;
    text: string;
    tokens: number | null;
    version: string;
  } | null>(null);
  const [savingPrompt, setSavingPrompt] = useState(false);
  const [promptMsg, setPromptMsg] = useState<string | null>(null);

  // V1.2-6.1: 打开提示词编辑器(加载当前内容 + token 估算)
  const openPromptEditor = async (name: string): Promise<void> => {
    setPromptMsg(null);
    try {
      const resp = await adminFetch(`http://127.0.0.1:8765/admin/skills/${name}/prompt`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setPromptEditor({
        name: data.name,
        text: data.system_prompt ?? "",
        tokens: data.token_count,
        version: data.version,
      });
    } catch (e) {
      setPromptMsg(`加载失败: ${String(e)}`);
    }
  };

  // V1.2-6.1: 保存提示词(落盘 + 自动快照 + 同步 PG)
  const savePrompt = async (): Promise<void> => {
    if (!promptEditor) return;
    setSavingPrompt(true);
    setPromptMsg(null);
    try {
      const resp = await adminFetch(`http://127.0.0.1:8765/admin/skills/${promptEditor.name}/prompt`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ system_prompt: promptEditor.text }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setPromptEditor((prev) => (prev ? { ...prev, tokens: data.token_count } : prev));
      setPromptMsg("已保存(自动生成快照, 旧会话将自动重建 Frozen Zone)");
    } catch (e) {
      setPromptMsg(`保存失败: ${String(e)}`);
    } finally {
      setSavingPrompt(false);
    }
  };

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
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12, padding: "4px 8px 4px 12px", borderRadius: 12,
              border: s.enabled ? "1px solid #4caf50" : "1px solid #ccc",
              color: s.enabled ? "#2e7d32" : "var(--text-tertiary)",
              background: s.enabled ? "rgba(76,175,80,0.08)" : "transparent",
            }}
          >
            {s.name} v{s.version} {s.enabled ? "· 已启用" : "· 未启用"}
            {/* V1.2-6.1: 提示词编辑器入口 */}
            <button
              onClick={() => void openPromptEditor(s.name)}
              title={`编辑 ${s.name} 的系统提示词`}
              style={{
                border: "none", background: "transparent", cursor: "pointer",
                fontSize: 12, padding: "2px 4px", color: "#8b5cf6",
              }}
            >
              ✏️
            </button>
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

      {/* V1.2-6.1: 提示词编辑器弹窗 */}
      {promptEditor && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 200,
            background: "rgba(15,23,42,0.4)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
          onClick={() => setPromptEditor(null)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 680, maxWidth: "92vw", maxHeight: "85vh",
              display: "flex", flexDirection: "column",
              background: "#fff", borderRadius: 14,
              padding: "20px 24px",
              boxShadow: "0 20px 60px rgba(15,23,42,0.25)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
              <span style={{ fontSize: 16, fontWeight: 700 }}>
                ✏️ 编辑系统提示词 · {promptEditor.name}
                <span style={{ marginLeft: 8, fontSize: 11, color: "var(--text-tertiary)", fontWeight: 400 }}>
                  v{promptEditor.version}
                </span>
              </span>
              <button
                onClick={() => setPromptEditor(null)}
                style={{ border: "none", background: "transparent", fontSize: 18, cursor: "pointer", color: "#64748b" }}
              >
                ×
              </button>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 8 }}>
              保存后自动生成快照(scope=prompt)；提示词变更会使旧会话续聊时自动重建上下文。
            </div>
            <textarea
              value={promptEditor.text}
              onChange={(e) => setPromptEditor({ ...promptEditor, text: e.target.value })}
              style={{
                flex: 1, minHeight: 320, resize: "vertical",
                fontFamily: "Consolas, monospace", fontSize: 13,
                border: "1px solid #e2e8f0", borderRadius: 8, padding: 12,
                color: "#334155", background: "#f8fafc",
                whiteSpace: "pre", lineHeight: 1.6,
              }}
            />
            <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
              <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                token 估算: {promptEditor.tokens ?? "—"}（保存后刷新）
              </span>
              <span style={{ flex: 1 }} />
              {promptMsg && (
                <span style={{ fontSize: 12, color: promptMsg.startsWith("保存失败") ? "#d32f2f" : "#059669" }}>
                  {promptMsg}
                </span>
              )}
              <button
                className="btn-primary"
                style={{ padding: "8px 20px", fontSize: 13 }}
                onClick={() => void savePrompt()}
                disabled={savingPrompt}
              >
                {savingPrompt ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// 关于与更新: 版本信息 + 检查更新 + 应用内一键升级(2026-08-06)
// 流程: 检查 → 发现新版本(施工文件夹发布) → 下载(进度) → 静默安装重启;
// 升级不触碰 %APPDATA%\Private Agent 用户配置与数据库(数据/记忆/密钥/技能/
// MCP/LLM 配置全部保留)。
// ──────────────────────────────────────────────────────────────────────────────

interface UpdateInfo {
  hasUpdate?: boolean;
  currentVersion?: string;
  latestVersion?: string;
  releaseUrl?: string;
  notes?: string;
  failed?: boolean;
  asset?: { name: string; url: string; sha256?: string };
}

function UpdateSection(): JSX.Element {
  const [checking, setChecking] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [installerPath, setInstallerPath] = useState("");

  // 订阅下载进度(主进程推送)
  useEffect(() => {
    const off = window.pa?.onUpdateProgress?.((p) => {
      setProgress(p.percent ?? 0);
    });
    return () => off?.();
  }, []);

  const runCheck = async (): Promise<void> => {
    setChecking(true);
    setResult(null);
    setInstallerPath("");
    setProgress(null);
    try {
      const r = (await window.pa?.checkForUpdates?.()) as UpdateInfo;
      setUpdateInfo(r ?? null);
      if (!r) {
        setResult("无法检查更新(请在打包版中使用)");
      } else if (r.failed) {
        setResult(`检查失败: ${r.notes || "未知错误"}`);
      } else if (r.hasUpdate) {
        setResult(
          `发现新版本 ${r.latestVersion}(当前 ${r.currentVersion})${r.asset ? "\n安装包就绪, 点击下方「下载更新」一键升级" : ""}`
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

  const runDownload = async (): Promise<void> => {
    if (!updateInfo?.asset) {
      setResult("未找到安装包资产(发布者未上传?)");
      return;
    }
    setDownloading(true);
    setProgress(0);
    setResult("正在下载更新…");
    try {
      const r = (await window.pa?.downloadUpdate?.(updateInfo.asset)) as {
        path: string;
        size: number;
        sha256: string;
        error?: string;
      };
      if (!r || r.error) {
        setResult(`下载失败: ${r?.error || "未知错误"}`);
      } else {
        setInstallerPath(r.path);
        setResult(`下载完成(${(r.size / 1024 / 1024).toFixed(1)} MB), sha256 校验通过 ✓`);
      }
    } catch (e) {
      setResult(`下载出错: ${String(e)}`);
    } finally {
      setDownloading(false);
      setProgress(null);
    }
  };

  const runInstall = async (): Promise<void> => {
    if (!installerPath) return;
    setResult("正在静默安装, 完成后将自动重启新版本…");
    const r = (await window.pa?.installUpdate?.(installerPath)) as
      | { ok: boolean; error?: string }
      | undefined;
    if (!r?.ok) {
      setResult(`安装启动失败: ${r?.error || "未知错误"}`);
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
          disabled={checking || downloading}
        >
          {checking ? "检查中..." : "检查更新"}
        </button>
        <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
          当前版本 v{window.pa?.versions?.app || "0.1.0"}
        </span>
      </div>
      {updateInfo?.hasUpdate && !installerPath && (
        <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 10 }}>
          <button
            className="btn-primary"
            style={{ padding: "8px 18px", fontSize: 13 }}
            onClick={() => void runDownload()}
            disabled={downloading}
          >
            {downloading ? `下载中 ${progress ?? 0}%` : "下载更新"}
          </button>
          {downloading && (
            <div
              style={{
                width: 200, height: 8, borderRadius: 4, background: "#e2e8f0",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  height: "100%", width: `${progress ?? 0}%`,
                  background: "linear-gradient(90deg,#818cf8,#6366f1)",
                  transition: "width 0.3s",
                }}
              />
            </div>
          )}
        </div>
      )}
      {installerPath && (
        <button
          className="btn-primary"
          style={{ padding: "8px 18px", fontSize: 13, marginTop: 10 }}
          onClick={() => void runInstall()}
        >
          ⬆ 安装并重启(升级完成)
        </button>
      )}
      {result && (
        <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", marginTop: 10, color: "var(--text-secondary)" }}>
          {result}
        </pre>
      )}
      {updateInfo?.notes && (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 6, whiteSpace: "pre-wrap" }}>
          更新说明: {updateInfo.notes}
        </div>
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
  // V1.4-8.2: 分组元数据
  const [group, setGroup] = useState(provider.group ?? "");
  const [kind, setKind] = useState(provider.kind ?? "cloud");
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
    setGroup(provider.group ?? "");
    setKind(provider.kind ?? "cloud");
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
      body.group = group.trim() || "";
      body.kind = kind;
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
            {provider.kind === "local" && (
              <span style={{ fontSize: 11, padding: "1px 8px", borderRadius: 10, background: "#ede9fe", color: "#6d28d9" }}>
                本地
              </span>
            )}
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
            {/* V1.4-8.2: 分组 + 类型 */}
            <input
              value={group}
              onChange={(e) => setGroup(e.target.value)}
              placeholder="分组(如: 主力模型)"
              style={{ width: 140, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
            />
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)" }}
            >
              <option value="cloud">云端模型</option>
              <option value="local">本地模型</option>
            </select>
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
      {/* V1.2-6.2: 环境变量标识(仅展示键名, 值不展示) */}
      {server.env && Object.keys(server.env).length > 0 && (
        <span
          title={`环境变量: ${Object.keys(server.env).join(", ")}`}
          style={{
            fontSize: 10, padding: "1px 8px", borderRadius: 10,
            background: "rgba(129,140,248,0.12)", color: "#4f46e5",
            flexShrink: 0, cursor: "help",
          }}
        >
          env {Object.keys(server.env).length}
        </span>
      )}
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
  // V1.2-6.2: 环境变量配置(每行 KEY=VALUE, stdio 模式注入子进程)
  const [envText, setEnvText] = useState("");
  const [jsonText, setJsonText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // V1.5 项-2 连接器开箱即用: 预置模板(纯配置不含凭证, 选中填充表单)
  interface McpTemplate {
    id: string;
    name: string;
    description: string;
    type: "http" | "stdio";
    command: string | null;
    args: string[];
    url: string | null;
    env: Record<string, string>;
    timeout_sec: number;
    protocol_version: string;
    requires: string[];
  }
  const [templates, setTemplates] = useState<McpTemplate[]>([]);
  const [templateNotes, setTemplateNotes] = useState<string[]>([]);

  // 打开表单时拉取模板列表
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void adminFetch(`${API_BASE}/mcp/templates`)
      .then((r) => (r.ok ? r.json() : { templates: [] }))
      .then((d) => {
        if (!cancelled) setTemplates(d.templates ?? []);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [open]);

  // 选中模板 → 填充表单(用户只补凭证/目录等个性化字段)
  const applyTemplate = (id: string): void => {
    const t = templates.find((x) => x.id === id);
    if (!t) return;
    setType(t.type);
    setName(t.id); // 模板 id 作为默认 server 名称(可改)
    setUrl(t.url ?? "");
    setCommand(t.command ?? "");
    setArgs((t.args ?? []).join(" "));
    setAuthToken("");
    setEnvText(
      Object.entries(t.env ?? {})
        .map(([k, v]) => `${k}=${v}`)
        .join("\n")
    );
    setTemplateNotes(t.requires ?? []);
    setMsg(null);
  };

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
      // V1.2-6.2: 解析 env(每行 KEY=VALUE, # 开头为注释)
      const env: Record<string, string> = {};
      for (const line of envText.split(/\r?\n/)) {
        const t = line.trim();
        if (!t || t.startsWith("#")) continue;
        const eq = t.indexOf("=");
        if (eq <= 0) continue;
        env[t.slice(0, eq).trim()] = t.slice(eq + 1).trim();
      }
      if (Object.keys(env).length > 0) {
        body.env = env;
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
      setEnvText("");
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
      {/* V1.5 项-2: 从模板添加(连接器开箱即用, 选中即填充) */}
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56, flexShrink: 0 }}>从模板</span>
        <select
          value=""
          onChange={(e) => applyTemplate(e.target.value)}
          style={{
            flex: 1, padding: "6px 10px", borderRadius: 6,
            border: "1px solid rgba(148,163,184,0.3)", fontSize: 12,
            background: "rgba(255,255,255,0.6)", color: "var(--text-primary)",
          }}
          title="选择预置连接器模板, 自动填充下方字段"
        >
          <option value="">选择预置模板…(fetch/time/filesystem/github 等)</option>
          {templates.map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
      </div>
      {templateNotes.length > 0 && (
        <div
          style={{
            fontSize: 11, color: "#92400e", background: "#fffbeb",
            border: "1px solid #fde68a", borderRadius: 6, padding: "6px 10px",
            lineHeight: 1.5,
          }}
        >
          ⚠️ 需补充: {templateNotes.join("；")}
        </div>
      )}
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
      {/* V1.2-6.2: 环境变量配置(stdio 子进程注入) */}
      <div style={{ display: "flex", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56, flexShrink: 0 }}>环境变量</span>
        <textarea
          value={envText}
          onChange={(e) => setEnvText(e.target.value)}
          placeholder={'每行 KEY=VALUE, 如:\nMY_API_KEY=sk-xxx\nBASE_URL=http://127.0.0.1:3000'}
          rows={3}
          spellCheck={false}
          style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "rgba(255,255,255,0.6)", resize: "vertical", fontFamily: "monospace" }}
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

// ──────────────────────────────────────────────────────────────────────────────
// V1.3-7.2 工作流自动化: Hooks 配置(事件订阅, CRUD 端点后端已有)
// ──────────────────────────────────────────────────────────────────────────────
const HOOK_EVENT_LABELS: Record<string, string> = {
  user_prompt_submit: "用户提交时",
  pre_tool_use: "工具调用前",
  post_tool_use: "工具调用后",
  stop: "停止/结束时",
  pre_compact: "压缩前",
  permission_request: "权限请求时",
};

const HOOK_TYPES = ["command", "http", "mcp_tool"];

interface HookItem {
  name: string;
  event: string;
  type: string;
  command?: string | null;
  url?: string | null;
  mcp_server?: string | null;
  mcp_tool?: string | null;
  timeout: number;
  enabled: boolean;
}

function HooksSection(): JSX.Element {
  const [hooks, setHooks] = useState<HookItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // 编辑态: null=新增
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    event: "user_prompt_submit",
    type: "command",
    command: "",
    url: "",
    mcp_server: "",
    mcp_tool: "",
    timeout: 5,
    enabled: true,
  });

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const resp = await adminFetch(`${API_BASE}/hooks`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setHooks(Array.isArray(data.hooks) ? data.hooks : []);
    } catch (e) {
      setMsg(`加载失败: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const startEdit = (h: HookItem): void => {
    setEditing(h.name);
    setForm({
      name: h.name,
      event: h.event,
      type: h.type,
      command: h.command ?? "",
      url: h.url ?? "",
      mcp_server: h.mcp_server ?? "",
      mcp_tool: h.mcp_tool ?? "",
      timeout: Number(h.timeout) || 5,
      enabled: h.enabled !== false,
    });
    setMsg(null);
  };

  const save = async (): Promise<void> => {
    if (!form.name.trim()) {
      setMsg("name 不能为空");
      return;
    }
    const body: Record<string, unknown> = {
      name: form.name.trim(),
      event: form.event,
      type: form.type,
      timeout: Number(form.timeout) || 5,
      enabled: form.enabled,
    };
    if (form.type === "command") body.command = form.command.trim() || null;
    if (form.type === "http") body.url = form.url.trim() || null;
    if (form.type === "mcp_tool") {
      body.mcp_server = form.mcp_server.trim() || null;
      body.mcp_tool = form.mcp_tool.trim() || null;
    }
    try {
      const url = editing
        ? `${API_BASE}/hooks/${encodeURIComponent(editing)}`
        : `${API_BASE}/hooks`;
      const resp = await adminFetch(url, {
        method: editing ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail ?? `HTTP ${resp.status}`);
      setMsg(editing ? `已更新 ${form.name}` : `已新增 ${form.name}(重启后端后生效)`);
      setEditing(null);
      void load();
    } catch (e) {
      setMsg(`保存失败: ${String(e)}`);
    }
  };

  const remove = async (name: string): Promise<void> => {
    if (!window.confirm(`删除 hook "${name}"?`)) return;
    try {
      const resp = await adminFetch(`${API_BASE}/hooks/${encodeURIComponent(name)}`, {
        method: "DELETE",
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setMsg(`已删除 ${name}`);
      void load();
    } catch (e) {
      setMsg(`删除失败: ${String(e)}`);
    }
  };

  const inputStyle = {
    padding: "6px 10px", borderRadius: 6, fontSize: 12,
    border: "1px solid rgba(148,163,184,0.3)",
    background: "rgba(255,255,255,0.6)",
  } as const;

  return (
    <div
      className="glass-panel animate-in delay-2"
      style={{ padding: "18px 22px", display: "flex", flexDirection: "column", gap: 12 }}
    >
      <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em" }}>
        工作流钩子 (Hooks)
      </div>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
        在关键事件点执行外部命令/HTTP 请求/MCP 工具(可改写输入、追加上下文、阻断执行)
      </div>

      {msg && <div style={{ fontSize: 12, color: msg.startsWith("失败") ? "#dc2626" : "#059669" }}>{msg}</div>}

      {/* 列表 */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {loading && <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>加载中…</div>}
        {!loading && hooks.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>暂无 hooks</div>
        )}
        {hooks.map((h) => (
          <div
            key={h.name}
            style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "8px 12px", borderRadius: 8,
              background: "rgba(255,255,255,0.5)",
              fontSize: 12,
            }}
          >
            <span
              style={{
                flexShrink: 0, fontSize: 11, fontWeight: 600,
                padding: "2px 8px", borderRadius: 10,
                background: h.enabled ? "rgba(76,175,80,0.12)" : "rgba(148,163,184,0.15)",
                color: h.enabled ? "#2e7d32" : "#64748b",
              }}
            >
              {h.enabled ? "启用" : "停用"}
            </span>
            <span style={{ fontWeight: 600, color: "var(--text-primary)", flexShrink: 0 }}>
              {h.name}
            </span>
            <span style={{ color: "var(--text-secondary)", flexShrink: 0 }}>
              {HOOK_EVENT_LABELS[h.event] ?? h.event}
            </span>
            <span
              style={{
                flex: 1, minWidth: 0, overflow: "hidden",
                textOverflow: "ellipsis", whiteSpace: "nowrap",
                color: "var(--text-tertiary)",
              }}
            >
              {h.type === "command" && h.command}
              {h.type === "http" && h.url}
              {h.type === "mcp_tool" && `${h.mcp_server}::${h.mcp_tool}`}
            </span>
            <button
              onClick={() => startEdit(h)}
              style={{ border: "none", background: "transparent", cursor: "pointer", color: "#6d28d9", fontSize: 12 }}
            >
              ✏️
            </button>
            <button
              onClick={() => void remove(h.name)}
              style={{ border: "none", background: "transparent", cursor: "pointer", color: "var(--danger-text)", fontSize: 13 }}
            >
              ×
            </button>
          </div>
        ))}
      </div>

      {/* 新增/编辑表单 */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "10px 12px", borderRadius: 8, background: "rgba(109,40,217,0.05)", border: "1px solid rgba(109,40,217,0.15)" }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: "#5b21b6" }}>
          {editing ? `编辑 hook: ${editing}` : "新增 hook"}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <input
            placeholder="name(唯一)"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            disabled={editing !== null}
            style={{ ...inputStyle, width: 140 }}
          />
          <select
            value={form.event}
            onChange={(e) => setForm({ ...form, event: e.target.value })}
            style={inputStyle}
          >
            {Object.entries(HOOK_EVENT_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v} ({k})</option>
            ))}
          </select>
          <select
            value={form.type}
            onChange={(e) => setForm({ ...form, type: e.target.value })}
            style={inputStyle}
          >
            {HOOK_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          {form.type === "command" && (
            <input
              placeholder="命令, 如: node hooks/xxx.mjs"
              value={form.command}
              onChange={(e) => setForm({ ...form, command: e.target.value })}
              style={{ ...inputStyle, flex: 1, minWidth: 200 }}
            />
          )}
          {form.type === "http" && (
            <input
              placeholder="URL, 如: http://127.0.0.1:9000/hook"
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              style={{ ...inputStyle, flex: 1, minWidth: 200 }}
            />
          )}
          {form.type === "mcp_tool" && (
            <>
              <input
                placeholder="mcp_server"
                value={form.mcp_server}
                onChange={(e) => setForm({ ...form, mcp_server: e.target.value })}
                style={{ ...inputStyle, width: 140 }}
              />
              <input
                placeholder="mcp_tool"
                value={form.mcp_tool}
                onChange={(e) => setForm({ ...form, mcp_tool: e.target.value })}
                style={{ ...inputStyle, width: 140 }}
              />
            </>
          )}
          <input
            type="number"
            min={1}
            value={String(form.timeout)}
            onChange={(e) => setForm({ ...form, timeout: Number(e.target.value) })}
            title="超时(秒)"
            style={{ ...inputStyle, width: 60 }}
          />
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
            />
            启用
          </label>
          <button className="btn-primary" style={{ padding: "6px 16px", fontSize: 12 }} onClick={() => void save()}>
            {editing ? "保存修改" : "添加"}
          </button>
          {editing && (
            <button
              onClick={() => { setEditing(null); setMsg(null); }}
              style={{ padding: "6px 12px", fontSize: 12, borderRadius: 6, border: "1px solid #ddd", background: "#fff", cursor: "pointer" }}
            >
              取消
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// V1.4-8.1 数据管理: 全局备份下载 / 上传还原 / 会话批量导出
// ──────────────────────────────────────────────────────────────────────────────
function DataSection(): JSX.Element {
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const restoreRef = useRef<HTMLInputElement | null>(null);
  // 批量导出
  const [batchIds, setBatchIds] = useState("");
  const [batchContent, setBatchContent] = useState<string | null>(null);

  const doBackup = async (): Promise<void> => {
    setBusy(true);
    setMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/backup`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const disposition = resp.headers.get("content-disposition") ?? "";
      const m = /filename="([^"]+)"/.exec(disposition);
      const name = m?.[1] ?? `pa-backup-${Date.now()}.zip`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      URL.revokeObjectURL(url);
      setMsg({ ok: true, text: `备份已下载: ${name} (含配置/会话/记忆/知识库, 请妥善保管)` });
    } catch (e) {
      setMsg({ ok: false, text: `备份失败: ${String(e)}` });
    } finally {
      setBusy(false);
    }
  };

  const doRestore = async (file: File | undefined | null): Promise<void> => {
    if (!file) return;
    if (!window.confirm("还原将覆盖当前全部会话/记忆/配置(先备份再操作)。确定继续?")) {
      if (restoreRef.current) restoreRef.current.value = "";
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const resp = await adminFetch(`${API_BASE}/backup/restore`, {
        method: "POST",
        body: form,
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? data.detail ?? `HTTP ${resp.status}`);
      const counts = Object.entries(data.restored ?? {})
        .map(([k, v]) => `${k}=${v}`)
        .join(", ");
      setMsg({
        ok: true,
        text: `还原成功(${counts})。${data.chunks_rebuild_pending ? "知识库片段需重索引: 请到知识库页执行 🔄 重索引。" : ""}`,
      });
      if (restoreRef.current) restoreRef.current.value = "";
    } catch (e) {
      setMsg({ ok: false, text: `还原失败: ${String(e)}` });
      if (restoreRef.current) restoreRef.current.value = "";
    } finally {
      setBusy(false);
    }
  };

  const doBatchExport = async (): Promise<void> => {
    const ids = batchIds
      .split(/[,，\s]+/)
      .map((s) => Number(s))
      .filter((n) => Number.isInteger(n) && n > 0);
    if (ids.length === 0) {
      setMsg({ ok: false, text: "请输入至少一个会话 ID" });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/sessions/export_batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_ids: ids, format: "md" }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setBatchContent(data.content ?? "");
      setMsg({ ok: true, text: `已导出 ${ids.length} 个会话` });
    } catch (e) {
      setMsg({ ok: false, text: `批量导出失败: ${String(e)}` });
    } finally {
      setBusy(false);
    }
  };

  const downloadBatch = (): void => {
    if (!batchContent) return;
    const blob = new Blob([batchContent], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `sessions-batch-${Date.now()}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="glass-panel animate-in delay-2" style={{ padding: "18px 22px", display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em" }}>数据管理</div>

      {msg && (
        <div style={{ fontSize: 12, color: msg.ok ? "#059669" : "#dc2626", wordBreak: "break-word" }}>{msg.text}</div>
      )}

      {/* 备份/还原 */}
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn-primary" onClick={() => void doBackup()} disabled={busy} style={{ padding: "7px 16px", fontSize: 12 }}>
          {busy ? "…" : "⬇ 一键备份"}
        </button>
        <label style={{ fontSize: 12, padding: "7px 16px", borderRadius: 8, border: "1px solid #d97706", background: "#fffbeb", color: "#92400e", cursor: "pointer" }}>
          ⬆ 上传还原
          <input
            ref={restoreRef}
            type="file"
            accept=".zip"
            style={{ display: "none" }}
            onChange={(e) => void doRestore(e.target.files?.[0])}
          />
        </label>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          备份含: 运行时配置(含 API Key 密文)/技能/会话/消息/记忆/知识库文档
        </span>
      </div>

      {/* 会话批量导出 */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>会话批量导出:</span>
        <input
          className="flow-input"
          style={{ width: 220 }}
          placeholder="会话 ID, 逗号分隔, 如 1,2,3"
          value={batchIds}
          onChange={(e) => setBatchIds(e.target.value)}
        />
        <button onClick={() => void doBatchExport()} disabled={busy} style={{ fontSize: 12, padding: "7px 16px", borderRadius: 8, border: "1px solid #6d28d9", background: "#f5f3ff", color: "#5b21b6", cursor: "pointer" }}>
          导出 MD
        </button>
        {batchContent && (
          <button onClick={downloadBatch} style={{ fontSize: 12, padding: "7px 16px", borderRadius: 8, border: "1px solid #059669", background: "#ecfdf5", color: "#065f46", cursor: "pointer" }}>
            ⬇ 下载合并文件
          </button>
        )}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// V1.4-8.3 系统设置: 存储路径/日志/代理/缓存清理/master key 状态
// ──────────────────────────────────────────────────────────────────────────────
interface SystemSettings {
  app_name: string;
  version: string;
  workspace_root: string;
  log_level: string;
  log_retention_days: number;
  proxy_http: string | null;
  proxy_https: string | null;
  master_key_configured: boolean;
  database: string;
}

function SystemSection(): JSX.Element {
  const [cfg, setCfg] = useState<SystemSettings | null>(null);
  const [logLevel, setLogLevel] = useState("INFO");
  const [retention, setRetention] = useState(7);
  const [proxyHttp, setProxyHttp] = useState("");
  const [proxyHttps, setProxyHttps] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (): Promise<void> => {
    try {
      const resp = await adminFetch(`${API_BASE}/settings/system`);
      if (!resp.ok) return;
      const data = await resp.json();
      setCfg(data);
      setLogLevel(data.log_level ?? "INFO");
      setRetention(data.log_retention_days ?? 7);
      setProxyHttp(data.proxy_http ?? "");
      setProxyHttps(data.proxy_https ?? "");
    } catch {
      /* 静默 */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (): Promise<void> => {
    setBusy(true);
    setMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/settings/system`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          log_level: logLevel,
          log_retention_days: Number(retention) || 7,
          proxy_http: proxyHttp.trim(),
          proxy_https: proxyHttps.trim(),
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setMsg({ ok: true, text: "已保存(log_level 即时生效, 代理与存储路径重启后端后完整生效)" });
      void load();
    } catch (e) {
      setMsg({ ok: false, text: `保存失败: ${String(e)}` });
    } finally {
      setBusy(false);
    }
  };

  const doClearCache = async (): Promise<void> => {
    if (!window.confirm("清理 outputs 产物目录中超过保留期的临时文件? 不影响对话与配置。")) return;
    setBusy(true);
    setMsg(null);
    try {
      const resp = await adminFetch(`${API_BASE}/cache/clear`, { method: "POST" });
      const data = await resp.json();
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setMsg({
        ok: true,
        text: `已清理 ${data.cleaned_files} 个文件(${data.freed_bytes > 0 ? `${(data.freed_bytes / 1024).toFixed(1)}KB` : "0KB"})`,
      });
    } catch (e) {
      setMsg({ ok: false, text: `清理失败: ${String(e)}` });
    } finally {
      setBusy(false);
    }
  };

  const inputStyle = {
    padding: "6px 10px", borderRadius: 6, fontSize: 12,
    border: "1px solid rgba(148,163,184,0.3)",
    background: "rgba(255,255,255,0.6)",
  } as const;

  return (
    <div className="glass-panel animate-in delay-2" style={{ padding: "18px 22px", display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontSize: 15, fontWeight: 600, letterSpacing: "-0.01em" }}>系统设置</div>

      {msg && (
        <div style={{ fontSize: 12, color: msg.ok ? "#059669" : "#dc2626", wordBreak: "break-word" }}>{msg.text}</div>
      )}

      {/* 状态行 */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 14, fontSize: 12, color: "var(--text-secondary)" }}>
        <span>{cfg?.app_name ?? "Private Agent"} v{cfg?.version ?? "—"}</span>
        <span>数据库: {cfg?.database ?? "—"}</span>
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
          Master Key:
          <span
            style={{
              fontSize: 11, padding: "1px 8px", borderRadius: 10,
              background: cfg?.master_key_configured ? "rgba(76,175,80,0.12)" : "#fef3c7",
              color: cfg?.master_key_configured ? "#2e7d32" : "#d97706",
            }}
          >
            {cfg?.master_key_configured ? "已配置(AES 加密)" : "未配置"}
          </span>
        </span>
        <span title="工作区根目录">存储路径: {cfg?.workspace_root ?? "—"}</span>
      </div>

      {/* 配置行 */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          日志级别
          <select value={logLevel} onChange={(e) => setLogLevel(e.target.value)} style={inputStyle}>
            {["DEBUG", "INFO", "WARNING", "ERROR"].map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          日志保留(天)
          <input
            type="number"
            min={1}
            max={3650}
            value={String(retention)}
            onChange={(e) => setRetention(Number(e.target.value))}
            style={{ ...inputStyle, width: 64 }}
          />
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          HTTP 代理
          <input
            value={proxyHttp}
            onChange={(e) => setProxyHttp(e.target.value)}
            placeholder="http://127.0.0.1:7890"
            style={{ ...inputStyle, width: 170 }}
          />
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          HTTPS 代理
          <input
            value={proxyHttps}
            onChange={(e) => setProxyHttps(e.target.value)}
            placeholder="https://127.0.0.1:7890"
            style={{ ...inputStyle, width: 170 }}
          />
        </label>
        <button className="btn-primary" style={{ padding: "6px 16px", fontSize: 12 }} onClick={() => void save()} disabled={busy}>
          保存
        </button>
        <button
          onClick={() => void doClearCache()}
          disabled={busy}
          style={{ fontSize: 12, padding: "6px 14px", borderRadius: 8, border: "1px solid #d97706", background: "#fffbeb", color: "#92400e", cursor: "pointer" }}
        >
          🧹 清理产物缓存
        </button>
      </div>
    </div>
  );
}

function WallpaperSection(): JSX.Element {  const [wallpaper, setWallpaper] = useState<string | null>(null);
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
                // V1.4.1: 缩放落元素尺寸(width/height=scale%), left%(容器)与
                // translate(-%)(元素)基数不同才有净偏移; transform 不再 scale
                position: "absolute",
                left: `${scale > 100 ? posX : 50}%`,
                top: `${scale > 100 ? posY : 50}%`,
                width: `${scale}%`,
                height: `${scale}%`,
                objectFit: fit,
                transform: `translate(-${scale > 100 ? posX : 50}%, -${scale > 100 ? posY : 50}%) rotate(${rotate}deg)`,
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
                position: "absolute",
                left: `${scale > 100 ? posX : 50}%`,
                top: `${scale > 100 ? posY : 50}%`,
                width: `${scale}%`,
                height: `${scale}%`,
                objectFit: fit,
                transform: `translate(-${scale > 100 ? posX : 50}%, -${scale > 100 ? posY : 50}%) rotate(${rotate}deg)`,
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
              onChange={(e) => {
                setPosX(Number(e.target.value));
                // V1.4.1: cover 铺满 + scale<=100 时无裁剪溢出, 移动位置不可见
                // → 拖动位置时自动放大到 110%, 保证有可移动空间
                if (scale <= 100) setScale(110);
              }}
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
              onChange={(e) => {
                setPosY(Number(e.target.value));
                // 同上: 自动放大保证移动可见
                if (scale <= 100) setScale(110);
              }}
              style={{ flex: 1 }}
            />
            <span style={{ fontSize: 12, color: "var(--text-tertiary)", width: 34, textAlign: "right" }}>{posY}%</span>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: -4 }}>
            💡 铺满模式下背景须大于画面才有移动空间: 拖动位置会自动放大到 110%, 也可手动调"缩放"后移动
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
