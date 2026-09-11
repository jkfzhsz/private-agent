import { describe, expect, it } from "vitest";

import { extractImagePaths, imagePathToUrl } from "./chatUi";

describe("extractImagePaths(2026-09-08 收紧: outputs 前缀必选)", () => {
  it("网页 HTML 中的 <img> 相对路径不再被误提取(破损图根因)", () => {
    const html = [
      '<!DOCTYPE html><html lang="zh-Hans">',
      '<img src="/assets/icon.png">',
      '<img src="icon.png">',
      '<img src="/assets/5c45-4099-8221-4ef1b2b98d9c.png">',
      '<img src="https://cdn.example.com/img/miaoda.webp">',
      '<img src="/logo/color.svg">',
      '<img src="xingchen-maas-logo.png">',
    ].join("\n");
    expect(extractImagePaths(html)).toEqual([]);
  });

  it("代码/日志文本中的裸图片文件名不再误提取", () => {
    expect(extractImagePaths('open("192.png") // config color.svg')).toEqual([]);
  });

  it("仍提取明确的 outputs 相对路径", () => {
    expect(extractImagePaths("已保存到 outputs/持仓热力图.png")).toEqual([
      "outputs/持仓热力图.png",
    ]);
    // 2026-09-11: 断言与实现对齐 —— 正则含可选前导斜杠 `(?:[\\/])?`,
    // 带斜杠的输入原样保留(与下方 Windows 绝对路径用例同一约定);
    // imagePathToUrl 取路径末段, 前导斜杠不影响最终 URL。
    expect(extractImagePaths("生成完成: /outputs/report_page_1.png")).toEqual([
      "/outputs/report_page_1.png",
    ]);
  });

  it("仍提取 Windows 绝对路径(反斜杠)", () => {
    expect(
      extractImagePaths("图已写入 D:\\PA\\wuya\\outputs\\架构图v2.png")
    ).toEqual(["D:\\PA\\wuya\\outputs\\架构图v2.png"]);
  });

  it("imagePathToUrl 兼容正斜杠与反斜杠", () => {
    expect(imagePathToUrl("outputs/a.png")).toBe(
      "http://127.0.0.1:8765/files/outputs/a.png"
    );
    expect(imagePathToUrl("D:\\PA\\wuya\\outputs\\b.png")).toBe(
      "http://127.0.0.1:8765/files/outputs/b.png"
    );
  });
});
