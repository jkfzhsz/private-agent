// 0.6.0 F1-3(2026-08-28) - Mission 长任务卡片面板
//
// 展示本会话的 mission 状态: 状态徽标(planning/executing/supervising/paused/
// done/failed/cancelled/escalated)、目标、里程碑列表(逐项 status)、台账尾部
// (journal 尾 5 条)、escalated 裁决按钮位(D-3 接线后激活回调)。
// 风格复用 SubagentPanel(var(--subagent-*) token, 亮/暗双值)。
//
// WS 事件 schema 契约(0.6.0 P3 起后端已按此推送, session_id 过滤同 subagent_*):
// - mission_created   {mission_id, session_id, goal, state, plan: [{id, milestone,
//                       executor_type, role?, status}]}   ← V1(G5 修复): 补 goal+plan
// - mission_update    {mission_id, session_id, state, detail?, plan?}   ← V2: 里程碑状态实时下发
// - mission_journal   {mission_id, session_id, entry: {kind, ts, detail}}
// - mission_escalated {mission_id, session_id, reason}
// - mission_done      {mission_id, session_id, state: done|failed|cancelled, result?}
// 可靠性兜底: GET /admin/missions?session_id= DB 轮询重建(同 R7 模式, V3 已接线)。
import { useMemo, useState } from "react";

export type MissionStateKind =
  | "planning"
  | "executing"
  | "supervising"
  | "paused"
  | "done"
  | "failed"
  | "cancelled"
  | "escalated";

export interface MissionMilestone {
  id: string;
  milestone: string;
  executor_type: string;
  /** 0.6.0 P3(W4 会议室): 里程碑执行角色(子瞻/白圭/清和); 无 = 编排者自有执行体 */
  role?: string | null;
  status?: string;
}

export interface MissionJournalEntry {
  kind: string;
  ts: string;
  detail?: string;
}

export interface MissionState {
  id: number;
  goal: string;
  state: MissionStateKind;
  milestones: MissionMilestone[];
  journal: MissionJournalEntry[];
  detail?: string;
  result?: string;
  error?: string;
  createdAt: number;
}

export function createMission(
  id: number,
  goal: string,
  state: MissionStateKind = "planning"
): MissionState {
  return {
    id,
    goal,
    state,
    milestones: [],
    journal: [],
    createdAt: Date.now(),
  };
}

// ── 0.6.0 P3(V3): DB 轮询兜底的行 → MissionState 归一化 ────────────────────
// 后端 GET /admin/missions 返回 JSONB 列(asyncpg 可能给 str 或已解析对象),
// 且字段来自 missions 表(charter/plan/journal), 需映射到渲染用的 MissionState。
// 纯函数导出以便单测(不渲染组件即可验证)。

function asObject(v: unknown): Record<string, unknown> {
  if (v && typeof v === "object" && !Array.isArray(v)) {
    return v as Record<string, unknown>;
  }
  if (typeof v === "string") {
    try {
      const parsed: unknown = JSON.parse(v);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      /* 脏数据降级为空对象 */
    }
  }
  return {};
}

function asArray(v: unknown): unknown[] {
  if (Array.isArray(v)) return v;
  if (typeof v === "string") {
    try {
      const parsed: unknown = JSON.parse(v);
      if (Array.isArray(parsed)) return parsed;
    } catch {
      /* 脏数据降级为空数组 */
    }
  }
  return [];
}

/** missions.plan(JSONB str/数组) → 里程碑视图数组。 */
export function normalizePlan(v: unknown): MissionMilestone[] {
  return asArray(v)
    .filter((x): x is Record<string, unknown> => !!x && typeof x === "object")
    .map((o, i) => ({
      id: String(o.id ?? `m${i}`),
      milestone: String(o.milestone ?? ""),
      executor_type: String(o.executor_type ?? "subagent"),
      role: (o.role ?? null) as string | null,
      status: o.status != null ? String(o.status) : undefined,
    }));
}

/** missions.journal(JSONB str/数组) → 台账条目数组。 */
export function normalizeJournal(v: unknown): MissionJournalEntry[] {
  return asArray(v)
    .filter((x): x is Record<string, unknown> => !!x && typeof x === "object")
    .map((o) => ({
      kind: String(o.kind ?? ""),
      ts: String(o.ts ?? ""),
      detail: o.detail != null ? String(o.detail) : undefined,
    }));
}

/**
 * GET /admin/missions 行 → MissionState(WS 已有状态优先, DB 补缺口)。
 *
 * 合并原则(同 fetchSubagents): WS 实时收到的 goal/detail/result/createdAt
 * 优先保留; state/milestones/journal/error 以 DB 为准(DB 是终局事实)。
 */
export function missionStateFromRow(
  row: Record<string, unknown>,
  prev?: MissionState
): MissionState {
  const id = Number(row.id);
  const charter = asObject(row.charter);
  return {
    id,
    goal: prev?.goal || String(charter.goal ?? ""),
    state: (row.state as MissionStateKind) ?? prev?.state ?? "planning",
    milestones: normalizePlan(row.plan),
    journal: normalizeJournal(row.journal),
    detail: prev?.detail,
    result: prev?.result,
    error: row.error != null ? String(row.error) : undefined,
    createdAt: prev?.createdAt ?? Date.now(),
  };
}

const STATE_META: Record<
  MissionStateKind,
  { bg: string; color: string; label: string; icon: string }
> = {
  // 复用 SubagentPanel token; escalated 需醒目橙红(等用户裁决)
  planning: { bg: "var(--subagent-pending-bg)", color: "var(--subagent-pending-color)", label: "规划中", icon: "📋" },
  executing: { bg: "var(--subagent-running-bg)", color: "var(--subagent-running-color)", label: "执行中", icon: "🔵" },
  supervising: { bg: "var(--subagent-running-bg)", color: "var(--subagent-running-color)", label: "监督中", icon: "🧭" },
  paused: { bg: "var(--subagent-pending-bg)", color: "var(--subagent-pending-color)", label: "已暂停", icon: "⏸" },
  done: { bg: "var(--subagent-succeeded-bg)", color: "var(--subagent-succeeded-color)", label: "已完成", icon: "✅" },
  failed: { bg: "var(--subagent-failed-bg)", color: "var(--subagent-failed-color)", label: "失败", icon: "❌" },
  cancelled: { bg: "var(--subagent-cancelled-bg)", color: "var(--subagent-cancelled-color)", label: "已中止", icon: "⏹" },
  escalated: { bg: "rgba(230, 126, 34, 0.18)", color: "#c0392b", label: "待裁决", icon: "⚠️" },
};

const EXECUTOR_ICON: Record<string, string> = {
  subagent: "🤖",
  script: "📜",
  wait: "⏱",
};

// 0.6.0 P3(V4 会议室): 里程碑 role 徽标 —— 与四窗口/房间成员同一套角色标识,
// 用户在进度里能直接看出"这一步是谁在干"。
const ROLE_META: Record<string, { icon: string; name: string }> = {
  office: { icon: "📄", name: "子瞻" },
  data_analysis: { icon: "📈", name: "白圭" },
  frontend_design: { icon: "🎨", name: "清和" },
};

interface Props {
  missions: Record<number, MissionState>;
  /** escalated 时用户批准改道(D-3 接线后由 App 传 WS 动作; F1 仅展示) */
  onApproveFallback?: (missionId: number) => void;
  onAbort?: (missionId: number) => void;
  onClearFinished?: () => void;
}

export default function MissionPanel({
  missions,
  onApproveFallback,
  onAbort,
  onClearFinished,
}: Props) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const items = useMemo(() => Object.values(missions), [missions]);
  if (items.length === 0) return null;

  const finished = (m: MissionState) =>
    m.state === "done" || m.state === "failed" || m.state === "cancelled";

  return (
    <div
      data-testid="mission-panel"
      style={{ display: "flex", flexDirection: "column", gap: 8, margin: "8px 0" }}
    >
      {items.map((m) => {
        const meta = STATE_META[m.state] ?? STATE_META.executing;
        const expanded = expandedId === m.id;
        const doneCount = m.milestones.filter(
          (ms) => ms.status === "done" || ms.status === "succeeded"
        ).length;
        return (
          <div
            key={m.id}
            data-testid={`mission-card-${m.id}`}
            style={{
              border: "1px solid var(--border-color, rgba(0,0,0,0.1))",
              borderRadius: 10,
              padding: "10px 12px",
              background: "var(--chat-ai-bg, rgba(0,0,0,0.03))",
              fontSize: 13,
            }}
          >
            <div
              data-testid={`mission-header-${m.id}`}
              style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
              onClick={() => setExpandedId(expanded ? null : m.id)}
            >
              <span
                  data-testid={`mission-state-${m.id}`}
                style={{
                  background: meta.bg,
                  color: meta.color,
                  borderRadius: 8,
                  padding: "2px 8px",
                  fontWeight: 600,
                  whiteSpace: "nowrap",
                }}
              >
                {meta.icon} #{m.id} {meta.label}
              </span>
              <span
                style={{
                  flex: 1,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  color: "var(--text-primary, inherit)",
                }}
                title={m.goal}
              >
                {m.goal}
              </span>
              {m.milestones.length > 0 && (
                <span style={{ color: "var(--text-secondary, #888)", whiteSpace: "nowrap" }}>
                  {doneCount}/{m.milestones.length}
                </span>
              )}
            </div>

            {expanded && (
              <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                {m.milestones.length > 0 && (
                  <div>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>里程碑</div>
                    {m.milestones.map((ms) => {
                      // role 徽标: 已知角色用中文名; 未知角色原样展示(显示层
                      // 不得静默丢数据, 与 utils/rooms.roleBadge 降级语义一致)
                      const role = ms.role
                        ? (ROLE_META[ms.role] ?? { icon: "•", name: ms.role })
                        : undefined;
                      return (
                        <div
                          key={ms.id}
                          style={{ display: "flex", gap: 6, alignItems: "baseline" }}
                        >
                          <span>{EXECUTOR_ICON[ms.executor_type] ?? "•"}</span>
                          <span style={{ color: "var(--text-secondary, #888)" }}>
                            [{ms.id}]
                          </span>
                          <span style={{ flex: 1 }}>{ms.milestone}</span>
                          {/* V4: role 徽标(仅房间任务才有, 不占额外行) */}
                          {role && (
                            <span
                              data-testid={`mission-role-${m.id}-${ms.id}`}
                              title={`由 ${role.name}(${ms.role}) 执行`}
                              style={{
                                flexShrink: 0,
                                fontSize: 10,
                                padding: "1px 7px",
                                borderRadius: 8,
                                background: "var(--accent-soft-bg)",
                                color: "var(--accent-soft-text)",
                                fontWeight: 600,
                                whiteSpace: "nowrap",
                              }}
                            >
                              {role.icon} {role.name}
                            </span>
                          )}
                          <span style={{ color: "var(--text-secondary, #888)" }}>
                            {ms.status ?? "pending"}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
                {m.journal.length > 0 && (
                  <div>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>
                      台账(共 {m.journal.length} 条)
                    </div>
                    {m.journal.slice(-5).map((e, i) => (
                      <div key={i} style={{ color: "var(--text-secondary, #888)" }}>
                        [{e.kind}] {e.detail ?? ""}
                      </div>
                    ))}
                  </div>
                )}
                {m.error && (
                  <div style={{ color: "var(--subagent-failed-color, #c0392b)" }}>
                    错误: {m.error}
                  </div>
                )}
                {m.state === "escalated" && (
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      data-testid={`mission-approve-${m.id}`}
                      onClick={() => onApproveFallback?.(m.id)}
                      style={{
                        padding: "4px 10px",
                        borderRadius: 6,
                        border: "1px solid var(--border-color, #ccc)",
                        cursor: "pointer",
                      }}
                    >
                      批准改道
                    </button>
                    <button
                      data-testid={`mission-abort-${m.id}`}
                      onClick={() => onAbort?.(m.id)}
                      style={{
                        padding: "4px 10px",
                        borderRadius: 6,
                        border: "1px solid var(--border-color, #ccc)",
                        cursor: "pointer",
                      }}
                    >
                      中止任务
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
      {onClearFinished && items.some(finished) && (
        <button
          onClick={onClearFinished}
          style={{
            alignSelf: "flex-end",
            fontSize: 12,
            padding: "2px 8px",
            borderRadius: 6,
            border: "1px solid var(--border-color, #ccc)",
            background: "transparent",
            cursor: "pointer",
            color: "var(--text-secondary, #888)",
          }}
        >
          清除已完成任务
        </button>
      )}
    </div>
  );
}
