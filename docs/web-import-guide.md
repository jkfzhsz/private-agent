# 网页入库指南（V1.5 项-7）

> 目标：把网页内容纳入知识库供对话检索。PA **不内置网页抓取器**（架构约定：
> 外部能力统一走 MCP），按"引导粘贴 → MCP 抓取 → 内置抓取(可选)"三条路径收敛。

## 路径一：粘贴正文入库（推荐，零依赖）

知识库页面（知识库 → 上传文档）已内置该入口：

1. 浏览器打开目标网页
2. `Ctrl+A` / `Cmd+A` 全选 → `Ctrl+C` 复制正文
3. 粘贴到知识库上传区的文本框
4. 填写文件名（如 `2026-08-05-web-article.md`），可选指定目标知识库（scenario）
5. 点击"上传到知识库" → 自动切片 + 向量化

底层接口（后端已存在，无需改动）：

```
POST /admin/knowledge/upload?session_id={sid}&filename={name}&content={正文}&scenario={可选}
→ {"doc_id": int, "chunks": int}
```

**适用场景**：一次性手动归档 1~10 个网页；无需任何外部工具；正文量大时注意
浏览器复制会带样式，粘贴后建议目检一遍 Markdown 结构。

## 路径二：MCP 检索工具抓取 → Agent 自动入库（半自动）

若已配置带网页检索能力的 MCP server（如 iFind 资讯、企查查公告、博查搜索等），
可直接在对话中让 Agent 完成"抓取 → 入库"闭环：

**对话指令示例**：

```
用 iFind 搜索今天的宏观经济新闻，把关于 CPI 的 3 篇资讯正文抓取下来，
然后逐篇入库到知识库(scenario=cpi)，文件名用新闻标题。
```

**Agent 执行链路**：

1. 调 MCP 工具（如 `mcp__iFind__search_news`）检索并获取正文/摘要
2. 对每篇内容调用知识库入库接口（见下方示例），即可被后续对话检索

**知识库入库接口**（HTTP，Agent 可经内置 `http_request` 工具调用）：

```
POST http://127.0.0.1:8765/admin/knowledge/upload?session_id={sid}&filename={title}.md&scenario={库名}
Body 参数: content = 网页正文(纯文本/Markdown)
```

> 提示：Agent 调用 http_request 需网络权限；也可直接由你在知识库页面粘贴。

**MCP 配置参考**（`docs/mcp-templates.md` 登记了常用检索类模板）。

## 路径三：内置抓取器（未实现，设计评估）

`POST /admin/knowledge/fetch_url`（httpx + readability 提取正文 → markdown →
入库）曾在 V1.5 规划中评估，结论：

- 优点：一键 URL 入库，体验最好
- 代价：SSRF 风险（需域名白名单）、反爬/超时处理、正文提取质量不可控、
  与"外部服务统一走 MCP"的架构原则冲突（2026-07 决策）
- 决策：**暂不内置**。若后续需求强烈，优先以"URL 模板 + MCP 浏览器抓取
  工具"方式实现，而非自研抓取器

## 常见问题

| 问题 | 处理 |
|---|---|
| 网页正文太长粘贴卡顿 | 分段粘贴，或只复制正文主体（跳过导航/广告） |
| 想按来源分类检索 | 使用 scenario 参数按主题分组（如 `cpi`、`竞品`） |
| 网页是 PDF/动态渲染 | 先在浏览器导出为文本/Markdown 再粘贴 |
| 抓取结果入库后检索不到 | 等待向量化完成；检查 scenario 是否与查询一致 |
