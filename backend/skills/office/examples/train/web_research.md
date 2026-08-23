# 示例:网页研究摘要

## 用户请求

帮我调研 2026 年主流 AI 编程助手,对比核心功能与定价。

## 助手响应

沙箱 network 关闭,采用 `web_search` 摘要模式(不抓取具体网页)。

[web_search] 查询 "2026 AI coding assistant comparison":
- 来源 1: https://example.com/ai-coding-2026
- 来源 2: https://example.com/dev-tools-review

## 调研摘要

| 产品 | 核心功能 | 定价(月) |
|------|---------|----------|
| Cursor | 代码补全 + 多模型 | $20 |
| Copilot | IDE 集成 + Chat | $10 |
| Trae | 全栈协作 + 上下文工程 | $15 |

来源:
- [AI Coding 2026 对比](https://example.com/ai-coding-2026)
- [开发工具评测](https://example.com/dev-tools-review)

注:沙箱 network 关闭,以上为搜索摘要结果,未抓取原网页。如需完整内容,请单独请求 `http_request`(需 elevated 权限确认)。
