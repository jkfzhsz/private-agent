// P0-4/P1-1(2026-08-17): AI 回复结构化渲染 —— 表格/代码块/标题/列表 受控渲染
// 审计 I1: deAIfy 全量剥离导致表格变冒号句、代码块无样式、标题层级消失。
// 本模块: 表格块 → 真实 <table>; 代码块 → 等宽 + 语言标签 + 复制按钮;
// 标题/加粗/列表 → 语义标签; 其余文本维持 deAIfy 纯文本语义(保留"去 AI 味"初衷)。
// 特性开关: localStorage["pa:rich-render"] === "0" → 回退纯文本(线上秒级降级)。
import { deAIfy } from "./deAIfy";

/** P1-1: 特性开关 —— 关闭则回退纯文本渲染(与 P0 前行为一致) */
export function richRenderEnabled(): boolean {
  try {
    return localStorage.getItem("pa:rich-render") !== "0";
  } catch {
    return true;
  }
}

function isTableRow(line: string): boolean {
  const t = line.trim();
  if (!t.includes("|")) return false;
  return /^\|?.*\|?$/.test(t);
}

function parseTable(lines: string[]): { header: string[]; rows: string[][] } | null {
  const cellsOf = (line: string): string[] => {
    const t = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    return t.split("|").map((s) => s.trim());
  };
  const dataLines = lines.filter((l) => !/^\s*\|?[\s:|-]+\|?\s*$/.test(l));
  if (dataLines.length === 0) return null;
  const header = cellsOf(dataLines[0]);
  if (header.length < 2) return null;
  const rows = dataLines.slice(1).map(cellsOf).filter((r) => r.length >= 2);
  return { header, rows };
}

type Block = { kind: "table" | "code" | "text"; lines: string[] };

/** 按段落切分: 表格块(| 连续行) / 代码块(``` 围栏) / 文本段 */
export function segmentBlocks(text: string): Block[] {
  const lines = text.split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fenceMatch = line.match(/^\s*(```+|~~~+)\s*([a-zA-Z0-9_+-]*)\s*$/);
    if (fenceMatch) {
      const fence = fenceMatch[1];
      const lang = fenceMatch[2];
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !new RegExp(`^\\s*${fence}\\s*$`).test(lines[i])) {
        codeLines.push(lines[i]);
        i += 1;
      }
      i += 1; // 跳过闭合围栏
      blocks.push({ kind: "code", lines: [lang, ...codeLines] });
      continue;
    }
    if (isTableRow(line)) {
      const tableLines: string[] = [];
      while (i < lines.length && isTableRow(lines[i])) {
        tableLines.push(lines[i]);
        i += 1;
      }
      blocks.push({ kind: "table", lines: tableLines });
      continue;
    }
    const textLines: string[] = [];
    while (
      i < lines.length &&
      !isTableRow(lines[i]) &&
      !/^\s*(```+|~~~+)\s*[a-zA-Z0-9_+-]*\s*$/.test(lines[i])
    ) {
      textLines.push(lines[i]);
      i += 1;
    }
    blocks.push({ kind: "text", lines: textLines });
  }
  return blocks;
}

/** 行内加粗提取(在 deAIfy 前, 保留语义); 其余文本段走 deAIfy 去 AI 味 */
function renderInline(raw: string, keyBase: number): (string | JSX.Element)[] {
  const parts: (string | JSX.Element)[] = [];
  const re = /\*\*([^*]+)\*\*/g;
  let m: RegExpExecArray | null;
  let last = 0;
  let k = 0;
  while ((m = re.exec(raw)) !== null) {
    if (m.index > last) parts.push(deAIfy(raw.slice(last, m.index)));
    parts.push(
      <strong key={`${keyBase}-b${k++}`} style={{ fontWeight: 600 }}>
        {m[1]}
      </strong>
    );
    last = m.index + m[0].length;
  }
  if (last < raw.length) parts.push(deAIfy(raw.slice(last)));
  return parts;
}

/** 渲染一个文本段(处理标题/无序列表/加粗) */
function renderTextSegment(lines: string[], keyBase: number): JSX.Element[] {
  const out: JSX.Element[] = [];
  let para: string[] = [];
  const flushPara = (k: number): void => {
    if (para.length === 0) return;
    const raw = para.join("\n");
    const parts = renderInline(raw, k);
    const hasContent = parts.some((p) => (typeof p === "string" ? p.trim() : true));
    if (hasContent) {
      out.push(
        <div
          key={k}
          style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 13, lineHeight: 1.6 }}
        >
          {parts}
        </div>
      );
    }
    para = [];
  };
  lines.forEach((rawLine, idx) => {
    const line = rawLine.trimEnd();
    // 标题(###/##/#) → 加粗大字级(保留去 AI 味: 只做层级不做装饰)
    const hMatch = line.match(/^\s{0,4}(#{1,6})\s+(.*)$/);
    if (hMatch) {
      flushPara(idx * 2);
      const level = Math.min(hMatch[1].length, 3);
      const size = level === 1 ? 16 : level === 2 ? 14.5 : 13.5;
      out.push(
        <div
          key={idx * 2 + 1}
          style={{
            fontSize: size,
            fontWeight: 700,
            letterSpacing: "-0.01em",
            margin: "4px 0",
            color: "var(--text-primary)",
          }}
        >
          {renderInline(hMatch[2], idx)}
        </div>
      );
      return;
    }
    // 无序列表 → 带符号行(语义列表)
    const liMatch = line.match(/^\s{0,3}[-*•]\s+(.*)$/);
    if (liMatch) {
      flushPara(idx * 2);
      out.push(
        <div
          key={idx * 2 + 1}
          style={{ display: "flex", gap: 6, fontSize: 13, lineHeight: 1.6, margin: "1px 0" }}
        >
          <span style={{ color: "var(--text-secondary)", flexShrink: 0 }}>•</span>
          <span style={{ flex: 1, minWidth: 0, wordBreak: "break-word" }}>
            {renderInline(liMatch[1], idx)}
          </span>
        </div>
      );
      return;
    }
    para.push(line);
  });
  flushPara(lines.length * 2 + 1);
  return out;
}

/** 渲染代码块(等宽 + 语言标签 + 复制按钮) */
function renderCodeBlock(block: Block, keyBase: number): JSX.Element {
  const [lang, ...codeLines] = block.lines;
  const code = codeLines.join("\n");
  return (
    <div
      key={keyBase}
      style={{
        margin: "6px 0",
        borderRadius: 10,
        overflow: "hidden",
        border: "1px solid var(--border-color)",
        background: "var(--code-bg)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "4px 10px",
          fontSize: 11,
          color: "var(--text-tertiary)",
          borderBottom: "1px solid var(--border-color)",
          background: "var(--surface-1)",
        }}
      >
        <span style={{ fontFamily: "var(--font-mono)" }}>{lang || "code"}</span>
        <button
          onClick={() => {
            void navigator.clipboard.writeText(code).catch(() => {});
          }}
          title="复制代码"
          style={{
            fontSize: 11,
            border: "none",
            background: "transparent",
            color: "var(--text-secondary)",
            cursor: "pointer",
            padding: "2px 6px",
            borderRadius: 6,
          }}
        >
          📋 复制
        </button>
      </div>
      <pre
        style={{
          margin: 0,
          padding: "8px 12px",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          fontSize: 12,
          fontFamily: "var(--font-mono)",
          lineHeight: 1.6,
          color: "var(--text-primary)",
        }}
      >
        {code}
      </pre>
    </div>
  );
}

/** 渲染 AI 回复(表格/代码/标题/列表 结构化, 其余 deAIfy 纯文本) */
export function renderFinalText(text: string): JSX.Element[] {
  if (!richRenderEnabled()) {
    const cleaned = deAIfy(text);
    return [
      <pre
        key="plain"
        style={{
          margin: 0,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          fontSize: 13,
          fontFamily: "inherit",
          lineHeight: 1.6,
          // 2026-08-17(实机修复): 显式透明, 免疫全局 pre 背景规则
          backgroundColor: "transparent",
        }}
      >
        {cleaned}
      </pre>,
    ];
  }
  const blocks = segmentBlocks(text);
  const out: JSX.Element[] = [];
  let textBuf: string[] = [];
  const flushText = (key: number): void => {
    if (textBuf.length === 0) return;
    const raw = textBuf.join("\n");
    if (raw.trim()) out.push(...renderTextSegment([raw], key));
    textBuf = [];
  };
  blocks.forEach((b, idx) => {
    if (b.kind === "text") {
      // 行级处理(标题/列表)需要逐行, 直接进 renderTextSegment 按段渲染
      out.push(...renderTextSegment(b.lines, idx * 10));
      return;
    }
    if (b.kind === "code") {
      flushText(idx * 10 + 5);
      out.push(renderCodeBlock(b, idx * 10 + 6));
      return;
    }
    // table
    flushText(idx * 10 + 7);
    const parsed = parseTable(b.lines);
    if (!parsed || parsed.rows.length === 0) {
      out.push(...renderTextSegment(b.lines, idx * 10 + 8));
      return;
    }
    out.push(
      <div
        key={idx * 10 + 9}
        style={{
          margin: "6px 0",
          overflowX: "auto",
          borderRadius: 10,
          border: "1px solid var(--border-color)",
        }}
      >
        <table
          style={{
            borderCollapse: "collapse",
            width: "100%",
            fontSize: 12,
            lineHeight: 1.5,
            color: "var(--text-primary)",
          }}
        >
          <thead>
            <tr>
              {parsed.header.map((h, hi) => (
                <th
                  key={hi}
                  style={{
                    textAlign: "left",
                    padding: "6px 10px",
                    background: "var(--surface-1)",
                    borderBottom: "1px solid var(--border-color)",
                    fontWeight: 600,
                    whiteSpace: "nowrap",
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {parsed.rows.map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => (
                  <td
                    key={ci}
                    style={{
                      padding: "6px 10px",
                      borderBottom: "1px solid var(--border-color)",
                      background: ri % 2 === 1 ? "var(--surface-1)" : "transparent",
                      wordBreak: "break-word",
                    }}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  });
  flushText(blocks.length * 10 + 20);
  return out;
}
