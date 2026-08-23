// B2 P1-9 - Skill 选择面板
//
// 功能:
// - GET /admin/skills 拉取可用技能列表
// - 点击技能卡片 → POST /admin/sessions/{session_id}/activate 锁定
// - 激活成功回调 onActivated(skillName)
// - 激活失败(404 skill_not_found 等)显示错误信息
import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";

import { adminFetch } from "./utils/apiClient";

export interface SkillInfo {
  name: string;
  version: string;
  description: string;
  enabled: boolean;
  // 0.5.0 M1: 场景名(子瞻/白圭/清和, 显示层统一 scene_name → display_name → name)
  scene_name?: string;
  display_name?: string;
  permissions?: {
    allow_file_write: boolean;
    allow_network: boolean;
    sandbox_enabled: boolean;
    max_file_size_mb: number;
    rules: { tool: string; paths: string[]; domains: string[] }[];
  };
}

interface SkillSelectionPanelProps {
  sessionId: number;
  onActivated: (skillName: string) => void;
}

const API_BASE = "http://localhost:8765/admin";

// 0.5.0 M1: 场景中文名(后端 skill.yaml scene_name 优先, 此处为旧后端回退)
const SCENARIO_LABELS: Record<string, string> = {
  office: "子瞻",
  data_analysis: "白圭",
  frontend_design: "清和",
};

// 显示名解析: scene_name → display_name → SCENARIO_LABELS → name
const displayName = (s: SkillInfo): string => {
  if (s.scene_name && s.scene_name.trim() !== "") return s.scene_name.trim();
  if (s.display_name && s.display_name.trim() !== "") return s.display_name.trim();
  return SCENARIO_LABELS[s.name] ?? s.name;
};

// 阶段三批次3(T3.2): Required Permissions 徽章样式
const permChipStyle: CSSProperties = {
  fontSize: 11,
  color: "var(--accent-soft-text)",
  background: "var(--accent-soft-bg)",
  border: "1px solid var(--border-color)",
  borderRadius: 10,
  padding: "1px 8px",
};

export default function SkillSelectionPanel({
  sessionId,
  onActivated,
}: SkillSelectionPanelProps): JSX.Element {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [activating, setActivating] = useState<string | null>(null);
  const [error, setError] = useState("");

  const loadSkills = useCallback(async (): Promise<void> => {
    setLoading(true);
    try {
      const resp = await adminFetch(`${API_BASE}/skills`);
      const data = (await resp.json()) as SkillInfo[] | { error: string };
      if (Array.isArray(data)) {
        setSkills(data.filter((s) => s.enabled));
      } else {
        setError("加载技能列表失败");
      }
    } catch {
      setError("加载技能列表失败,请确认后端服务已启动");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSkills();
  }, [loadSkills]);

  const activate = useCallback(
    async (name: string): Promise<void> => {
      setActivating(name);
      setError("");
      try {
        const resp = await adminFetch(`${API_BASE}/sessions/${sessionId}/activate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ skill_name: name }),
        });
        if (!resp.ok) {
          const data = (await resp.json()) as { detail?: string; error?: string };
          if (data.detail === "skill_not_found") {
            setError("技能不存在,请刷新列表");
          } else if (data.detail === "skill_switch_not_allowed") {
            setError("当前会话已锁定其他技能,无法切换");
          } else {
            setError(data.error ?? data.detail ?? "激活失败");
          }
          return;
        }
        onActivated(name);
      } catch {
        setError("激活失败,请确认后端服务已启动");
      } finally {
        setActivating(null);
      }
    },
    [sessionId, onActivated],
  );

  return (
    <div>
      <h2 style={{ fontSize: 18, marginBottom: 8 }}>选择技能场景</h2>
      <p style={{ fontSize: 13, color: "#666", marginBottom: 16 }}>
        每个技能对应一组专用的工具、提示词与评估集,激活后将锁定到当前会话。
      </p>

      {loading && <div style={{ color: "#999", fontSize: 13 }}>加载技能列表...</div>}

      {error && (
        <div
          role="alert"
          style={{
            background: "var(--error-bg)", color: "var(--danger-text)", padding: "8px 12px",
            borderRadius: 6, fontSize: 13, marginBottom: 12,
          }}
        >
          {error}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {skills.map((skill) => (
          <button
            key={skill.name}
            onClick={() => void activate(skill.name)}
            disabled={activating !== null}
            style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "14px 16px", borderRadius: 8, border: "1px solid #ddd",
              background: "#fff", cursor: activating !== null ? "not-allowed" : "pointer",
              textAlign: "left", fontSize: 14,
            }}
          >
            <span>
              <span style={{ fontWeight: 600, fontSize: 15 }}>
                {displayName(skill)}
              </span>
              <span style={{ color: "#999", fontSize: 12, marginLeft: 8 }}>
                v{skill.version}
              </span>
              <div style={{ color: "#666", fontSize: 13, marginTop: 4 }}>
                {skill.description}
              </div>
              {/* 阶段三批次3(T3.2): Required Permissions 展示 */}
              {skill.permissions && (
                <div
                  style={{
                    marginTop: 6, display: "flex", flexWrap: "wrap", gap: 6,
                  }}
                >
                  {skill.permissions.allow_file_write && (
                    <span style={permChipStyle}>📁 文件读写</span>
                  )}
                  {skill.permissions.allow_network && (
                    <span style={permChipStyle}>🌐 网络访问</span>
                  )}
                  {skill.permissions.sandbox_enabled && (
                    <span style={permChipStyle}>⚙️ 沙箱执行</span>
                  )}
                  {skill.permissions.rules.map((r, i) => (
                    <span key={i} style={permChipStyle}>
                      🔐 {r.tool}
                      {r.domains.length > 0 && ` → ${r.domains.join(",")}`}
                      {r.paths.length > 0 && ` → ${r.paths.join(",")}`}
                    </span>
                  ))}
                  {skill.permissions.max_file_size_mb < 50 && (
                    <span style={permChipStyle}>📦 ≤{skill.permissions.max_file_size_mb}MB</span>
                  )}
                </div>
              )}
            </span>
            <span
              style={{
                fontSize: 12, color: "#1976d2", flexShrink: 0, marginLeft: 12,
              }}
            >
              {activating === skill.name ? "激活中..." : "激活 →"}
            </span>
          </button>
        ))}
      </div>

      {!loading && skills.length === 0 && (
        <div style={{ color: "#999", fontSize: 13 }}>暂无可用技能</div>
      )}
    </div>
  );
}
