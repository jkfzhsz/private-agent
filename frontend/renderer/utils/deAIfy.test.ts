import { describe, expect, it } from "vitest";
import { deAIfy } from "./deAIfy";

describe("deAIfy 表格处理(2026-08-08 修复)", () => {
  it("表格数据行转为自然文本, 内容不丢失", () => {
    const input =
      "## 今日新闻速览\n" +
      "### 财经 / 科技\n" +
      "| 新闻 | 要点 | 来源 |\n" +
      "|---|---|---|\n" +
      "| **宇树科技 IPO 路演** | 8/7 下午交流会 | [东方财富](https://kuaixun.eastmoney.com) |\n" +
      "| **美联储存款** | 19.363 万亿美元 | [每经](https://www.toutiao.com) |\n";
    const out = deAIfy(input);
    // 表格内容必须保留(此前整行删除 → 只有标题没内容)
    expect(out).toContain("宇树科技 IPO 路演");
    expect(out).toContain("美联储存款");
    expect(out).toContain("19.363 万亿美元");
    expect(out).toContain("东方财富");
    // 分隔行应被移除
    expect(out).not.toMatch(/^\s*\|?[\s:|-]+\|?\s*$/m);
  });

  it("表格转文本后仍有标题语义", () => {
    const input =
      "### 财经 / 科技\n" +
      "| 新闻 | 要点 |\n" +
      "|---|---|\n" +
      "| A | B 内容 |\n";
    const out = deAIfy(input);
    expect(out).toContain("财经 / 科技");
    expect(out).toContain("A: B 内容");
  });
});
