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
  multimodal: boolean;
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

// 2026-08-08: 设置页分区折叠(模型/MCP/技能内容多, 默认收起避免页面过长;
// 标题栏显示统计数, 点击标题或按钮展开/收起)
function CollapsibleSection({
  title,
  subtitle,
  count,
  defaultOpen = false,
  children,
}: {
  title: string;
  subtitle?: string;
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}): JSX.Element {
  const [open, setOpen] = useState(defaultOpen);
  const toggle = (): void => setOpen((o) => !o);
  return (
    <div className="glass-panel animate-in" style={{ padding: "20px 24px" }}>
      <div
        style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          cursor: "pointer", gap: 12,
        }}
        onClick={toggle}
      >
        <div>
          <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
            {title}
            {count !== undefined && (
              <span style={{ fontSize: 12, color: "var(--text-tertiary)", marginLeft: 8 }}>({count})</span>
            )}
          </div>
          {subtitle && (
            <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{subtitle}</div>
          )}
        </div>
        <button
          onClick={(e) => {
            e.stopPropagation();
            toggle();
          }}
          title={open ? "收起" : "展开"}
          style={{
            fontSize: 12, padding: "4px 10px", borderRadius: 10, cursor: "pointer", flexShrink: 0,
            border: "1px solid var(--border-strong)", background: "var(--button-ghost-bg)",
            color: "var(--text-primary)",
          }}
        >
          {open ? "收起 ▲" : "展开 ▼"}
        </button>
      </div>
      {open && <div style={{ marginTop: 16 }}>{children}</div>}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// 0.5.0 P4: 智能体名称配置卡 —— 主智能体 + 三场景智能体名称统一管理
// 主智能体: PUT /admin/agent-profile (display_name)
// 场景智能体: PUT /admin/skills/{name}/meta (display_name, 标识符不变)
// ──────────────────────────────────────────────────────────────────────────────

const AGENT_NAME_ROWS = [
  { key: "main", label: "主智能体", sub: "系统监控与全局对话" },
  { key: "office", label: "场景 · 工作学习", sub: "office (子瞻)" },
  { key: "data_analysis", label: "场景 · 投资理财", sub: "data_analysis (白圭)" },
  { key: "frontend_design", label: "场景 · 生活美学", sub: "frontend_design (清和)" },
];

function AgentNameSection(): JSX.Element {
  const [mainName, setMainName] = useState<string>("私人智能体");
  const [sceneNames, setSceneNames] = useState<Record<string, string>>({});
  // 2026-08-15(蒋先生需求): 场景工作区 —— 每个场景智能体的产物
  // 默认落在自己的工作区目录(空=全局默认 workspace_root)
  const [sceneWorkspaces, setSceneWorkspaces] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const [profResp, skillsResp] = await Promise.all([
        adminFetch(`${API_BASE}/agent-profile`),
        adminFetch(`${API_BASE}/skills`),
      ]);
      if (profResp.ok) {
        const p = await profResp.json();
        if (typeof p.display_name === "string" && p.display_name.trim()) {
          setMainName(p.display_name.trim());
        }
      }
      if (skillsResp.ok) {
        const list = await skillsResp.json();
        const names: Record<string, string> = {};
        const workspaces: Record<string, string> = {};
        for (const s of list ?? []) {
          if (typeof s.name === "string" && s.display_name) {
            names[s.name] = s.display_name;
          }
          if (typeof s.name === "string" && typeof s.workspace === "string") {
            workspaces[s.name] = s.workspace;
          }
        }
        setSceneNames(names);
        setSceneWorkspaces(workspaces);
      }
    } catch {
      /* 读取失败保留默认 */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const saveMain = async (): Promise<void> => {
    const trimmed = mainName.trim();
    try {
      const resp = await adminFetch(`${API_BASE}/agent-profile`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: trimmed }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setMsg("主智能体名称已保存(全局对话/问候生效)");
    } catch (e) {
      setMsg(`保存失败: ${String(e)}`);
    }
  };

  const saveScene = async (name: string, value: string, wsValue: string): Promise<void> => {
    const trimmed = value.trim();
    const wsTrimmed = wsValue.trim();
    try {
      const resp = await adminFetch(`${API_BASE}/skills/${name}/meta`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: trimmed, workspace: wsTrimmed }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setSceneNames((prev) => ({ ...prev, [name]: trimmed }));
      setSceneWorkspaces((prev) => ({ ...prev, [name]: wsTrimmed }));
      setMsg(`「${AGENT_NAME_ROWS.find((r) => r.key === name)?.label ?? name}」已保存`);
    } catch (e) {
      setMsg(`保存失败: ${String(e)}`);
    }
  };

  return (
    <CollapsibleSection
      title="智能体名称配置"
      subtitle="主智能体 + 三场景智能体名称与场景工作区管理(标识符不变, 仅显示名与工作目录)"
      count={4}
    >
      {loading ? (
        <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>加载中…</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {AGENT_NAME_ROWS.map((row) => {
            const isMain = row.key === "main";
            const value = isMain ? mainName : (sceneNames[row.key] ?? row.sub.split(" (")[0]);
            const wsValue = isMain ? "" : (sceneWorkspaces[row.key] ?? "");
            // 2026-08-15(蒋先生反馈): 每行改两行式(名称行 / 工作区行),
            // 避免单行 4 元素(名称+工作区+保存)在窄窗口撑出画面。
            return (
              <div
                key={row.key}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                  padding: 8,
                  borderRadius: 8,
                  background: "var(--panel-bg)",
                  border: "1px solid rgba(148,163,184,0.12)",
                }}
              >
                {/* 第一行: 名称 + 保存 */}
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div style={{ width: 120, flexShrink: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{row.label}</div>
                    <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{row.sub}</div>
                  </div>
                  <input
                    value={value}
                    onChange={(e) => {
                      if (isMain) setMainName(e.target.value);
                      else setSceneNames((prev) => ({ ...prev, [row.key]: e.target.value }));
                    }}
                    style={{
                      flex: 1,
                      minWidth: 0,
                      padding: "6px 10px",
                      borderRadius: 6,
                      border: "1px solid rgba(148,163,184,0.3)",
                      fontSize: 12,
                      background: "var(--panel-bg)",
                      color: "var(--text-primary)",
                    }}
                  />
                  <button
                    onClick={() => (isMain ? void saveMain() : void saveScene(row.key, value, wsValue))}
                    style={{
                      fontSize: 12,
                      padding: "6px 14px",
                      borderRadius: 6,
                      border: "1px solid var(--border-strong)",
                      background: "var(--button-ghost-bg)",
                      color: "var(--text-primary)",
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    保存
                  </button>
                </div>
                {/* 2026-08-15(蒋先生需求): 场景工作区 —— 第二行: 输入 + 浏览选择 */}
                {!isMain && (
                  <div style={{ display: "flex", alignItems: "center", gap: 10, paddingLeft: 130 }}>
                    <input
                      value={wsValue}
                      onChange={(e) =>
                        setSceneWorkspaces((prev) => ({ ...prev, [row.key]: e.target.value }))
                      }
                      placeholder="工作区目录(空=全局默认)"
                      title="该场景智能体的文件/脚本/输出默认落在此目录"
                      style={{
                        flex: 1,
                        minWidth: 0,
                        padding: "6px 10px",
                        borderRadius: 6,
                        border: "1px solid rgba(148,163,184,0.3)",
                        fontSize: 12,
                        background: "var(--panel-bg)",
                        color: wsValue ? "var(--text-primary)" : "var(--text-tertiary)",
                      }}
                    />
                    <button
                      onClick={async () => {
                        const dir = await window.pa?.pickDirectory?.();
                        if (dir) {
                          setSceneWorkspaces((prev) => ({ ...prev, [row.key]: dir }));
                        }
                      }}
                      title={
                        window.pa?.pickDirectory
                          ? "打开系统目录选择器"
                          : "当前环境不支持目录选择器, 请手动输入路径"
                      }
                      style={{
                        fontSize: 12,
                        padding: "6px 12px",
                        borderRadius: 6,
                        border: "1px solid var(--border-strong)",
                        background: "var(--button-ghost-bg)",
                        color: "var(--text-primary)",
                        cursor: window.pa?.pickDirectory ? "pointer" : "not-allowed",
                        flexShrink: 0,
                        opacity: window.pa?.pickDirectory ? 1 : 0.5,
                      }}
                    >
                      浏览…
                    </button>
                  </div>
                )}
              </div>
            );
          })}
          <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            场景工作区: 该场景智能体新建会话时自动使用, 产物(文件/脚本/输出)落各自目录; 留空则使用全局默认工作区。
          </div>
          {msg && (
            <div style={{ fontSize: 12, color: msg.startsWith("保存失败") ? "#dc2626" : "#059669" }}>
              {msg}
            </div>
          )}
        </div>
      )}
    </CollapsibleSection>
  );
}


// ──────────────────────────────────────────────────────────────────────────────
// 2026-08-10 设置中心重构: 左侧分组导航 + 顶部栏 + 内容区按页切换
// 旧版全部设置单页堆叠; 新版按「偏好 / AI 能力 / 安全与数据 / 系统」分组。
// 所有分区组件原样保留(独立状态), 页面全部挂载 + display 切换, 切换不丢状态。
// 导航项只登记真实存在的分区(通知暂未实现故不列入)。
// ──────────────────────────────────────────────────────────────────────────────

interface SettingsNavItem {
  key: string;
  label: string;
  icon: string;
}

interface SettingsNavGroup {
  label: string;
  items: SettingsNavItem[];
}

const SETTINGS_NAV_GROUPS: SettingsNavGroup[] = [
  {
    label: "偏好",
    items: [{ key: "general", label: "通用设置", icon: "🎨" }],
  },
  {
    label: "AI 能力",
    items: [
      { key: "models", label: "模型服务", icon: "🧠" },
      { key: "agents", label: "智能体与人格", icon: "🤖" },
      { key: "mcp", label: "工具与 MCP", icon: "🔌" },
      { key: "skills", label: "技能 Skills", icon: "🧩" },
    ],
  },
  {
    label: "安全与数据",
    items: [
      { key: "security", label: "权限与安全", icon: "🛡️" },
      { key: "sandbox", label: "沙箱与钩子", icon: "🧪" },
      { key: "data", label: "数据管理", icon: "🗄️" },
    ],
  },
  {
    label: "系统",
    items: [
      { key: "system", label: "系统设置", icon: "⚙️" },
      { key: "about", label: "关于与更新", icon: "ℹ️" },
    ],
  },
];

export default function SettingsView({ sessionId = 1, theme }: { sessionId?: number; theme?: "light" | "dark" }): JSX.Element {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [fallbackChain, setFallbackChain] = useState<string[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [protocol, setProtocol] = useState("");
  const [error, setError] = useState("");
  // 2026-08-10: 设置中心导航状态(当前页 + 顶部栏搜索词)
  const [activeKey, setActiveKey] = useState<string>("models");
  const [navQuery, setNavQuery] = useState("");

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

  // 各导航页内容(全部保留挂载, 用 display 切换以保留表单状态)
  const pages: Record<string, { title: string; subtitle: string; node: JSX.Element }> = {
    general: {
      title: "通用设置",
      subtitle: "外观、主题与壁纸",
      node: <WallpaperSection theme={theme} />,
    },
    models: {
      title: "模型服务",
      subtitle: "模型服务商管理、降级链与会话模型",
      node: (
        <CollapsibleSection
          title="模型提供商"
          subtitle="可新增/编辑/删除模型(任意 OpenAI 兼容服务) · 编辑可配置参数上限(输入/输出/轮次)"
          count={providers.length}
        >
          {/* 降级链编辑器: 可拖拽排序 / 增删 */}
          {providers.length > 0 && (
            <FallbackChainEditor
              providers={providers}
              chain={fallbackChain}
              onUpdated={load}
            />
          )}
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
        </CollapsibleSection>
      ),
    },
    agents: {
      title: "智能体与人格",
      subtitle: "主智能体与三场景智能体命名统一管理",
      node: <AgentNameSection />,
    },
    mcp: {
      title: "工具与 MCP",
      subtitle: `协议版本: ${protocol || "—"} · 可新增/删除/测试连通性(改动重启后端后生效)`,
      node: (
        <CollapsibleSection
          title="MCP 服务"
          subtitle={`协议版本: ${protocol || "—"} · 可新增/删除/测试连通性(改动重启后端后生效)`}
          count={mcpServers.length}
        >
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
        </CollapsibleSection>
      ),
    },
    skills: {
      title: "技能 Skills",
      subtitle: "技能列表与上传新技能",
      node: <SkillsSection />,
    },
    security: {
      title: "权限与安全",
      subtitle: "控制面鉴权 token 与会话权限模式",
      node: (
        <>
          {/* 阶段二批次 1: admin 鉴权 token 管理 */}
          <SecuritySection />
          {/* 阶段三批次 1(T1.2): 会话级权限模式切换(使用当前会话 id) */}
          <PermissionModeSection sessionId={sessionId} />
        </>
      ),
    },
    sandbox: {
      title: "沙箱与钩子",
      subtitle: "沙箱执行环境与工作流自动化钩子",
      node: (
        <>
          {/* §6.14 [MVP] 沙箱配置管理 UI */}
          <SandboxSection />
          {/* V1.3-7.2 工作流自动化: Hooks 配置 */}
          <HooksSection />
        </>
      ),
    },
    data: {
      title: "数据管理",
      subtitle: "数据库连接配置与备份/还原/导出",
      node: (
        <>
          {/* 2026-08-06: 数据库连接配置(打包版首次使用必配; 密码仅存本地 .env) */}
          <DatabaseSection />
          {/* V1.4-8.1 数据管理: 备份/还原/批量导出 */}
          <DataSection />
        </>
      ),
    },
    system: {
      title: "系统设置",
      subtitle: "日志、代理、缓存与 master key",
      node: <SystemSection />,
    },
    about: {
      title: "关于与更新",
      subtitle: "版本信息与更新检查",
      node: <UpdateSection />,
    },
  };

  // 顶部栏搜索: 过滤侧边栏导航项(Enter 直达首个匹配页)
  const q = navQuery.trim().toLowerCase();
  const filteredGroups = q
    ? SETTINGS_NAV_GROUPS.map((g) => ({
        ...g,
        items: g.items.filter((it) => it.label.toLowerCase().includes(q)),
      })).filter((g) => g.items.length > 0)
    : SETTINGS_NAV_GROUPS;
  const firstMatch = filteredGroups[0]?.items[0];

  const activePage = pages[activeKey] ?? pages.models;

  return (
    <div className="settings-layout">
      {/* 左侧分组导航 */}
      <div className="settings-sidebar">
        <div className="settings-brand">
          <div className="settings-brand-logo">PA</div>
          <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 3 }}>
            <div
              style={{
                fontSize: 15, fontWeight: 700, letterSpacing: "-0.02em",
                color: "var(--text-primary)", whiteSpace: "nowrap",
                overflow: "hidden", textOverflow: "ellipsis",
              }}
            >
              Private Agent
            </div>
            <div className="settings-version-badge" style={{ alignSelf: "flex-start" }}>
              v{window.pa?.versions?.app || "0.5.1"}
            </div>
          </div>
        </div>

        <div className="settings-nav-scroll">
          {filteredGroups.map((group) => (
            <div key={group.label} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <div className="settings-nav-group-label">{group.label}</div>
              {group.items.map((item) => (
                <button
                  key={item.key}
                  className={`nav-item${activeKey === item.key ? " active" : ""}`}
                  onClick={() => setActiveKey(item.key)}
                  title={item.label}
                >
                  <span style={{ width: 20, flexShrink: 0, textAlign: "center", fontSize: 14 }}>{item.icon}</span>
                  <span style={{ flex: 1, textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {item.label}
                  </span>
                </button>
              ))}
            </div>
          ))}
          {filteredGroups.length === 0 && (
            <div style={{ fontSize: 12, color: "var(--text-tertiary)", padding: "10px 12px" }}>
              无匹配设置项
            </div>
          )}
        </div>

        <div className="settings-sidebar-foot">
          <span>🔌 后端 {API_BASE}</span>
          <span>模式 · 本地运行</span>
        </div>
      </div>

      {/* 右侧: 顶部栏 + 内容区 */}
      <div className="settings-content-col">
        <div className="settings-topbar">
          <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
            <div className="settings-breadcrumb">
              <span>设置</span>
              <span style={{ color: "var(--text-tertiary)" }}>/</span>
              <span style={{ color: "var(--text-secondary)" }}>{activePage.title}</span>
            </div>
            <div className="settings-page-title">{activePage.title}</div>
            <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>{activePage.subtitle}</div>
          </div>
          <input
            className="settings-search"
            placeholder="搜索设置项…"
            value={navQuery}
            onChange={(e) => setNavQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && firstMatch) setActiveKey(firstMatch.key);
            }}
          />
        </div>

        {error && (
          <div style={{ fontSize: 12, color: "var(--danger-text)", padding: "0 4px" }}>
            加载失败: {error}
          </div>
        )}

        <div className="settings-pages">
          {Object.entries(pages).map(([key, page]) => (
            <div
              key={key}
              style={{
                display: activeKey === key ? "flex" : "none",
                flexDirection: "column",
                gap: 16,
              }}
            >
              {page.node}
            </div>
          ))}
        </div>
      </div>
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
      {/* 2026-08-15(蒋先生反馈): 固定 4 列在窄窗口溢出 → auto-fit 自动换行 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8, marginBottom: 8 }}>
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
                border: "1px solid rgba(148,163,184,0.4)", background: "var(--panel-bg-solid)",
                color: "var(--text-primary)", outline: "none",
              }}
            />
          </label>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
        {/* 2026-08-15: minWidth 0 防 input intrinsic 宽度撑破 */}
        <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            密码
            {/* 2026-08-06: 密码配置状态徽标(空框不再误导) */}
            <span
              style={{
                fontSize: 10, padding: "1px 8px", borderRadius: 8,
                background: passwordConfigured ? "var(--success-bg)" : "var(--warning-bg)",
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
              border: "1px solid rgba(148,163,184,0.4)", background: "var(--panel-bg-solid)",
              color: "var(--text-primary)", outline: "none",
            }}
          />
        </label>
        <button
          onClick={() => void save()}
          disabled={busy}
          style={{
            marginTop: 20, fontSize: 13, padding: "6px 18px", borderRadius: 8,
            border: "1px solid #6366f1", background: "#6366f1", color: "var(--on-accent)",
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
            border: "1px solid rgba(148,163,184,0.4)", background: "var(--panel-bg-solid)",
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
              background: "var(--code-bg)", border: "1px solid var(--border-color)",
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
              border: "1px solid #cbd5e1", background: "var(--panel-bg-solid)", cursor: "pointer",
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
  // V1.1-3.6 改名: 显示名(空回退 name, 标识符不变)
  display_name?: string;
}

function SkillsSection(): JSX.Element {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [name, setName] = useState("");
  const [skillYaml, setSkillYaml] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  // 2026-08-08: 技能区折叠(内容多, 默认收起)
  const [collapsed, setCollapsed] = useState(false);
  // 2026-08-04: zip 一键上传
  const [zipBusy, setZipBusy] = useState(false);
  const [zipMsg, setZipMsg] = useState<string | null>(null);
  // 2026-08-12 Phase1: 上传结果结构化展示(安装卡片/失败原因)
  const [zipResult, setZipResult] = useState<{
    ok: boolean;
    mode?: string;
    installed?: {
      name: string;
      display_name?: string;
      description?: string;
      scenario?: string;
      tools?: string[];
      files?: number;
    }[];
    failed?: { name: string; errors: { field: string; reason: string }[] }[];
    note?: string;
  } | null>(null);
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
  // V1.1-3.6 改名: 行内编辑显示名(标识符不变)
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [renameInput, setRenameInput] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameMsg, setRenameMsg] = useState<string | null>(null);

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

  // V1.1-3.6 改名: PUT /admin/skills/{name}/meta body.display_name
  const submitRename = async (s: SkillInfo): Promise<void> => {
    const trimmed = renameInput.trim();
    if (!trimmed) {
      setRenameMsg("显示名不能为空");
      return;
    }
    setRenameBusy(true);
    setRenameMsg(null);
    try {
      const resp = await adminFetch(`http://127.0.0.1:8765/admin/skills/${s.name}/meta`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_name: trimmed }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail ?? data.error ?? `HTTP ${resp.status}`);
      setRenameMsg(`已将 "${s.name}" 重命名为 "${trimmed}"`);
      setRenameTarget(null);
      setRenameInput("");
      void load();
    } catch (e) {
      setRenameMsg(`改名失败: ${String(e)}`);
    } finally {
      setRenameBusy(false);
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
    setZipResult(null);
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
      // 2026-08-12 Phase1: 增强返回结构(ok/installed/failed) — 安装卡片 + 失败原因
      if (data && typeof data === "object" && "installed" in data) {
        setZipResult(data);
        const okCount = Array.isArray(data.installed) ? data.installed.length : 0;
        const failCount = Array.isArray(data.failed) ? data.failed.length : 0;
        if (okCount > 0 && failCount === 0) {
          setZipMsg(`✅ 成功安装 ${okCount} 个技能`);
        } else if (okCount > 0) {
          setZipMsg(`⚠️ 安装 ${okCount} 个, ${failCount} 个失败(详见下方)`);
        } else {
          setZipMsg(`❌ ${failCount} 个技能全部校验失败(详见下方)`);
        }
      } else if (Array.isArray(data.skills) && data.skills.length > 0) {
        // 兼容旧返回(素材库自动技能化 skills 数组)
        setZipResult({
          ok: true,
          mode: data.mode,
          installed: data.skills.map((s: { name: string }) => ({ name: s.name })),
          failed: [],
        });
        const names = data.skills.map((s: { name: string }) => s.name).join(", ");
        setZipMsg(`✅ 已导入 ${data.skills.length} 个技能: ${names}`);
      } else {
        // 兼容旧返回(单技能 {name,path,files})
        setZipResult({
          ok: true,
          mode: "single",
          installed: [{ name: data.name, display_name: data.name, files: data.files }],
          failed: [],
        });
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
      <div
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer" }}
        onClick={() => setCollapsed((c) => !c)}
      >
        <div style={{ fontSize: 15, fontWeight: 600 }}>
          技能管理
          <span style={{ fontSize: 12, color: "var(--text-tertiary)", marginLeft: 8 }}>({skills.length})</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            className="btn-secondary"
            style={{ fontSize: 12, padding: "4px 12px", cursor: "pointer" }}
            onClick={(e) => {
              e.stopPropagation();
              void load();
            }}
          >
            {loading ? "刷新中..." : "刷新"}
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation();
              setCollapsed((c) => !c);
            }}
            title={collapsed ? "展开" : "收起"}
            style={{
              fontSize: 12, padding: "4px 10px", borderRadius: 10, cursor: "pointer",
              border: "1px solid var(--border-strong)", background: "var(--button-ghost-bg)",
              color: "var(--text-primary)",
            }}
          >
            {collapsed ? "展开 ▼" : "收起 ▲"}
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          {/* 已安装技能: 一行一个(2026-08-08 由 chip 流式改为行式, 与 MCP 列表一致) */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 14, marginTop: 12 }}>
            {skills.map((s) => (
              <div
                key={s.name}
                style={{
                  display: "flex", alignItems: "center", gap: 12,
                  padding: "10px 14px", borderRadius: "var(--radius-sm)",
                  background: "var(--panel-bg)",
                }}
              >
                {renameTarget === s.name ? (
                  <input
                    autoFocus
                    value={renameInput}
                    onChange={(e) => setRenameInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void submitRename(s);
                      else if (e.key === "Escape") {
                        setRenameTarget(null);
                        setRenameInput("");
                      }
                    }}
                    disabled={renameBusy}
                    style={{
                      fontSize: 13, fontWeight: 600, flexShrink: 0, width: 160,
                      padding: "2px 6px", borderRadius: 4,
                      border: "1px solid var(--border-strong, #94a3b8)",
                      background: "var(--input-bg)", color: "var(--text-primary)",
                    }}
                  />
                ) : (
                  <span style={{ fontSize: 13, fontWeight: 600, flexShrink: 0 }}>
                    {s.display_name || s.name}
                  </span>
                )}
                <span style={{ fontSize: 11, color: "var(--text-tertiary)", flexShrink: 0 }}>v{s.version}</span>
                <span
                  style={{
                    fontSize: 11, padding: "1px 8px", borderRadius: 10, flexShrink: 0,
                    background: s.enabled ? "var(--success-bg)" : "var(--surface-2)",
                    color: s.enabled ? "var(--success-text)" : "var(--text-tertiary)",
                  }}
                >
                  {s.enabled ? "已启用" : "未启用"}
                </span>
                <span
                  style={{
                    fontSize: 12, color: "var(--text-tertiary)", flex: 1, minWidth: 0,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}
                >
                  {s.description}
                </span>
                {/* V1.1-3.6 改名入口 */}
                {renameTarget === s.name ? (
                  <>
                    <button
                      onClick={() => void submitRename(s)}
                      disabled={renameBusy}
                      title="保存新显示名"
                      style={{
                        border: "none", background: "transparent", cursor: renameBusy ? "wait" : "pointer",
                        fontSize: 14, padding: "2px 4px", color: "#15803d", flexShrink: 0,
                      }}
                    >
                      ✓
                    </button>
                    <button
                      onClick={() => {
                        setRenameTarget(null);
                        setRenameInput("");
                      }}
                      disabled={renameBusy}
                      title="取消"
                      style={{
                        border: "none", background: "transparent", cursor: "pointer",
                        fontSize: 14, padding: "2px 4px", color: "var(--text-tertiary)", flexShrink: 0,
                      }}
                    >
                      ✕
                    </button>
                  </>
                ) : (
                  <button
                    onClick={() => {
                      setRenameTarget(s.name);
                      setRenameInput(s.display_name || s.name);
                      setRenameMsg(null);
                    }}
                    title={`重命名 ${s.name}(只改显示名, 标识符不变)`}
                    style={{
                      border: "none", background: "transparent", cursor: "pointer",
                      fontSize: 12, padding: "2px 4px", color: "var(--accent-soft-text)", flexShrink: 0,
                    }}
                  >
                    ✏️ 改名
                  </button>
                )}
                {/* V1.2-6.1: 提示词编辑器入口 */}
                <button
                  onClick={() => void openPromptEditor(s.name)}
                  title={`编辑 ${s.name} 的系统提示词`}
                  style={{
                    border: "none", background: "transparent", cursor: "pointer",
                    fontSize: 14, padding: "2px 4px", color: "var(--accent-soft-text)", flexShrink: 0,
                  }}
                >
                  📝
                </button>
              </div>
            ))}
            {renameMsg && (
              <div style={{ fontSize: 12, color: "var(--success-text)", marginTop: 4 }}>{renameMsg}</div>
            )}
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
                background: "var(--gradient-indigo)", color: "var(--on-accent)",
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
          {/* 2026-08-12 Phase1: 上传结果卡片(安装技能信息 / 失败原因) */}
          {zipResult && (
            <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 14 }}>
              {zipResult.installed && zipResult.installed.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {zipResult.installed.map((s) => (
                    <div
                      key={s.name}
                      style={{
                        padding: "12px 14px", borderRadius: 10,
                        background: "rgba(5,150,105,0.07)", border: "1px solid rgba(5,150,105,0.3)",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <span style={{ fontSize: 14, fontWeight: 700 }}>✅ {s.display_name || s.name}</span>
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                          {s.name} · {s.files ?? 0} 个文件
                        </span>
                      </div>
                      {s.description && (
                        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
                          简介: {s.description}
                        </div>
                      )}
                      {s.scenario && (
                        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>
                          适用场景: {s.scenario}
                        </div>
                      )}
                      {Array.isArray(s.tools) && s.tools.length > 0 && (
                        <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
                          工具: {s.tools.join(", ")}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {zipResult.failed && zipResult.failed.length > 0 && (
                <div
                  style={{
                    padding: "12px 14px", borderRadius: 10,
                    background: "rgba(239,68,68,0.06)", border: "1px solid rgba(239,68,68,0.3)",
                  }}
                >
                  <div style={{ fontSize: 13, fontWeight: 600, color: "#d32f2f", marginBottom: 6 }}>
                    ❌ {zipResult.failed.length} 个技能安装失败
                  </div>
                  {zipResult.failed.map((f) => (
                    <div key={f.name} style={{ marginBottom: 6 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                        技能「{f.name}」
                      </div>
                      {f.errors.map((er) => (
                        <div key={er.field} style={{ fontSize: 12, color: "#d32f2f", marginLeft: 8 }}>
                          • {er.field}: {er.reason}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* 上传新技能(高级: 手动填写) */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>高级: 手动填写 skill.yaml</div>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="技能名称(小写字母/数字/下划线, 如 my_skill)"
              style={{ padding: "8px 10px", borderRadius: 6, border: "1px solid var(--border-strong)", fontSize: 13 }}
            />
            <textarea
              value={skillYaml}
              onChange={(e) => setSkillYaml(e.target.value)}
              placeholder="skill.yaml 内容(需含 name 字段)"
              rows={5}
              style={{ padding: "8px 10px", borderRadius: 6, border: "1px solid var(--border-strong)", fontSize: 12, fontFamily: "monospace" }}
            />
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="system_prompt.md 内容(可选)"
              rows={3}
              style={{ padding: "8px 10px", borderRadius: 6, border: "1px solid var(--border-strong)", fontSize: 12 }}
            />
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <button className="btn-primary" style={{ padding: "8px 18px", fontSize: 13 }} onClick={() => void upload()}>
                上传技能
              </button>
              {msg && <span style={{ fontSize: 12, color: msg.startsWith("技能") ? "#4caf50" : "#d32f2f" }}>{msg}</span>}
            </div>
          </div>
        </>
      )}

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
              background: "var(--panel-bg-solid)", borderRadius: 14,
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
                border: "1px solid var(--border-color)", borderRadius: 8, padding: 12,
                color: "#334155", background: "var(--code-bg)",
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
  noRelease?: boolean;
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
      } else if (r.noRelease) {
        // 2026-08-06: 仓库尚无任何 Release(未发布过) → 友好提示
        setResult("暂无发布版本(更新源尚未发布; 施工侧需先执行发布脚本)");
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
                width: 200, height: 8, borderRadius: 4, background: "var(--surface-2)",
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
  const [multimodal, setMultimodal] = useState(provider.multimodal ?? false);
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
    setMultimodal(provider.multimodal ?? false);
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
      body.multimodal = multimodal;
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
        background: "var(--panel-bg)",
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
              <span style={{ fontSize: 11, padding: "1px 8px", borderRadius: 10, background: "var(--accent-soft-bg)", color: "var(--accent-soft-text)" }}>
                本地
              </span>
            )}
            <span
              style={{
                fontSize: 11, padding: "1px 8px", borderRadius: 10,
                background: provider.api_key_configured ? "var(--success-bg)" : "var(--surface-2)",
                color: provider.api_key_configured ? "var(--success-text)" : "var(--text-tertiary)",
              }}
            >
              {provider.api_key_configured ? "Key 已配置" : "Key 未配置"}
            </span>
            {provider.multimodal && (
              <span style={{ fontSize: 11, padding: "1px 8px", borderRadius: 10, background: "rgba(99,102,241,0.12)", color: "#4f46e5" }} title="支持图片输入, 看图任务自动跳转此模型">
                多模态
              </span>
            )}
            {!provider.enabled && (
              <span style={{ fontSize: 11, padding: "1px 8px", borderRadius: 10, background: "var(--warning-bg)", color: "#d97706" }}>
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
              style={{ flex: 1, minWidth: 0, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
            />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>模型名</span>
            <input
              value={modelName}
              onChange={(e) => setModelName(e.target.value)}
              placeholder="model-name"
              style={{ flex: 1, minWidth: 0, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
            />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>API Key</span>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={provider.api_key_configured ? "已配置(留空不修改)" : "输入新 Key"}
              style={{ flex: 1, minWidth: 0, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
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
              style={{ width: 90, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
            />
            <input
              type="number"
              min={64}
              value={maxOutput}
              onChange={(e) => setMaxOutput(Number(e.target.value))}
              title="最大输出 tokens"
              style={{ width: 90, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
            />
            <input
              type="number"
              min={1}
              value={maxTurns}
              onChange={(e) => setMaxTurns(Number(e.target.value))}
              title="最大轮次"
              style={{ width: 70, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
            />
            <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>输入/输出/轮次</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            {/* V1.4-8.2: 分组 + 类型 */}
            <input
              value={group}
              onChange={(e) => setGroup(e.target.value)}
              placeholder="分组(如: 主力模型)"
              style={{ width: 140, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
            />
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              style={{ padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
            >
              <option value="cloud">云端模型</option>
              <option value="local">本地模型</option>
            </select>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              启用
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }} title="勾选后, 当用户发送图片时降级链将跳过纯文本模型, 直接从本模型开始调用">
              <input type="checkbox" checked={multimodal} onChange={(e) => setMultimodal(e.target.checked)} />
              多模态
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
// 降级链编辑器: 拖拽排序 + 启用/禁用控制
// ──────────────────────────────────────────────────────────────────────────────

function FallbackChainEditor({
  providers,
  chain,
  onUpdated,
}: {
  providers: ProviderInfo[];
  chain: string[];
  onUpdated: () => void;
}): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [localChain, setLocalChain] = useState<string[]>(chain);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // 同步外部更新
  useEffect(() => {
    setLocalChain(chain);
  }, [chain]);

  // 未在 chain 中但已启用的 provider
  const available = providers
    .filter((p) => p.enabled && !localChain.includes(p.name))
    .map((p) => p.name);

  const moveUp = (idx: number): void => {
    if (idx === 0) return;
    const next = [...localChain];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    setLocalChain(next);
  };

  const moveDown = (idx: number): void => {
    if (idx === localChain.length - 1) return;
    const next = [...localChain];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    setLocalChain(next);
  };

  const remove = (name: string): void => {
    setLocalChain(localChain.filter((n) => n !== name));
  };

  const add = (name: string): void => {
    setLocalChain([...localChain, name]);
  };

  const save = async (): Promise<void> => {
    setBusy(true);
    setError("");
    try {
      const resp = await adminFetch(`${API_BASE}/settings/fallback-chain`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chain: localChain }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail ?? `HTTP ${resp.status}`);
      setEditing(false);
      onUpdated();
    } catch (err) {
      setError(`保存失败: ${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const cancel = (): void => {
    setLocalChain(chain);
    setEditing(false);
    setError("");
  };

  if (!editing) {
    return (
      <div
        style={{
          display: "flex", alignItems: "center", gap: 10, marginBottom: 12,
          padding: "8px 12px", borderRadius: "var(--radius-sm)",
          background: "var(--panel-bg)",
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", flexShrink: 0 }}>
          降级链
        </span>
        <div style={{ flex: 1, fontSize: 12, color: "var(--text-tertiary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {chain.length > 0
            ? chain.map((name, i) => (
                <span key={name}>
                  {i > 0 && <span style={{ color: "var(--text-tertiary)", margin: "0 4px" }}>→</span>}
                  <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>{name}</span>
                </span>
              ))
            : "—（未配置）"}
        </div>
        <button
          className="btn-ghost"
          style={{ fontSize: 11, padding: "4px 10px", flexShrink: 0 }}
          onClick={() => setEditing(true)}
        >
          编辑
        </button>
      </div>
    );
  }

  return (
    <div
      style={{
        marginBottom: 12, padding: "12px 14px", borderRadius: "var(--radius-sm)",
        background: "var(--panel-bg)", display: "flex", flexDirection: "column", gap: 8,
      }}
    >
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)" }}>
        降级链顺序（失败时按此顺序逐个尝试）
      </div>
      {localChain.length === 0 && (
        <div style={{ fontSize: 12, color: "var(--text-tertiary)", padding: "8px 0" }}>
          降级链为空，请从下方可用模型中选择
        </div>
      )}
      {localChain.map((name, idx) => (
        <div
          key={name}
          style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "6px 10px", borderRadius: 6,
            background: "var(--panel-bg)",
            border: "1px solid rgba(148,163,184,0.2)",
          }}
        >
          <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 20, flexShrink: 0 }}>
            {idx + 1}.
          </span>
          <span style={{ flex: 1, fontSize: 12, fontWeight: 500, color: "var(--text-secondary)" }}>
            {name}
          </span>
          <button
            className="btn-ghost"
            style={{ fontSize: 11, padding: "2px 8px", minWidth: 28 }}
            onClick={() => moveUp(idx)}
            disabled={idx === 0}
          >
            ↑
          </button>
          <button
            className="btn-ghost"
            style={{ fontSize: 11, padding: "2px 8px", minWidth: 28 }}
            onClick={() => moveDown(idx)}
            disabled={idx === localChain.length - 1}
          >
            ↓
          </button>
          <button
            className="btn-ghost"
            style={{ fontSize: 11, padding: "2px 8px", minWidth: 28, color: "var(--danger-text)" }}
            onClick={() => remove(name)}
          >
            ✕
          </button>
        </div>
      ))}
      {available.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 6 }}>
            可用但未在链中:
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {available.map((name) => (
              <button
                key={name}
                className="btn-ghost"
                style={{ fontSize: 11, padding: "3px 10px", borderStyle: "dashed" }}
                onClick={() => add(name)}
              >
                + {name}
              </button>
            ))}
          </div>
        </div>
      )}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 4 }}>
        <button className="btn-primary" style={{ fontSize: 12, padding: "6px 14px" }} onClick={() => void save()} disabled={busy}>
          保存
        </button>
        <button className="btn-ghost" style={{ fontSize: 12, padding: "6px 12px" }} onClick={cancel} disabled={busy}>
          取消
        </button>
        {error && <span style={{ fontSize: 12, color: "var(--danger-text)" }}>{error}</span>}
      </div>
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
  const [multimodal, setMultimodal] = useState(false);
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
        multimodal,
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
    border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)",
  } as const;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "12px 14px", borderRadius: "var(--radius-sm)", background: "var(--panel-bg)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 72, flexShrink: 0 }}>名称</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="如 deepseek-flash / qwen-2.5（字母/数字/下划线/连字符/小数点）" style={inputStyle} />
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
        <input type="number" min={256} value={maxInput} onChange={(e) => setMaxInput(Number(e.target.value))} title="最大输入 tokens" style={{ width: 90, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }} />
        <input type="number" min={64} value={maxOutput} onChange={(e) => setMaxOutput(Number(e.target.value))} title="最大输出 tokens" style={{ width: 90, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }} />
        <input type="number" min={1} value={maxTurns} onChange={(e) => setMaxTurns(Number(e.target.value))} title="最大轮次" style={{ width: 70, padding: "6px 8px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }} />
        <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>输入/输出/轮次</span>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          启用并加入降级链
        </label>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }} title="勾选后, 当用户发送图片时降级链将跳过纯文本模型, 直接从本模型开始调用">
          <input type="checkbox" checked={multimodal} onChange={(e) => setMultimodal(e.target.checked)} />
          多模态
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
      if (data.ok) {
        // 2026-08-07 修复 UI 误导: 之前 `data.server_info || data.protocol || "ok"`
        // 把"2026-07-28"(MCP 协议版本号)显示出来, 被用户误读为"连接时间"。
        // 现在按"服务器名 · N 工具 · Xms"显示, 协议版本号放 detail 字段。
        const name = data.server || server.id;
        const tools = data.tools_count ?? "—";
        const lat = data.latency_ms != null ? `${data.latency_ms}ms` : "";
        const detail = data.detail ? ` · ${data.detail}` : "";
        setMsg(`✅ ${name} · ${tools} 工具 ${lat}${detail}`);
      } else {
        setMsg(`❌ ${data.error ?? "测试失败"}`);
      }
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
    // 2026-08-15(蒋先生反馈): 单行 9 元素窄窗口溢出 → flexWrap 换行
    <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", padding: "12px 14px", borderRadius: "var(--radius-sm)", background: "var(--panel-bg)" }}>
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
    <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "12px 14px", borderRadius: "var(--radius-sm)", background: "var(--panel-bg)" }}>
      {/* 模式切换: 表单 / JSON 导入 */}
      <div style={{ display: "flex", gap: 4, marginBottom: 2 }}>
        {(["form", "json"] as const).map((m) => (
          <button
            key={m}
            className="btn-ghost"
            style={{
              fontSize: 11, padding: "4px 12px",
              background: mode === m ? "var(--gradient-indigo)" : "var(--panel-bg)",
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
            style={{ minHeight: 130, fontFamily: "monospace", fontSize: 11, padding: "8px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", background: "var(--panel-bg)", resize: "vertical" }}
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
            background: "var(--panel-bg)", color: "var(--text-primary)",
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
            fontSize: 11, color: "#92400e", background: "var(--confirmation-bg)",
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
          style={{ flex: 1, minWidth: 0, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
        />
        <div style={{ display: "flex", gap: 4 }}>
          {(["http", "stdio"] as const).map((t) => (
            <button
              key={t}
              className="btn-ghost"
              style={{
                fontSize: 11, padding: "4px 10px",
                background: type === t ? "var(--gradient-indigo)" : "var(--panel-bg)",
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
            style={{ flex: 1, minWidth: 0, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
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
              style={{ flex: 1, minWidth: 0, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
            />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56, flexShrink: 0 }}>参数</span>
            <input
              value={args}
              onChange={(e) => setArgs(e.target.value)}
              placeholder="空格分隔, 如 -y @modelcontextprotocol/server-filesystem C:/tmp"
              style={{ flex: 1, minWidth: 0, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
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
          style={{ flex: 1, minWidth: 0, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)" }}
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
          style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid rgba(148,163,184,0.3)", fontSize: 12, background: "var(--panel-bg)", resize: "vertical", fontFamily: "monospace" }}
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
  background: "var(--panel-bg, var(--panel-bg))",
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
    background: "var(--panel-bg)",
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
              background: "var(--panel-bg)",
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
              style={{ padding: "6px 12px", fontSize: 12, borderRadius: 6, border: "1px solid var(--border-strong)", background: "var(--panel-bg-solid)", cursor: "pointer" }}
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
        <label style={{ fontSize: 12, padding: "7px 16px", borderRadius: 8, border: "1px solid #d97706", background: "var(--confirmation-bg)", color: "#92400e", cursor: "pointer" }}>
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
        <button onClick={() => void doBatchExport()} disabled={busy} style={{ fontSize: 12, padding: "7px 16px", borderRadius: 8, border: "1px solid #6d28d9", background: "var(--accent-soft-bg)", color: "#5b21b6", cursor: "pointer" }}>
          导出 MD
        </button>
        {batchContent && (
          <button onClick={downloadBatch} style={{ fontSize: 12, padding: "7px 16px", borderRadius: 8, border: "1px solid #059669", background: "var(--tool-result-bg)", color: "#065f46", cursor: "pointer" }}>
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
    background: "var(--panel-bg)",
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
              background: cfg?.master_key_configured ? "rgba(76,175,80,0.12)" : "var(--warning-bg)",
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
          style={{ fontSize: 12, padding: "6px 14px", borderRadius: 8, border: "1px solid #d97706", background: "var(--confirmation-bg)", color: "#92400e", cursor: "pointer" }}
        >
          🧹 清理产物缓存
        </button>
      </div>
    </div>
  );
}

function WallpaperSection({ theme }: { theme?: "light" | "dark" }): JSX.Element {  const [wallpaper, setWallpaper] = useState<string | null>(null);
  const [wpType, setWpType] = useState<"image" | "video">("image");
  // 2026-08-08: 暗色/亮色各自独立保存背景; 这里直接编辑"当前全局主题"对应
  // 的那一套, 在侧边栏切换主题后本区自动联动加载另一套(无需独立 tab)。
  const targetTheme: "light" | "dark" = theme ?? "light";
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
      const resp = await adminFetch(
        `${API_BASE}/wallpaper?theme=${encodeURIComponent(targetTheme)}`
      );
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
        setPosX(Number(data.style.position_x) || 50);
        setPosY(Number(data.style.position_y) || 50);
        setScale(Number(data.style.scale) || 100);
        setRotate(Number(data.style.rotate) || 0);
      }
    } catch {
      setWallpaper(null);
    }
  }, [targetTheme]);

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
          fit: "contain", // 固定完整显示, 放大+移动选取区域
          scale: Math.max(scale, 100),
          rotate,
          theme: targetTheme,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
      setMsg(`${targetTheme === "dark" ? "暗色" : "亮色"}主题样式已保存`);
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
        body: JSON.stringify({ data_url: dataUrl, theme: targetTheme }),
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
      setMsg(`${targetTheme === "dark" ? "暗色" : "亮色"}主题背景已更新, 首页将使用新背景`);
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
      await adminFetch(
        `${API_BASE}/wallpaper?theme=${encodeURIComponent(targetTheme)}`,
        { method: "DELETE" }
      );
      setWallpaper(null);
      setMsg(`${targetTheme === "dark" ? "暗色" : "亮色"}主题已恢复默认背景`);
    } catch {
      setMsg("移除失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="glass-panel animate-in delay-3" style={{ padding: "20px 24px" }}>
      <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.02em", marginBottom: 4 }}>
        主题与壁纸
      </div>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 12 }}>
        暗色/亮色主题各自独立保存背景, 当前编辑的是
        <b style={{ color: "var(--text-primary)" }}> {targetTheme === "dark" ? "🌙 暗色" : "☀️ 亮色"}主题</b>
        的背景 —— 在左侧边栏切换主题后, 这里自动联动到另一套
      </div>
      <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 16 }}>
        支持静态图 (PNG / JPG / WebP, ≤6MB) 或动态视频 (MP4 / WebM, ≤50MB)。
        图片文件原样保存、绝不裁剪; 缩放/移动/旋转只改变背景中显示的图片区域,
        超出背景容器的部分依然存在只是不显示
      </div>
      {/* 2026-08-15(蒋先生反馈): 240px 预览图 + minWidth 260 控件列同行,
          窄窗口溢出 → flexWrap 换行 + 预览图 maxWidth 100% */}
      <div style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div
          style={{
            width: 240,
            maxWidth: "100%",
            height: 136,
            borderRadius: "var(--radius-sm)",
            overflow: "hidden",
            background:
              theme === "dark"
                ? "linear-gradient(135deg, #0f172a 0%, #1e1b4b 45%, #111827 100%)"
                : "linear-gradient(135deg, #eef1f8 0%, #e6ebf6 45%, #ece7f7 100%)",
            flexShrink: 0,
            border: "1px solid rgba(148,163,184,0.15)",
            position: "relative",
          }}
        >
          {wallpaper && wpType === "video" && (
            <video
              key={wallpaper}
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
                // 完整显示(contain)为基线, scale 放大 + position 选区域 + rotate;
                // key={src} 重挂载播放淡入, 与首页一致
                position: "absolute",
                left: `${scale > 100 ? posX : 50}%`,
                top: `${scale > 100 ? posY : 50}%`,
                width: `${Math.max(scale, 100)}%`,
                height: `${Math.max(scale, 100)}%`,
                objectFit: "contain",
                transform: `translate(-${scale > 100 ? posX : 50}%, -${scale > 100 ? posY : 50}%) rotate(${rotate}deg)`,
                display: "block",
                animation: "wp-fade-in 0.4s ease both",
              }}
            />
          )}
          {wallpaper && wpType === "image" && (
            <img
              key={wallpaper}
              src={wallpaper}
              alt="当前壁纸"
              onError={() =>
                setLoadError(`图片加载失败: ${wallpaper}(检查后端 sidecar 是否运行)`)
              }
              onLoad={() => setLoadError(null)}
              style={{
                // 完整显示(contain)为基线, scale 放大 + position 选区域 + rotate;
                // key={src} 重挂载播放淡入, 与首页一致
                position: "absolute",
                left: `${scale > 100 ? posX : 50}%`,
                top: `${scale > 100 ? posY : 50}%`,
                width: `${Math.max(scale, 100)}%`,
                height: `${Math.max(scale, 100)}%`,
                objectFit: "contain",
                transform: `translate(-${scale > 100 ? posX : 50}%, -${scale > 100 ? posY : 50}%) rotate(${rotate}deg)`,
                display: "block",
                animation: "wp-fade-in 0.4s ease both",
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

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56 }}>水平位置</span>
            <input
              type="range"
              min={0}
              max={100}
              value={posX}
              onChange={(e) => {
                setPosX(Number(e.target.value));
                // 完整显示(scale=100)时无溢出区域, 移动不可见 → 自动放大保证可移动
                if (scale <= 100) setScale(130);
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
                if (scale <= 100) setScale(130);
              }}
              style={{ flex: 1 }}
            />
            <span style={{ fontSize: 12, color: "var(--text-tertiary)", width: 34, textAlign: "right" }}>{posY}%</span>
          </div>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: -4 }}>
            💡 放大后背景才有可移动的空间: 拖动位置会自动放大到 130%, 也可手动调"缩放"
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", width: 56 }}>缩放</span>
            <input
              type="range"
              min={100}
              max={300}
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
                      : "var(--button-ghost-bg)",
                  color: rotate === deg ? "var(--on-accent)" : "var(--text-primary)",
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
