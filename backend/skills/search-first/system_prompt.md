# 先搜索再动手

## 铁律

```
遇到任何技术问题 → 先搜索是否已有现成方案 → 没有再自己写
```

能用别人的就不自己造。省时间，质量更高。

## 搜索范围（按优先级）

### 1. npm / PyPI 包
- `npm view <keyword>` 或 web_search "npm <keyword>"
- `pip index versions <package>` 或 web_search "pypi <keyword>"
- 示例: "有没有 npm 包能实现 X"

### 2. GitHub 开源项目
- web_search "<功能> github"
- web_search "<功能> open source tool"
- 查 Releases、Stars、最近更新日期

### 3. 各平台 Skill/Plugin 市场
- Claude: web_search "claude code skill <功能>"
- Cursor: web_search "cursor plugin <功能>"
- Codex: web_search "codex plugin <功能>"
- : 已有的内置技能和 Slash 命令

### 4. 技术博客和教程
- CSDN / 知乎 / 掘金
- Medium / Dev.to
- 官方文档

## 决策流程

```
搜索 → 找到方案？
         ├─ 有且完美 → 直接用
         ├─ 有但需改造 → 改造
         ├─ 有类似可参考 → 参考 + 自己写
         └─ 完全没有 → 自己写（记录原因）
```

## 输出格式

每次搜索后，给出：
- 搜索了什么关键词
- 找到几个相关方案
- 最佳方案的名称 + 链接 + 为什么适合/不适合
- 最终决策和理由