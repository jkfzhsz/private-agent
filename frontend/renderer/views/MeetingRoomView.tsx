// 0.6.0 P1(会议室) —— 会议室视图: 已有房间列表 + 新建会议室弹层。
//
// 设计文档: docs/next-phase-plan-2026-09-11-meeting-room.md §7.2
//
// 交互契约(需求 5):
// - 入口独立于四个单智能体窗口: 用户在此**选择成员组合**并**指定本次主持人**;
// - 主持人只能从**已勾选的成员**中产生(后端 normalize_roles 同样强校验,
//   前端提前拦住是为了给出即时反馈, 而不是等 400);
// - 创建成功后直接进入房间(由 App 切换到房间对话视图)。
//
// UI 约束(蒋先生既定偏好): 不悬浮堆图标; 房间列表单行紧凑;
// 弹层仅在有意义时展示必填项, 不做多步向导。
import { useCallback, useEffect, useState } from "react";

import {
  ROOM_ROLES,
  createRoom,
  listRooms,
  roleBadge,
  roleName,
  type RoomSummary,
} from "../utils/rooms";

export interface MeetingRoomViewProps {
  /** 进入房间(由 App 切到房间对话视图并加载 room_meta)。 */
  onEnterRoom: (roomId: number) => void;
}

/** 弹层默认勾选(与设计文档 §7.2 示意一致): 子瞻 + 白圭, 主持人子瞻。 */
const DEFAULT_MEMBERS = ["office", "data_analysis"];
const DEFAULT_HOST = "office";

const ROLE_KEYS = ROOM_ROLES.map((r) => r.key);

export default function MeetingRoomView({
  onEnterRoom,
}: MeetingRoomViewProps): JSX.Element {
  const [rooms, setRooms] = useState<RoomSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [dialogOpen, setDialogOpen] = useState(false);
  const [goal, setGoal] = useState("");
  const [members, setMembers] = useState<string[]>(DEFAULT_MEMBERS);
  const [hostRole, setHostRole] = useState<string>(DEFAULT_HOST);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError("");
    try {
      setRooms(await listRooms());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openDialog = (): void => {
    setGoal("");
    setMembers(DEFAULT_MEMBERS);
    setHostRole(DEFAULT_HOST);
    setCreateError("");
    setDialogOpen(true);
  };

  // 成员勾选(保持 ROOM_ROLES 顺序, 与后端 normalize_roles 的去重保序一致)。
  const toggleMember = (key: string): void => {
    const has = members.includes(key);
    const next = ROLE_KEYS.filter((k) =>
      k === key ? !has : members.includes(k)
    );
    setMembers(next);
    // 主持人必须属于成员: 取消勾选当前主持人 → 清空, 由用户重新指定
    if (!next.includes(hostRole)) setHostRole("");
  };

  const memberKeysInOrder = (): string[] =>
    ROLE_KEYS.filter((k) => members.includes(k));

  const canSubmit =
    goal.trim().length > 0 &&
    memberKeysInOrder().length > 0 &&
    memberKeysInOrder().includes(hostRole) &&
    !creating;

  const submit = async (): Promise<void> => {
    if (!canSubmit) return;
    setCreating(true);
    setCreateError("");
    try {
      const res = await createRoom({
        goal: goal.trim(),
        members: memberKeysInOrder(),
        host_role: hostRole,
      });
      setDialogOpen(false);
      onEnterRoom(res.id);
      void load();
    } catch (e) {
      setCreateError(String(e));
    } finally {
      setCreating(false);
    }
  };

  const orderedMembers = memberKeysInOrder();

  return (
    <div
      className="view-scroll animate-in"
      data-testid="meeting-room-view"
      style={{ flex: 1, padding: 24, minHeight: 0 }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 16,
        }}
      >
        <div>
          <div className="page-title">会议室</div>
          <div className="fs-12 text-tertiary" style={{ marginTop: 6 }}>
            选定参会成员与本次主持人; 主持人统筹任务分配与执行, 成员产物统一
            落在房间共享目录, 可直接互相取用。
          </div>
        </div>
        <button
          className="btn-primary"
          data-testid="room-new-btn"
          onClick={openDialog}
          style={{ flexShrink: 0 }}
        >
          ＋ 新建会议室
        </button>
      </div>

      {error && (
        <div
          className="fs-12"
          style={{
            color: "var(--danger-text)",
            background: "var(--error-bg)",
            border: "1px solid var(--border-color)",
            borderRadius: "var(--radius-sm)",
            padding: "8px 12px",
          }}
        >
          房间列表加载失败: {error}
        </div>
      )}

      <div className="flex-col" style={{ gap: 8 }}>
        <div className="subhead" style={{ marginBottom: 0 }}>
          已有会议室
          {rooms.length > 0 && (
            <span className="fs-11 text-tertiary" style={{ marginLeft: 8, fontWeight: 400 }}>
              共 {rooms.length} 间
            </span>
          )}
        </div>

        {loading && rooms.length === 0 && (
          <div className="fs-12 text-tertiary" style={{ padding: "12px 2px" }}>
            加载中…
          </div>
        )}

        {!loading && rooms.length === 0 && (
          <div
            className="fs-12 text-tertiary"
            data-testid="room-empty"
            style={{ padding: "12px 2px", lineHeight: 1.8 }}
          >
            还没有会议室。点击右上角「新建会议室」，选择成员并指定主持人后开始
            协同；同一房间内的产物对全体成员可见、可改。
          </div>
        )}

        {rooms.map((r) => {
          const host = r.room_meta?.host_role ?? "";
          const n = (r.room_meta?.members ?? []).length;
          const archived = r.status === "archived";
          const title = r.title || r.room_meta?.goal || `#${r.id}`;
          return (
            <button
              key={r.id}
              className="stat-card hover-highlight"
              data-testid={`room-row-${r.id}`}
              onClick={() => onEnterRoom(r.id)}
              title={`进入会议室 ${title}`}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "12px 16px",
                textAlign: "left",
                cursor: "pointer",
                width: "100%",
                // 同上: stat-card 有 padding+border, 不设 border-box 会横向溢出
                boxSizing: "border-box",
                fontFamily: "var(--font-sans)",
              }}
            >
              <span style={{ fontSize: 15 }}>🏛</span>
              <span
                className="fw-600"
                style={{
                  fontSize: 13,
                  color: "var(--text-primary)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: "46%",
                }}
              >
                {title}
              </span>
              <span
                className="fs-10"
                style={{
                  flexShrink: 0,
                  padding: "1px 8px",
                  borderRadius: 8,
                  background: archived ? "var(--neutral-bg)" : "var(--success-bg)",
                  color: archived ? "var(--neutral-text)" : "var(--success-text)",
                  fontWeight: 600,
                }}
              >
                {archived ? "已归档" : "进行中"}
              </span>
              <span style={{ flex: 1 }} />
              <span className="fs-11 text-tertiary" style={{ flexShrink: 0 }}>
                {host ? `主持人 ${roleName(host)}` : "主持人未记录"}
                {n > 0 ? ` · ${n} 人` : ""}
              </span>
            </button>
          );
        })}
      </div>

      {dialogOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="新建会议室"
          data-testid="room-create-dialog"
          onClick={() => setDialogOpen(false)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 9999,
            background: "rgba(0,0,0,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 460,
              maxWidth: "92vw",
              borderRadius: 16,
              background: "var(--panel-bg-solid)",
              border: "1px solid var(--border-strong)",
              boxShadow: "0 12px 48px rgba(0,0,0,0.35)",
              padding: 20,
              animation: "flow-slide-up 0.25s var(--transition-smooth) both",
            }}
          >
            <div
              style={{
                fontSize: 15,
                fontWeight: 700,
                color: "var(--text-primary)",
                marginBottom: 14,
              }}
            >
              新建会议室
            </div>

            {/* 会议主题 → 写入房间 README.md 的任务目标 */}
            <div className="fs-12 fw-600" style={{ marginBottom: 6 }}>
              会议主题
            </div>
            <input
              className="flow-input"
              data-testid="room-goal-input"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="例: 做一份 Q3 经营分析汇报 PPT"
              style={{
                width: "100%",
                // 2026-09-12(蒋先生反馈: 主题输入框撑出弹窗): .flow-input 自带
                // padding 10/16 + border 1 且未设 border-box —— width:100% 按
                // 内容盒计算会溢出 34px, 必须显式按边框盒计宽。
                boxSizing: "border-box",
                marginBottom: 14,
              }}
            />

            {/* 参会成员(可多选) */}
            <div className="fs-12 fw-600" style={{ marginBottom: 6 }}>
              参会成员
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
              {ROOM_ROLES.map((r) => {
                const on = members.includes(r.key);
                return (
                  <button
                    key={r.key}
                    data-testid={`room-member-${r.key}`}
                    aria-pressed={on}
                    onClick={() => toggleMember(r.key)}
                    title={r.desc}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 7,
                      padding: "7px 12px",
                      borderRadius: "var(--radius-sm)",
                      border: `1px solid ${on ? "var(--border-strong)" : "var(--border-color)"}`,
                      background: on ? "var(--accent-soft-bg)" : "var(--panel-bg-solid)",
                      color: on ? "var(--accent-soft-text)" : "var(--text-secondary)",
                      cursor: "pointer",
                      fontFamily: "var(--font-sans)",
                      fontSize: 12,
                      fontWeight: on ? 600 : 400,
                    }}
                  >
                    <span>{on ? "☑" : "☐"}</span>
                    <span>
                      {r.emoji} {r.name}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* 本次主持人(单选, 仅从已选成员中选) */}
            <div className="fs-12 fw-600" style={{ marginBottom: 6 }}>
              本次主持人
              <span className="fs-11 text-tertiary" style={{ marginLeft: 8, fontWeight: 400 }}>
                仅可从已选成员中指定
              </span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 6 }}>
              {orderedMembers.length === 0 && (
                <span className="fs-12 text-tertiary">请先勾选参会成员</span>
              )}
              {orderedMembers.map((k) => {
                const on = hostRole === k;
                return (
                  <button
                    key={k}
                    data-testid={`room-host-${k}`}
                    aria-pressed={on}
                    onClick={() => setHostRole(k)}
                    style={{
                      padding: "6px 14px",
                      borderRadius: "var(--radius-sm)",
                      border: `1px solid ${on ? "var(--accent-soft-text)" : "var(--border-color)"}`,
                      background: on ? "var(--accent-soft-bg)" : "var(--panel-bg-solid)",
                      color: on ? "var(--accent-soft-text)" : "var(--text-secondary)",
                      cursor: "pointer",
                      fontFamily: "var(--font-sans)",
                      fontSize: 12,
                      fontWeight: on ? 600 : 400,
                    }}
                  >
                    {on ? "●" : "○"} {roleBadge(k)}
                  </button>
                );
              })}
            </div>
            {orderedMembers.length > 0 && !hostRole && (
              <div className="fs-11" style={{ color: "var(--warning-text)", marginBottom: 6 }}>
                请指定本次主持人
              </div>
            )}

            {createError && (
              <div
                className="fs-12"
                style={{ color: "var(--danger-text)", marginTop: 10 }}
              >
                创建失败: {createError}
              </div>
            )}

            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                gap: 10,
                marginTop: 18,
              }}
            >
              <button
                className="btn-ghost"
                onClick={() => setDialogOpen(false)}
                style={{ padding: "7px 16px", fontSize: 13 }}
              >
                取消
              </button>
              <button
                className="btn-primary"
                data-testid="room-create-submit"
                disabled={!canSubmit}
                onClick={() => void submit()}
                style={{
                  opacity: canSubmit ? 1 : 0.5,
                  cursor: canSubmit ? "pointer" : "not-allowed",
                }}
              >
                {creating ? "创建中…" : "创建并进入"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
