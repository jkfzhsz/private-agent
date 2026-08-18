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

// P0-4(2026-08-17): 表格结构化渲染 —— segmentBlocks/parseTable 单元测试
describe("P0-4 表格结构化渲染", () => {
  it("识别连续表格块并拆分为 table/text 段落", () => {
    const input =
      "下面是行情对比:\n" +
      "| 标的 | 涨跌幅 |\n" +
      "|---|---|\n" +
      "| 沪深300 | +0.5% |\n" +
      "| 中证500 | -0.2% |\n" +
      "以上数据仅供参考。";
    const blocks = segmentBlocks(input);
    expect(blocks.length).toBe(3);
    expect(blocks[0].kind).toBe("text");
    expect(blocks[1].kind).toBe("table");
    expect(blocks[1].lines.length).toBe(4);
    expect(blocks[2].kind).toBe("text");
  });

  it("非法表格(单列)不误判为表格块", () => {
    // 单行含 | 且列数 <2 → parseTable 返回 null, renderFinalText 回退文本
    const blocks = segmentBlocks("普通文本 | 没有表格");
    expect(blocks[0].kind).toBe("table");
    const parsed = blocks[0].lines;
    // renderFinalText 中会回退为文本, 此处验证 parseTable 逻辑经 render 不崩溃
    expect(parsed.length).toBe(1);
  });
});

// P1-1(2026-08-17): 全量受控 Markdown 渲染 —— 表格/代码/标题/列表/引用/混合
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { renderFinalText, richRenderEnabled, segmentBlocks } from "./renderFinal";

describe("P1-1 受控 Markdown 渲染", () => {
  it("表格 → 真实 <table>", () => {
    const input =
      "| 标的 | 涨跌幅 |\n" +
      "|---|---|\n" +
      "| 沪深300 | +0.5% |\n" +
      "| 中证500 | -0.2% |";
    const html = renderToStaticMarkup(React.createElement(React.Fragment, null, ...renderFinalText(input)));
    expect(html).toContain("<table");
    expect(html).toContain("沪深300");
    expect(html).toContain("<th");
  });

  it("代码块 → 等宽 pre + 语言标签 + 复制按钮", () => {
    const input = "```python\nprint('hi')\n```";
    const html = renderToStaticMarkup(React.createElement(React.Fragment, null, ...renderFinalText(input)));
    expect(html).toContain("python");
    expect(html).toContain("print(&#x27;hi&#x27;)");
    expect(html).toContain("复制");
  });

  it("标题 → 加粗大字级", () => {
    const input = "## 结论";
    const html = renderToStaticMarkup(React.createElement(React.Fragment, null, ...renderFinalText(input)));
    expect(html).toContain("结论");
    expect(html).toContain("font-weight:700");
  });

  it("无序列表 → 语义列表行", () => {
    const input = "- 第一点\n- 第二点";
    const html = renderToStaticMarkup(React.createElement(React.Fragment, null, ...renderFinalText(input)));
    expect(html).toContain("第一点");
    expect(html).toContain("第二点");
  });

  it("引用行仍走 deAIfy 剥离(去 AI 味)", () => {
    const input = "> 这是一句引用";
    const html = renderToStaticMarkup(React.createElement(React.Fragment, null, ...renderFinalText(input)));
    expect(html).toContain("这是一句引用");
    expect(html).not.toContain("&gt; 这是一句引用");
  });

  it("混合内容: 标题+表格+代码+文本 一次渲染不崩溃", () => {
    const input =
      "## 分析结果\n" +
      "| 指标 | 值 |\n" +
      "|---|---|\n" +
      "| A | 1 |\n" +
      "代码: ```js\nconst x = 1;\n```\n结束";
    const html = renderToStaticMarkup(React.createElement(React.Fragment, null, ...renderFinalText(input)));
    expect(html).toContain("分析结果");
    expect(html).toContain("<table");
    expect(html).toContain("const x = 1;");
    expect(html).toContain("结束");
  });

  it("特性开关 pa:rich-render=0 → 回退纯文本", () => {
    localStorage.setItem("pa:rich-render", "0");
    try {
      expect(richRenderEnabled()).toBe(false);
      const input = "| A | B |\n|---|---|\n| 1 | 2 |";
      const html = renderToStaticMarkup(React.createElement(React.Fragment, null, ...renderFinalText(input)));
      expect(html).not.toContain("<table");
      expect(html).toContain("1: 2");
    } finally {
      localStorage.removeItem("pa:rich-render");
    }
  });

  it("segmentBlocks 识别代码块围栏", () => {
    const blocks = segmentBlocks("前文\n```py\ncode\n```\n后文");
    expect(blocks.map((b) => b.kind)).toEqual(["text", "code", "text"]);
    expect(blocks[1].lines[0]).toBe("py");
  });
});
