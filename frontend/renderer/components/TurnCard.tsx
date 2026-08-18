// P3-2 批次2(2026-08-17): 单轮对话卡片 —— 从 App.tsx 拆出的消息渲染单元
// App.tsx 5023 行 → 目标 ≤1500 的关键拆分: turnGroups.map 内 455 行渲染
// 移至本组件。props 用薄回调(封装 sendWs/输入编辑等副作用), 组件内只做展示。
// 注意: 类型(EventType/ReactEvent/TurnGroupData)随本文件导出, App 单向依赖。
import RobotAvatar from "./RobotAvatar";
import { ThinkingWait, MsgActionBtn, formatPayload, errorCategoryColor, extractImagePaths, imagePathToUrl } from "../utils/chatUi";
import { renderFinalText } from "../utils/renderFinal";

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
  | "iteration_limit_reached";

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
  } = g;
  // 有用户消息但还没有最终文本 → AI 正在思考
  const isPending = !!userEv && !finalText && !errorEv;

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

            {isPending && !thinkingEv && <ThinkingWait />}

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

            {toolEvents.length > 0 &&
              toolEvents.map((te) => {
                const text = formatPayload(te.event_type, te.payload);
                const imagePaths =
                  te.event_type === "tool_result" ? extractImagePaths(text) : [];
                const durationMs =
                  te.event_type === "tool_result"
                    ? (te.payload.duration_ms as number | undefined)
                    : undefined;
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
                        <>🔧 {text}</>
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
              })}

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
