// 0.6.0 P1(会议室) —— 前端共享层: 角色表 + 房间 API。
//
// 设计文档: docs/next-phase-plan-2026-09-11-meeting-room.md §6.3 / §7
//
// 为什么单独抽一层: 房间概念被三处消费 —— 会议室视图(房间列表/新建弹层)、
// 房间信息条(主持人/成员展示)、App 房间接线(进入/恢复)。角色中文名与 API
// 路径若各写一份, 必然漂移(既有 SCENE_NAME_MAP 已在 Sidebar 与 App 各存一份)。
// 此处作为新增代码的单一来源; 后端对应 ROLE_LABELS(core/room.py)。
import { adminFetch } from "./apiClient";

const API_BASE = "http://127.0.0.1:8765/admin";

/** 房间角色(顺序 = UI 展示顺序; 与后端 core/room.py ROOM_ROLES 一致)。 */
export const ROOM_ROLES: readonly {
  key: string;
  name: string;
  emoji: string;
  desc: string;
}[] = [
  { key: "office", name: "子瞻", emoji: "📄", desc: "项目分析 · 办公文档" },
  { key: "data_analysis", name: "白圭", emoji: "📈", desc: "投资金融 · 数据分析" },
  { key: "frontend_design", name: "清和", emoji: "🎨", desc: "前端设计 · 视觉交付" },
];

/** 角色 → 中文名(未知角色原样返回, 与后端 role_label 同语义)。 */
export function roleName(key: string | null | undefined): string {
  if (!key) return "";
  return ROOM_ROLES.find((r) => r.key === key)?.name ?? key;
}

/** 角色 → `子瞻(office)` 形式。 */
export function roleLabel(key: string | null | undefined): string {
  if (!key) return "";
  const found = ROOM_ROLES.find((r) => r.key === key);
  return found ? `${found.name}(${found.key})` : key;
}

/** 角色 → 带 emoji 的展示名(列表/信息条用)。 */
export function roleBadge(key: string | null | undefined): string {
  if (!key) return "";
  const found = ROOM_ROLES.find((r) => r.key === key);
  return found ? `${found.emoji} ${found.name}` : key;
}

/** sessions.room_meta(P1 后端写入)。 */
export interface RoomMeta {
  host_role?: string;
  members?: string[];
  goal?: string;
  room_key?: string;
}

/** /admin/sessions 列表项中与房间相关的字段。 */
export interface RoomSummary {
  id: number;
  title: string | null;
  status: string;
  kind?: string | null;
  updated_at: string | null;
  last_turn: number;
  room_meta: RoomMeta;
}

/** GET /admin/rooms/{id} 返回体。 */
export interface RoomInfo {
  ok: boolean;
  session_id: number;
  title: string | null;
  status: string;
  host_role: string;
  members: string[];
  goal: string;
  room_dir: string;
  exists: boolean;
  artifacts: { name: string; size: number; mtime: number }[];
  notes: { name: string; size: number; mtime: number }[];
}

export interface CreateRoomInput {
  goal: string;
  members: string[];
  host_role: string;
  title?: string;
}

export interface CreateRoomResult {
  ok: boolean;
  id: number;
  kind: string;
  host_role: string;
  members: string[];
  room_dir: string;
  room_meta: RoomMeta;
}

/** 列出房间(含尚未产生对话的新房 —— has_messages=false 是必需参数)。 */
export async function listRooms(limit = 100): Promise<RoomSummary[]> {
  const resp = await adminFetch(
    `${API_BASE}/sessions?limit=${limit}&has_messages=false`
  );
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const all = (await resp.json()) as RoomSummary[];
  return (all ?? []).filter((s) => s.kind === "room");
}

/** 读取单个房间(元数据 + 产物/交接清单)。 */
export async function getRoom(sessionId: number): Promise<RoomInfo> {
  const resp = await adminFetch(`${API_BASE}/rooms/${sessionId}`);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(
      (data as { message?: string; error?: string }).message ??
        (data as { error?: string }).error ??
        `HTTP ${resp.status}`
    );
  }
  return data as RoomInfo;
}

/** 创建房间: 建房 + 建共享目录 + 写 README.md, 返回新会话 id。 */
export async function createRoom(
  input: CreateRoomInput
): Promise<CreateRoomResult> {
  const resp = await adminFetch(`${API_BASE}/rooms`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg =
      (data as { message?: string; error?: string }).message ??
      (data as { error?: string }).error ??
      `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return data as CreateRoomResult;
}
