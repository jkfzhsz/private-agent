// P3-2 批次2(2026-08-17): 单轮对话卡片 —— 从 App.tsx 拆出的消息渲染单元
// App.tsx 5023 行 → 目标 ≤1500 的关键拆分: turnGroups.map 内 455 行渲染
// 移至本组件。props 用薄回调(封装 sendWs/输入编辑等副作用), 组件内只做展示。
// 注意: 类型(EventType/ReactEvent/TurnGroupData)随本文件导出, App 单向依赖。
import { useEffect, useState } from "react";
import RobotAvatar from "./RobotAvatar";
import { ThinkingWait, MsgActionBtn, formatPayload, errorCategoryColor, extractImagePaths, imagePathToUrl } from "../utils/chatUi";
import { renderFinalText } from "../utils/renderFinal";

// 2026-08-19(工具执行无反馈): tool_call 发出后、tool_result 返回前显示
// "⏳ 执行中 · 已 Xs" 实时耗时 —— 长任务(codegraph 索引/全量回归等)卡住时
// 用户能感知"仍在执行", 而非 tool_call 仅一行 🔧 文案后无声死等。
function ToolPendingTimer({ startedAt }: { startedAt: number }): JSX.Element {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const iv = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(iv);
  }, []);
  const s = Math.max(1, Math.round((now - startedAt) / 1000));
  return (
    <span style={{ color: "var(--warning-text)", marginLeft: 8, fontWeight: 600 }}>
      ⏳ 执行中 · 已 {s}s
    </span>
  );
}

// 2026-08-19: 耗时格式化 —— ms → "Xm Ys"(≥60s) 或 "Ys"(<60s)
function formatDuration(ms: number): string {
  const totalSec = Math.max(1, Math.round(ms / 1000));
  if (totalSec < 60) return `${totalSec}s`;
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

// ──────────────────────────────────────────────────────────────────────────────
// 类型(原 App.tsx, 随 TurnCard 迁移)
// ──────────────────────────────────────────────────────────────────────────────
export type EventType =
  | "user"
  | "thinking"
  | "tool_call"
  | "tool_result"
  | "delta"
  | "final"
  | "error"
  | "sandbox_output"
  | "tool_confirmation_required"
  | "tool_confirmation_result"
  | "turn_paused"
  | "turn_resumed"
  | "iteration_limit_reached"
  | "status";

export interface ReactEvent {
  id: number;
  session_id: number;
  turn: number;
  event_type: EventType;
  payload: Record<string, unknown>;
  ts: number;
  replayed?: boolean;
}

// 2026-08-12 perf: 预计算的 turn 分组数据 —— render 阶段 O(1) 解构
export interface TurnGroupData {
  user?: ReactEvent;
  thinking?: ReactEvent;
  final?: ReactEvent;
  error?: ReactEvent;
  confirmResult?: ReactEvent;
  /** 2026-08-19: 最新 status 事件(LLM 调用中/工具执行中, persist=False
   *  不入库, 实时推送; 工具心跳每 10s 覆盖更新 → 前端展示实时耗时) */
  status?: ReactEvent;
  toolEvents: ReactEvent[];
  confirmEvents: ReactEvent[];
  sandboxText: string;
  deltaText: string;
  finalText: string;
  thinkingText: string;
}

// ──────────────────────────────────────────────────────────────────────────────
// 单轮对话卡片
// ──────────────────────────────────────────────────────────────────────────────
export interface TurnCardProps {
  turn: number;
  g: TurnGroupData;
  /** 是否为最后一轮(编辑重发按钮仅在最后一轮显示) */
  isLast: boolean;
  isGenerating: boolean;
  thinkingOpen: boolean;
  onToggleThinking: () => void;
  deleted: boolean;
  starred: boolean;
  assistantName: string;
  onRegenerate: () => void;
  onToggleStar: () => void;
  onRequestDelete: () => void;
  onCopy: (text: string) => void;
  onCopyCode: (text: string) => void;
  /** 编辑重发(在 App 侧 setEditingOriginal + setInput + focus) */
  onEditOriginal: (content: string) => void;
  /** 权限确认: 同意/拒绝(approved=true/false) */
  onConfirmAction: (confirmationId: string, approved: boolean) => void;
  /** 权限确认: 稍后决定(挂起) */
  onDeferAction: (confirmationId: string) => void;
  /** 2026-08-18(请求卡片点不了): 已过期/已处理的确认 ID 集合 ——
   *  命中则按钮禁用并显示"已过期", 防对失效确认重复点击 */
  expiredConfirmIds: Set<string>;
  /** 2026-08-19: 该轮完成时间戳(ms), null 表示未完成/未结束 */
  completedAt: number | null;
  /** 2026-08-19: 该轮开始时间戳(ms, 首个事件到达时刻), 与 completedAt
   *  差值即该轮总耗时 */
  startedAt: number | null;
  /** 2026-08-19(A 方案): 服务端权威耗时(ms, turn_end 携带 duration_ms)。
   *  优先于 startedAt/completedAt 差值 —— 前端差值会被跨窗口 turn 号
   *  撞车污染(prev.has 短路), 后端 monotonic 计时可靠。 */
  durationMs: number | null;
  /** 2026-08-19(假思考检测): 最近一次收到 react_event 的时刻(ms)。
   *  传给 ThinkingWait 判断"等待期间无任何新事件"→ 提示可能卡住。 */
  lastEventAt?: number;
}

export function TurnCard({
  turn,
  g,
  isLast,
  isGenerating,
  thinkingOpen,
  onToggleThinking,
  deleted,
  starred,
  assistantName,
  onRegenerate,
  onToggleStar,
  onRequestDelete,
  onCopy,
  onCopyCode,
  onEditOriginal,
  onConfirmAction,
  onDeferAction,
  expiredConfirmIds,
  completedAt,
  startedAt,
  durationMs,
  lastEventAt,
}: TurnCardProps): JSX.Element {
  const {
    user: userEv,
    thinking: thinkingEv,
    error: errorEv,
    confirmResult: confirmResultEv,
    toolEvents,
    confirmEvents,
    sandboxText,
    finalText,
    thinkingText,
    status: statusEv,
  } = g;
  // 有用户消息但还没有最终文本 → AI 正在思考
  // 2026-08-18(停止键无效的视觉根源): 仅生成中(isGenerating)才显示
  // "思考中" —— 历史 interrupted 会话的中断轮(无运行 task)此前恒
  // isPending=true, 显示"思考中"等待但停止键不可用(后端无 task),
  // 用户误以为卡死。非生成态的中断轮不显示等待动画。
  // 2026-08-19(假计时 1500s+ 误伤): 进一步加 isLast 限制 —— 只有当前正在
  // 生成的最后一轮显示 ThinkingWait; 历史未完成 turn(此前中断的轮)即使
  // isGenerating=true 也不挂"思考中"假计时器。
  const isPending = isLast && !!userEv && !finalText && !errorEv && isGenerating;

  return (
    <div style={{ marginBottom: 14 }}>
      {userEv && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 10 }}>
          <div
            style={{
              backgroundColor: "var(--chat-user-bg)",
              borderRadius: "12px 12px 2px 12px",
              padding: "8px 14px",
              maxWidth: "80%",
              color: "var(--text-primary)",
            }}
          >
            <pre
              style={{
                margin: 0,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                fontSize: 13,
                fontFamily: "inherit",
                // 2026-08-17(实机修复): 显式透明, 免疫任何全局 pre 背景规则
                backgroundColor: "transparent",
              }}
            >
              {formatPayload("user", userEv.payload)}
            </pre>
          </div>
          {/* 阶段三批次3(T3.4): 编辑重发(最后一条 user 消息, 非生成中) */}
          {!isGenerating && isLast && (
            <button
              onClick={() => onEditOriginal(formatPayload("user", userEv.payload))}
              title="编辑并重发(自动沉淀纠正记忆)"
              style={{
                alignSelf: "center",
                marginLeft: 6,
                padding: "4px 8px",
                fontSize: 11,
                borderRadius: 10,
                border: "1px solid var(--border)",
                background: "var(--panel-bg)",
                color: "var(--text-tertiary)",
                cursor: "pointer",
              }}
            >
              ✎ 编辑
            </button>
          )}
        </div>
      )}

      {userEv && (
        <div style={{ display: "flex", gap: 10 }}>
          {/* 2026-08-08: 助手头像统一桌面图标原图 + 名字(场景会话=场景名, 否则主智能体名) */}
          <RobotAvatar size={32} style={{ marginTop: 2 }} />
          <div className="flex-1-min0">
            <div style={{ fontSize: "var(--fs-body)", fontWeight: 600, color: "var(--text-primary)", marginBottom: 4 }}>
              {assistantName}
            </div>

            {isPending && !thinkingEv && <ThinkingWait lastEventAt={lastEventAt} />}

            {thinkingEv && (
              <div
                style={{
                  border: "1px solid var(--border-color)",
                  borderRadius: 10,
                  marginBottom: 8,
                  overflow: "hidden",
                }}
              >
                <button
                  onClick={onToggleThinking}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    width: "100%",
                    padding: "6px 10px",
                    border: "none",
                    background: "var(--surface-1)",
                    cursor: "pointer",
                    fontSize: 12,
                    color: "var(--text-secondary)",
                    textAlign: "left",
                  }}
                >
                  <span style={{ fontSize: 11 }}>{thinkingOpen ? "▾" : "▸"}</span>
                  {thinkingOpen ? "收起推理过程" : "查看推理过程"}
                  {!thinkingOpen && (
                    <span style={{ color: "var(--text-tertiary)", marginLeft: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {thinkingText.slice(0, 60)}
                    </span>
                  )}
                </button>
                {thinkingOpen && (
                  <pre
                    style={{
                      margin: 0,
                      padding: "8px 12px",
                      whiteSpace: "pre-wrap",
                      wordBreak: "break-word",
                      fontSize: 12,
                      color: "var(--text-secondary)",
                      maxHeight: 260,
                      overflowY: "auto",
                    }}
                  >
                    {thinkingText || "（无推理内容）"}
                  </pre>
                )}
              </div>
            )}

            {/* 2026-08-19(后端进程反馈): status 事件 —— LLM 调用中/工具执行中。
                后端驱动(工具心跳每 10s 覆盖 status), 实时展示"正在干什么",
                消除大上下文 prefill / 长工具执行期间的无感知等待。 */}
            {statusEv && (() => {
              const stage = String(statusEv.payload?.stage ?? "");
              if (stage === "llm_calling") {
                return (
                  <div style={{ fontSize: 12, color: "var(--text-tertiary)", margin: "4px 0" }}>
                    🧠 {String(statusEv.payload?.message ?? "模型调用中")}
                  </div>
                );
              }
              if (stage === "tool_running") {
                return (
                  <div style={{ fontSize: 12, color: "var(--warning-text)", margin: "4px 0" }}>
                    ⏳ 工具 {String(statusEv.payload?.tool ?? "")} 执行中 · 已{" "}
                    {String(statusEv.payload?.elapsed_sec ?? 0)}s
                  </div>
                );
              }
              return null;
            })()}

            {toolEvents.length > 0 && (() => {
              // 2026-08-19(工具执行无反馈): 判定"执行中"的 tool_call ——
              // 生成中且 tool_call 数 > tool_result 数(最后一个 call 无结果)。
              // 对该 call 显示 ToolPendingTimer 实时耗时; 历史已结束的轮
              // (非生成中)或结果已返回的不显示。
              const calls = toolEvents.filter((e) => e.event_type === "tool_call");
              const results = toolEvents.filter((e) => e.event_type === "tool_result");
              const pendingCall =
                isGenerating && calls.length > results.length
                  ? calls[calls.length - 1]
                  : undefined;
              return toolEvents.map((te) => {
                const text = formatPayload(te.event_type, te.payload);
                const imagePaths =
                  te.event_type === "tool_result" ? extractImagePaths(text) : [];
                const durationMs =
                  te.event_type === "tool_result"
                    ? (te.payload.duration_ms as number | undefined)
                    : undefined;
                const isPendingThis = pendingCall !== undefined && te.id === pendingCall.id;
                return (
                  <div key={te.id} style={{ marginBottom: 6 }}>
                    <div
                      style={{
                        backgroundColor:
                          te.event_type === "tool_call" ? "var(--tool-call-bg)" : "var(--tool-result-bg)",
                        borderRadius: 10,
                        padding: "6px 10px",
                        fontSize: 12,
                        color: "var(--text-secondary)",
                      }}
                    >
                      {te.event_type === "tool_call" ? (
                        <>
                          🔧 {text}
                          {isPendingThis && <ToolPendingTimer startedAt={te.ts} />}
                        </>
                      ) : (
                        <>✅ {text.slice(0, 120)}{text.length > 120 ? "…" : ""}
                          {durationMs != null ? ` · ${durationMs}ms` : ""}
                        </>
                      )}
                    </div>
                    {imagePaths.length > 0 && (
                      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 6 }}>
                        {imagePaths.map((p) => (
                          <img
                            key={p}
                            src={imagePathToUrl(p)}
                            alt={p}
                            style={{ maxWidth: "100%", borderRadius: 10, border: "1px solid var(--border-color)" }}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                );
              });
            })()}

            {/* V2 P1: 沙箱终端流式输出 */}
            {sandboxText && (
              <div
                style={{
                  marginBottom: 8,
                  border: "1px solid var(--border-color)",
                  borderRadius: 10,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    padding: "4px 10px",
                    background: "var(--code-bg)",
                    fontSize: 11,
                    fontWeight: 600,
                    color: "var(--text-tertiary)",
                    borderBottom: "1px solid var(--border-color)",
                  }}
                >
                  🖥️ 沙箱输出
                </div>
                <pre
                  style={{
                    margin: 0,
                    padding: "8px 12px",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    fontFamily: "Consolas, 'Courier New', monospace",
                    fontSize: 12,
                    color: "var(--text-primary)",
                    maxHeight: 260,
                    overflowY: "auto",
                  }}
                >
                  {sandboxText}
                </pre>
              </div>
            )}

            {/* V2 P1 + 阶段三批次1(B-8): 权限确认卡片(风险分级 + 来源解释) */}
            {confirmEvents.length > 0 &&
              confirmEvents.map((ce) => {
                const args = ce.payload.args_summary as Record<string, unknown> | undefined;
                const argsPreview = args ? JSON.stringify(args).slice(0, 200) : "";
                const risk = (ce.payload.risk_level as string) || "medium";
                const reason = (ce.payload.reason as string) || "";
                const display = ce.payload.display as
                  | { title?: string; summary?: string[] }
                  | undefined;
                const riskColor =
                  risk === "high" ? "#dc2626" : risk === "medium" ? "#d97706" : "#16a34a";
                const riskLabel =
                  risk === "high" ? "高风险" : risk === "medium" ? "中风险" : "低风险";
                return (
                  <div
                    key={ce.id}
                    style={{
                      marginBottom: 8,
                      border: "1px solid #fcd34d",
                      borderRadius: 10,
                      background: "var(--confirmation-bg)",
                      padding: "10px 12px",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                      <span style={{ fontSize: 12, fontWeight: 600, color: "#92400e" }}>
                        ⚠️ {display?.title ?? formatPayload("tool_confirmation_required", ce.payload)}
                      </span>
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          color: "#fff",
                          background: riskColor,
                          borderRadius: 10,
                          padding: "1px 8px",
                        }}
                      >
                        {riskLabel}
                      </span>
                    </div>
                    {display?.summary && display.summary.length > 0 && (
                      <div style={{ fontSize: 11, color: "#92400e", marginBottom: 4, lineHeight: 1.7 }}>
                        {display.summary.map((line, i) => (
                          <div key={i}>· {line}</div>
                        ))}
                      </div>
                    )}
                    {reason && (
                      <div style={{ fontSize: 11, color: "#78350f", marginBottom: 4 }}>
                        原因: {reason}
                      </div>
                    )}
                    {argsPreview && (
                      <details style={{ margin: "0 0 8px", fontSize: 11 }}>
                        <summary style={{ cursor: "pointer", color: "var(--warning-text)" }}>
                          技术详情(原始参数)
                        </summary>
                        <pre
                          style={{
                            margin: "6px 0 0",
                            whiteSpace: "pre-wrap",
                            wordBreak: "break-all",
                            fontSize: 11,
                            color: "var(--warning-text)",
                            fontFamily: "Consolas, monospace",
                          }}
                        >
                          {argsPreview}
                        </pre>
                      </details>
                    )}
                    {confirmResultEv ? (
                      <div className="fs-12 text-secondary">
                        {formatPayload("tool_confirmation_result", confirmResultEv.payload)}
                      </div>
                    ) : expiredConfirmIds.has(String(ce.payload.confirmation_id)) ? (
                      // 2026-08-18(请求卡片点不了): 已过期/已处理 —— 明确
                      // 展示状态而非可点按钮(原实现卡片残留可点, 点击报
                      // unknown confirmation_id 且无定位)
                      <div
                        className="fs-12"
                        style={{
                          color: "var(--text-tertiary)",
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                        }}
                      >
                        <span>⏳ 该确认已过期或已处理</span>
                        <span
                          style={{
                            fontSize: 10,
                            padding: "1px 6px",
                            borderRadius: 8,
                            background: "var(--panel-bg-hover)",
                            color: "var(--text-tertiary)",
                          }}
                        >
                          不可再操作
                        </span>
                      </div>
                    ) : (
                      <div style={{ display: "flex", gap: 8 }}>
                        <button
                          onClick={() => onConfirmAction(String(ce.payload.confirmation_id), true)}
                          style={{ background: "#16a34a", color: "#fff", border: "none", borderRadius: 10, padding: "4px 14px", fontSize: 12, cursor: "pointer" }}
                        >
                          同意执行
                        </button>
                        <button
                          onClick={() => onConfirmAction(String(ce.payload.confirmation_id), false)}
                          style={{ background: "#dc2626", color: "#fff", border: "none", borderRadius: 10, padding: "4px 14px", fontSize: 12, cursor: "pointer" }}
                        >
                          拒绝
                        </button>
                        {/* 阶段三批次4(B-14): 稍后决定(挂起确认, 不立即拒绝) */}
                        <button
                          onClick={() => onDeferAction(String(ce.payload.confirmation_id))}
                          title="60 秒后不自动拒绝, 挂起等待后续决定"
                          style={{ background: "#6d28d9", color: "#fff", border: "none", borderRadius: 10, padding: "4px 14px", fontSize: 12, cursor: "pointer" }}
                        >
                          稍后决定
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}

            {finalText && !deleted ? (
              <div
                style={{
                  backgroundColor: "var(--chat-ai-bg)",
                  border: "1px solid var(--chat-ai-border)",
                  borderRadius: 12,
                  padding: "10px 14px",
                  color: "var(--text-primary)",
                }}
              >
                {renderFinalText(finalText)}
                {/* V1.1-3.3 消息操作条(非生成中显示) */}
                {!isGenerating && (
                  <div style={{ display: "flex", gap: 4, marginTop: 6, marginLeft: 0, flexWrap: "wrap" }}>
                    <MsgActionBtn label="🔄 重生成" title="重新生成这条回复" onClick={onRegenerate} />
                    <MsgActionBtn
                      label={starred ? "★ 已收藏" : "☆ 收藏"}
                      title="收藏/取消收藏"
                      onClick={onToggleStar}
                    />
                    <MsgActionBtn label="🗑 删除" title="软删除这条回复" danger onClick={onRequestDelete} />
                    <MsgActionBtn label="📋 复制" title="复制回复内容" onClick={() => onCopy(finalText)} />
                    {finalText.includes("```") && (
                      <MsgActionBtn label="📄 复制代码" title="提取代码块并复制" onClick={() => onCopyCode(finalText)} />
                    )}
                  </div>
                )}
                {/* 2026-08-19: 任务完成时间 + 总耗时(turn_end 事件记录) ——
                    四场景通用, 长任务(全量回归等)结束后一眼看出该轮耗时 */}
                {completedAt && (
                  <div
                    className="fs-12"
                    style={{
                      color: "var(--text-tertiary)",
                      marginTop: 6,
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      flexWrap: "wrap",
                    }}
                  >
                    <span>✓ 完成于</span>
                    <span style={{ fontFamily: "Consolas, monospace" }}>
                      {new Date(completedAt).toLocaleTimeString("zh-CN", {
                        hour12: false,
                      })}
                    </span>
                    {/* 2026-08-19(A 方案): 总耗时优先后端权威 duration_ms
                        (turn_end 携带, monotonic 计时, 不受窗口/turn 号/前端
                        时钟污染); 旧后端无字段时回退前端差值 */}
                    {(durationMs != null && durationMs >= 0) ||
                    (startedAt && completedAt >= startedAt) ? (
                      <span
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                        }}
                      >
                        <span style={{ opacity: 0.6 }}>·</span>
                        <span>⏱ 总耗时</span>
                        <span style={{ fontFamily: "Consolas, monospace" }}>
                          {durationMs != null && durationMs >= 0
                            ? formatDuration(durationMs)
                            : startedAt && completedAt >= startedAt
                              ? formatDuration(completedAt - startedAt)
                              : ""}
                        </span>
                      </span>
                    ) : null}
                  </div>
                )}
              </div>
            ) : finalText && deleted ? (
              <div style={{ fontSize: 12, color: "var(--text-tertiary)", fontStyle: "italic" }}>
                此回复已删除
              </div>
            ) : null}

            {errorEv && (
              <div
                style={{
                  backgroundColor: "var(--error-bg)",
                  borderRadius: 10,
                  padding: "8px 12px",
                  fontSize: 13,
                  color: errorCategoryColor(errorEv.payload?.message) ?? "var(--danger-text)",
                }}
              >
                ❌ {formatPayload("error", errorEv.payload)}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
