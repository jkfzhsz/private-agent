/**
 * 一次性重构脚本: 把组件内联样式里硬编码的浅色字面量替换为语义 CSS 变量。
 *
 * 背景: 暗色主题下大量组件仍显示亮色面板/白底气泡, 根因是 React 内联样式
 * 硬编码 #fff / rgba(255,255,255,*) 等, 且无法用属性选择器覆盖(React 通过
 * JS 赋值 style, 浏览器序列化后与源码字面量不匹配)。
 *
 * 策略: 按行判断该行属于哪类样式属性(背景 / 边框 / 文字), 再用对应映射表
 * 替换该行中的目标颜色字面量, 避免误伤品牌渐变色与状态徽章色。
 *
 * 用法: node scripts/theme-tokenize.mjs [--write]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { globSync } from "node:fs";
import path from "node:path";

const WRITE = process.argv.includes("--write");
const ROOT = path.resolve(process.cwd(), "renderer");

const FILES = [
  "App.tsx",
  "views/SettingsView.tsx",
  "views/MemoryView.tsx",
  "views/KnowledgeView.tsx",
  "views/AgentLibraryView.tsx",
  "views/HomeView.tsx",
  "components/SubagentPanel.tsx",
  "components/Sidebar.tsx",
  "components/FilePanel.tsx",
  "components/ArtifactPanel.tsx",
];

/** 背景类颜色 → 语义变量
 * 注意: #fff/#ffffff 只匹配 background/backgroundColor 属性上下文, 否则会误伤
 * 同一行内 color:"#fff"(彩色按钮上的白字, 应保留为 --on-accent)。 */
const BG = [
  [/(background(?:Color)?\s*:\s*)rgba\(255,\s*255,\s*255,\s*0\.(8|85|9|95)\)/g, '$1var(--panel-bg-hover)'],
  [/(background(?:Color)?\s*:\s*)rgba\(255,\s*255,\s*255,\s*0\.[0-7]\d*\)/g, '$1var(--panel-bg)'],
  [/(background(?:Color)?\s*:\s*)"#ffffff"/g, '$1"var(--panel-bg-solid)"'],
  [/(background(?:Color)?\s*:\s*)"#fff"/g, '$1"var(--panel-bg-solid)"'],
  [/"#f9fafb"/g, '"var(--surface-1)"'],
  [/"#fafafa"/g, '"var(--surface-1)"'],
  [/"#f3f4f6"/g, '"var(--surface-2)"'],
  [/"#f5f5f5"/g, '"var(--surface-2)"'],
  [/"#f1f5f9"/g, '"var(--surface-2)"'],
  [/"#f8fafc"/g, '"var(--code-bg)"'],
  [/"#f5f3ff"/g, '"var(--accent-soft-bg)"'],
  [/"#faf5ff"/g, '"var(--accent-soft-bg)"'],
  [/"#eef2ff"/g, '"var(--tool-call-bg)"'],
  [/"#ecfdf5"/g, '"var(--tool-result-bg)"'],
  [/"#dbeafe"/g, '"var(--chat-user-bg)"'],
  [/"#fffbeb"/g, '"var(--confirmation-bg)"'],
  [/"#fef3c7"/g, '"var(--warning-bg)"'],
  [/"#ffebee"/g, '"var(--error-bg)"'],
  [/"#fee2e2"/g, '"var(--error-bg)"'],
  [/"#d1fae5"/g, '"var(--success-bg)"'],
  [/"#e8f5e9"/g, '"var(--success-bg)"'],
  [/"#e8eaf6"/g, '"var(--tool-call-bg)"'],
];

/** 边框类颜色 → 语义变量(含复合值 "1px solid #ddd") */
const BORDER = [
  [/#e5e7eb\b/g, "var(--border-color)"],
  [/#e2e8f0\b/g, "var(--border-color)"],
  [/#eeeeee\b/g, "var(--border-color)"],
  [/#ddd\b/g, "var(--border-strong)"],
  [/#eee\b/g, "var(--border-color)"],
  [/#ccc\b/g, "var(--border-strong)"],
  [/rgba\(255,\s*255,\s*255,\s*0\.\d+\)/g, "var(--border-color)"],
];

/** 文字类颜色 → 语义变量(仅深灰文字, 品牌色/状态色保留) */
const TEXT = [
  [/"#111827"/g, '"var(--text-primary)"'],
  [/"#1f2937"/g, '"var(--text-primary)"'],
  [/"#374151"/g, '"var(--text-primary)"'],
  [/"#333333"/g, '"var(--text-primary)"'],
  [/"#333"/g, '"var(--text-primary)"'],
  [/"#4b5563"/g, '"var(--text-secondary)"'],
  [/"#6b7280"/g, '"var(--text-secondary)"'],
  [/"#666666"/g, '"var(--text-secondary)"'],
  [/"#666"/g, '"var(--text-secondary)"'],
  [/"#9ca3af"/g, '"var(--text-tertiary)"'],
  [/"#999999"/g, '"var(--text-tertiary)"'],
  [/"#999"/g, '"var(--text-tertiary)"'],
];

const isBgLine = (l) => /\b(background|backgroundColor)\s*:/.test(l);
const isBorderLine = (l) =>
  /\b(border|borderColor|borderTop|borderBottom|borderLeft|borderRight|outline)\s*:/.test(
    l,
  );
const isTextLine = (l) => /\bcolor\s*:/.test(l) && !/backgroundColor\s*:/.test(l);

let total = 0;
const samples = [];

for (const rel of FILES) {
  const abs = path.join(ROOT, rel);
  let src;
  try {
    src = readFileSync(abs, "utf8");
  } catch {
    continue;
  }
  const lines = src.split("\n");
  let changed = 0;
  const out = lines.map((line, i) => {
    let next = line;
    if (isBgLine(next)) for (const [re, to] of BG) next = next.replace(re, to);
    if (isBorderLine(next))
      for (const [re, to] of BORDER) next = next.replace(re, to);
    if (isTextLine(next)) for (const [re, to] of TEXT) next = next.replace(re, to);
    if (next !== line) {
      changed++;
      if (samples.length < 25)
        samples.push(`${rel}:${i + 1}\n  - ${line.trim()}\n  + ${next.trim()}`);
    }
    return next;
  });
  if (changed > 0) {
    total += changed;
    console.log(`${String(changed).padStart(3)}  ${rel}`);
    if (WRITE) writeFileSync(abs, out.join("\n"), "utf8");
  }
}

console.log(`\n共 ${total} 行${WRITE ? "已写入" : "待修改(dry-run)"}\n`);
console.log(samples.join("\n"));
