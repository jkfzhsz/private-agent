# prompts.chat Prompt 库使用指南

你了解 prompts.chat MCP 服务，知道何时以及如何调用它来获取高质量 Prompt 模板辅助写作。

## 关于 prompts.chat

prompts.chat 是全球最大的开源 Prompt 库（164k+ GitHub Stars），收录了 1500+ 经过社区验证的高质量 Prompt 模板，覆盖写作、编程、教育、商业、创意等各个领域。

作为  的 MCP 服务，它提供了 `listPrompts`（浏览）和 `getPrompt`（获取详情）两个核心能力。

## 何时应该调用 prompts.chat

### ✅ 应该调用的场景

| 场景 | 说明 |
|------|------|
| 用户需要特定角色的 Prompt | 如「用一个小说家的视角帮我写…」「像编辑一样给我反馈」 |
| 写作前需要结构化模板 | 如「我要写一个商业计划书」「帮我生成一个文章大纲模板」 |
| 需要灵感启发 | 如「给我一些故事创意 Prompt」「有什么好的头脑风暴方法」 |
| 用户明确要求 | 「帮我从 prompts.chat 找一个…」 |

### ❌ 不建议调用的场景

| 场景 | 原因 |
|------|------|
| 简单的写作请求 | 直接写就行，不需要绕一圈查 Prompt 库 |
| 已经在本技能库覆盖的 | novelist / article-writer 等自有技能已足够 |
| 纯技术编码任务 | prompts.chat 偏向文本创作，编程用自带工具更好 |

## 如何使用

### 浏览可用 Prompt
调用 prompts.chat MCP 的 `listPrompts` 可以浏览所有可用的 Prompt 模板。

### 获取具体 Prompt
调用 prompts.chat MCP 的 `getPrompt` 获取某个 Prompt 的完整内容，然后基于这个 Prompt 指导完成用户的写作任务。

## 写作相关的重点 Prompt 类别

以下是 prompts.chat 中与写作最相关的 Prompt 类别：

| 类别 | 代表 Prompt | 用途 |
|------|-----------|------|
| **创意写作** | Novelist, Screenwriter, Storyteller, Poet | 小说/剧本/故事/诗歌 |
| **内容创作** | Blog Post Writer, Content Strategist, Copywriter | 博客/营销文案/内容策略 |
| **编辑润色** | Editor in Chief, Proofreader, AI Writing Tutor | 编辑审校/校对/写作辅导 |
| **商业写作** | Business Plan Writer, Pitch Deck Creator | 商业计划/融资路演 |
| **技术写作** | Technical Writer, Documentation Writer | 技术文档/API 文档 |
| **角色扮演** | 任意角色 Prompt | 以特定人物风格写作 |

## 组合使用策略

prompts.chat 的 Prompt 可以与本地的  写作技能组合：

```
用户请求 → 判断复杂度
  ├── 简单 ──→ 直接写
  ├── 中等 ──→ 使用本地技能 (novelist / article-writer / doc-coauthor)
  └── 复杂/特定角色 ──→ 先查 prompts.chat 获取模板
                        └── 再结合本地技能执行
```

## 注意事项

- prompts.chat MCP 是远程服务，调用有网络延迟，不要滥用
- 获取的 Prompt 是英文为主，需要根据用户需求翻译和本地化
- 社区 Prompt 是灵感模板，不是教条——根据实际情况调整
- 一次对话中查 1-2 个 Prompt 即可，不要反复查询