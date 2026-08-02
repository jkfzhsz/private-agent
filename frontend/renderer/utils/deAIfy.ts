// 去 AI 味: 将模型输出的 markdown 风格文本转为自然对话文本
// 处理: 标题符(#)、加粗(**)斜体(*)、行内/块级代码、列表符、引用、表格、emoji 装饰

/** 去除一段文本的 markdown 语法符号(保留自然换行与语义)。 */
export function deAIfy(text: string): string {
  if (!text) return text;
  let t = text;
  // 代码块: ```lang ... ``` → 保留内容(去掉围栏)
  t = t.replace(/```[a-zA-Z0-9_+-]*\n?([\s\S]*?)```/g, "$1");
  // 行内代码: `code` → code
  t = t.replace(/`([^`]+)`/g, "$1");
  // 标题: ### 内容 → 内容(可加粗感去掉)
  t = t.replace(/^\s{0,4}#{1,6}\s+/gm, "");
  // 加粗/斜体/删除线: **x** *x* __x__ ~~x~~ → x(保留中间内容)
  t = t.replace(/\*\*([^*]+)\*\*/g, "$1");
  t = t.replace(/__([^_]+)__/g, "$1");
  t = t.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1$2");
  t = t.replace(/~~([^~]+)~~/g, "$1");
  // 引用: > x → x
  t = t.replace(/^\s*>\s?/gm, "");
  // 无序列表: - x / * x / • x → x(转自然句)
  t = t.replace(/^\s*[-*•]\s+/gm, "");
  // 有序列表: 1. x → x
  t = t.replace(/^\s*\d+[.、)]\s+/gm, "");
  // 表格行: | a | b | 与分隔行 --- 移除
  t = t.replace(/^\s*\|.*\|\s*$/gm, "");
  t = t.replace(/^\s*\|?[\s:|-]+\|?\s*$/gm, "");
  // 超链接: [text](url) → text
  t = t.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
  // 残留的孤立符号清理(行首 -, *, | 等)
  t = t.replace(/^[|*#>\-\s]+$/gm, "");
  // 多个连续空行压缩为一个
  t = t.replace(/\n{3,}/g, "\n\n");
  // 行内多余空格(中文后空格)
  t = t.replace(/[\u4e00-\u9fa5] +(?=[\u4e00-\u9fa5])/g, (m) => m.replace(" ", ""));
  return t.trim();
}
