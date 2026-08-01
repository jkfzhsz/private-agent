# 示例:产品落地页

## 用户请求

请帮我生成一个产品落地页 `outputs/landing.html`,包含 header(导航)、hero(主标题 + CTA)、features(三列特性)、footer 四部分,需要响应式适配移动端与桌面端,遵循设计系统主色规范。

## 助手响应

我将按 `search_knowledge` → `code_execution` → `file_write` → `file_read` 流程处理。

[search_knowledge] 检索设计系统:
```
query="按钮组件规范 颜色 token", scenario="frontend_design"
```
命中设计 token:主色 `#3B82F6`、间距单位 `8px`、圆角 `md=8px`、字体 `system-ui`。

[code_execution] 用 Python 字符串拼接生成 HTML(内联 CSS,媒体查询适配移动端):
```python
from datetime import datetime

now = datetime.now().strftime("%Y-%m-%d %H:%M")

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>产品落地页</title>
  <!-- 生成时间: {now} | 来源: frontend_design skill -->
  <style>
    :root {{
      --primary: #3B82F6;
      --spacing-unit: 8px;
      --radius-md: 8px;
      --font-stack: system-ui, -apple-system, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: var(--font-stack); color: #1F2937; }}
    header {{ display: flex; justify-content: space-between; padding: calc(var(--spacing-unit)*2); }}
    .hero {{ text-align: center; padding: calc(var(--spacing-unit)*8) var(--spacing-unit)*2; }}
    .hero h1 {{ font-size: 2.5rem; margin-bottom: var(--spacing-unit); }}
    .cta {{ background: var(--primary); color: #fff; border: none; padding: var(--spacing-unit)*2 var(--spacing-unit)*3; border-radius: var(--radius-md); cursor: pointer; }}
    .features {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--spacing-unit)*3; padding: calc(var(--spacing-unit)*4) var(--spacing-unit)*2; }}
    .feature {{ padding: var(--spacing-unit)*3; border: 1px solid #E5E7EB; border-radius: var(--radius-md); }}
    footer {{ text-align: center; padding: var(--spacing-unit)*4; color: #6B7280; font-size: 0.875rem; }}
    @media (max-width: 768px) {{
      .features {{ grid-template-columns: 1fr; }}
      .hero h1 {{ font-size: 1.75rem; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="logo">BrandName</div>
    <nav><a href="#features">特性</a></nav>
  </header>
  <section class="hero">
    <h1>让效率翻倍</h1>
    <p>面向未来的工作流自动化</p>
    <button class="cta">立即开始</button>
  </section>
  <section class="features" id="features">
    <div class="feature"><h3>快速</h3><p>毫秒级响应</p></div>
    <div class="feature"><h3>安全</h3><p>端到端加密</p></div>
    <div class="feature"><h3>易用</h3><p>零配置上手</p></div>
  </section>
  <footer>© 2026 BrandName. All rights reserved.</footer>
</body>
</html>"""

with open("outputs/landing.html", "w", encoding="utf-8") as f:
    f.write(html)
```

[file_write] 输出 `outputs/landing.html`。

[file_read] 读取确认:文件 1 个,结构完整(header / hero / features / footer)。

## 结构说明

- header:品牌 logo + 锚点导航
- hero:主标题 + 副标题 + CTA 按钮(主色 `#3B82F6`)
- features:三列特性卡,移动端自动堆叠为单列
- footer:版权信息

## 设计规范引用

- 主色:`#3B82F6`(来源:设计系统知识库)
- 间距单位:`8px`(来源:设计系统知识库)
- 圆角:`md=8px`(来源:设计系统知识库)
- 字体:`system-ui` 栈(来源:设计系统知识库)

## 响应式断点

- 桌面端(>768px):features 三列布局
- 移动端(≤768px):features 单列堆叠,标题缩小至 1.75rem

文件路径:`outputs/landing.html`
生成时间:[datetime] 2026-08-01
