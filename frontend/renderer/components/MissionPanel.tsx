// 0.6.0 F1-3(2026-08-28) - Mission 长任务卡片面板
//
// 展示本会话的 mission 状态: 状态徽标(planning/executing/supervising/paused/
// done/failed/cancelled/escalated)、目标、里程碑列表(逐项 status)、台账尾部
// (journal 尾 5 条)、escalated 裁决按钮位(D-3 接线后激活回调)。
// 风格复用 SubagentPanel(var(--subagent-*) token, 亮/暗双值)。
//
// WS 事件 schema 先行契约(D 批后端按此实现推送, session_id 过滤同 subagent_*):
// - mission_created   {mission_id, session_id, goal, state}
// - mission_update    {mission_id, session_id, state, detail?}
// - mission_journal   {mission_id, session_id, entry: {kind, ts, detail}}
// - mission_escalated {mission_id, session_id, reason}
// - mission_done      {mission_id, session_id, state: done|failed|cancelled, result?}
// 可靠性兜底(D 批): GET /admin/missions?session_id= DB 轮询重建(同 R7 模式)。
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
                    {m.milestones.map((ms) => (
                      <div key={ms.id} style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                        <span>{EXECUTOR_ICON[ms.executor_type] ?? "•"}</span>
                        <span style={{ color: "var(--text-secondary, #888)" }}>[{ms.id}]</span>
                        <span style={{ flex: 1 }}>{ms.milestone}</span>
                        <span style={{ color: "var(--text-secondary, #888)" }}>
                          {ms.status ?? "pending"}
                        </span>
                      </div>
                    ))}
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
