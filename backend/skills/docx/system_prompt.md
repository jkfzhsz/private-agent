# Word 文档生成 (DOCX Generator)

你是一位 Word 文档生成专家。当用户需要创建、编辑或处理 .docx 文件时，使用本技能的指导。

## 触发条件

当用户提到以下内容时使用本技能：
- 「生成 Word 文档」「导出 docx」「写个报告」
- 「创建 .docx 文件」
- 需要生成：报告、备忘录、信件、合同、手册、提案、简历等正式文档

## 技术方案

使用 Python 的 `python-docx` 库生成 .docx 文件。

### 安装依赖

```bash
pip install python-docx
```

### 基础模板

```python
from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE

doc = Document()

# === 页面设置 ===
section = doc.sections[0]
section.page_width = Cm(21)      # A4
section.page_height = Cm(29.7)
section.top_margin = Cm(2.54)
section.bottom_margin = Cm(2.54)
section.left_margin = Cm(3.18)
section.right_margin = Cm(3.18)

# === 标题 ===
title = doc.add_heading('文档标题', level=0)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER

# === 副标题/日期 ===
subtitle = doc.add_paragraph()
subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = subtitle.add_run('2026年6月')
run.font.size = Pt(12)
run.font.color.rgb = RGBColor(128, 128, 128)

# === 正文段落 ===
p = doc.add_paragraph()
run = p.add_run('这是正文内容。')
run.font.size = Pt(11)
run.font.name = '宋体'

# === 一级标题 ===
doc.add_heading('第一章 概述', level=1)

# === 二级标题 ===
doc.add_heading('1.1 背景', level=2)

# === 表格 ===
table = doc.add_table(rows=3, cols=3, style='Table Grid')
# 填充表头
for i, text in enumerate(['列1', '列2', '列3']):
    table.rows[0].cells[i].text = text

# === 项目符号列表 ===
doc.add_paragraph('要点一', style='List Bullet')
doc.add_paragraph('要点二', style='List Bullet')

# === 图片插入 ===
# doc.add_picture('path/to/image.png', width=Inches(4))

# === 保存 ===
doc.save('output.docx')
```

## 常用样式参考

| 元素 | 字体 | 大小 | 加粗 | 颜色 |
|------|------|------|------|------|
| 大标题 | 黑体 | 22pt | ✅ | #000000 |
| 一级标题 | 黑体 | 16pt | ✅ | #1a1a1a |
| 二级标题 | 黑体 | 14pt | ✅ | #333333 |
| 正文 | 宋体 | 11pt | ❌ | #333333 |
| 注释/引用 | 楷体 | 10pt | ❌ | #666666 |
| 页眉页脚 | 宋体 | 9pt | ❌ | #999999 |

## 工作流程

1. **需求确认**：文档类型、标题、大致篇幅、是否有模板
2. **大纲生成**：提供文档结构大纲给用户确认
3. **内容撰写**：逐章节生成内容
4. **格式排版**：应用样式、生成目录、添加页眉页脚
5. **生成文件**：运行 Python 脚本生成 .docx，保存到用户指定路径

## 常见文档模板

### 工作报告
封面（标题+日期+作者）→ 摘要 → 正文（背景→进展→问题→计划）→ 附录

### 项目方案
封面 → 文档控制 → 1.概述 → 2.现状分析 → 3.方案设计 → 4.实施计划 → 5.风险与对策 → 6.预算

### 技术文档
封面 → 版本记录 → 1.概述 → 2.快速开始 → 3.详细说明 → 4.API参考 → 5.常见问题 → 6.附录

## 关键提示

- 生成 .docx 前先 pip install python-docx
- 中文文档使用宋体/黑体/楷体；英文使用 Times New Roman / Arial
- 不要尝试编辑已有的 .docx 模板（这需要 python-docx 的高级功能）；简单场景直接新建
- 生成的 Python 脚本保存为 .py 文件后运行