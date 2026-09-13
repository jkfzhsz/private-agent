// 0.6.0 P1(会议室) —— 房间信息条(设计文档 §7.3)。
//
// 作用: 进入房间对话后, 让用户**随时看得见**这间房是谁在主持、有谁、产物
// 落在哪个目录 —— 产物交接是本功能的核心, 目录不可见则交接退化为猜测。
//
// UI 约束(蒋先生既定偏好): 单行紧凑、不悬浮堆图标、不默认展开任何面板。
// 超长路径用省略号 + title 兜底, 不换行撑高头部。
import { useState } from "react";

import { roleBadge, roleName, type RoomInfo } from "../utils/rooms";

export interface RoomInfoBarProps {
  info: RoomInfo;
  /** 返回房间列表(会议室视图首页)。 */
  onExit: () => void;
  /** 轻提示(打开目录失败 / 已复制路径等)。 */
  onNotify?: (text: string, ok?: boolean) => void;
}

export default function RoomInfoBar({
  info,
  onExit,
  onNotify,
}: RoomInfoBarProps): JSX.Element {
  const [busy, setBusy] = useState(false);

  const copyPath = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(info.room_dir);
      onNotify?.("房间目录已复制", true);
    } catch {
      onNotify?.("复制失败", false);
    }
  };

  const openDir = async (): Promise<void> => {
    setBusy(true);
    try {
      const bridge = window.pa?.openPath;
      if (!bridge) {
        // 浏览器 dev(vite) 无 Electron 桥: 降级为复制路径, 不让按钮变死键
        await copyPath();
        return;
      }
      const res = await bridge(info.room_dir);
      if (!res?.ok) {
        onNotify?.(`打开目录失败: ${res?.error ?? "未知原因"}`, false);
      }
    } catch (e) {
      onNotify?.(`打开目录失败: ${String(e)}`, false);
    } finally {
      setBusy(false);
    }
  };

  const members = info.members ?? [];
  const artifacts = info.artifacts?.length ?? 0;
  const notes = info.notes?.length ?? 0;

  return (
    <div
      data-testid="room-info-bar"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        flexWrap: "nowrap",
        padding: "8px 12px",
        marginBottom: 8,
        borderRadius: "var(--radius-sm)",
        background: "var(--accent-soft-bg)",
        border: "1px solid var(--border-color)",
        fontSize: 12,
        color: "var(--text-secondary)",
        flexShrink: 0,
        overflow: "hidden",
      }}
    >
      <button
        className="fs-11"
        data-testid="room-exit-btn"
        onClick={onExit}
        title="返回会议室列表(房间与产物保留)"
        style={{
          flexShrink: 0,
          padding: "3px 10px",
          borderRadius: 8,
          border: "1px solid var(--border-color)",
          background: "var(--panel-bg-solid)",
          color: "var(--text-primary)",
          cursor: "pointer",
          fontFamily: "var(--font-sans)",
        }}
      >
        ← 房间列表
      </button>

      <span style={{ fontWeight: 700, color: "var(--text-primary)", flexShrink: 0 }}>
        🏛 会议室
      </span>

      <span style={{ flexShrink: 0 }}>
        主持人{" "}
        <b style={{ color: "var(--text-primary)" }}>{roleName(info.host_role)}</b>
      </span>

      <span
        data-testid="room-info-members"
        style={{ flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
      >
        成员 {members.map((m) => roleBadge(m)).join(" · ")}
      </span>

      {/* 产物/交接计数: 让"交接是否发生过"一眼可见 */}
      <span className="fs-11" style={{ flexShrink: 0, color: "var(--text-tertiary)" }}>
        产物 {artifacts} · 交接 {notes}
      </span>

      <span style={{ flex: 1, minWidth: 12 }} />

      <span
        data-testid="room-info-dir"
        title={info.room_dir}
        onClick={() => void copyPath()}
        style={{
          flexShrink: 1,
          minWidth: 60,
          maxWidth: 320,
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: "var(--text-tertiary)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          direction: "rtl",
          textAlign: "left",
          cursor: "pointer",
        }}
      >
        {info.room_dir}
      </span>

      <button
        className="fs-11"
        data-testid="room-open-dir-btn"
        onClick={() => void openDir()}
        disabled={busy}
        title="在文件管理器中打开房间共享目录"
        style={{
          flexShrink: 0,
          padding: "3px 10px",
          borderRadius: 8,
          border: "1px solid var(--border-color)",
          background: "var(--panel-bg-solid)",
          color: "var(--text-primary)",
          cursor: busy ? "wait" : "pointer",
          fontFamily: "var(--font-sans)",
        }}
      >
        📁 打开目录
      </button>
    </div>
  );
}
