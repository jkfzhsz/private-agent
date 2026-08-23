# PDF 文档生成 (PDF Generator)

你是一位 PDF 文档生成专家。当用户需要创建 PDF 文件时使用本技能。

## 触发条件

- 「生成 PDF」「导出 PDF」「打印成 PDF」
- 「做个电子版」「生成发票/合同/证书」
- 需要将 HTML/Markdown/文本 转换为 PDF
- 需要合并、拆分 PDF 文件

## 技术方案

根据场景选择最佳方案：

### 方案一：HTML → PDF（推荐，排版最灵活）

使用 `weasyprint` 或 `pdfkit`（需安装 wkhtmltopdf）。

```bash
pip install weasyprint
# 或
pip install pdfkit  # 还需要系统安装 wkhtmltopdf
```

```python
# === WeasyPrint 方案 ===
from weasyprint import HTML

html_content = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
  @page {
    size: A4;
    margin: 2.5cm 2cm;
    @bottom-center {
      content: "第 " counter(page) " 页";
      font-size: 10pt;
      color: #999;
    }
  }
  body {
    font-family: "SimSun", "宋体", serif;
    font-size: 12pt;
    line-height: 1.8;
    color: #333;
  }
  h1 { font-size: 22pt; text-align: center; margin-bottom: 1cm; }
  h2 { font-size: 16pt; border-bottom: 1px solid #ddd; padding-bottom: 4pt; }
  .signature { margin-top: 2cm; text-align: right; }
  table { width: 100%; border-collapse: collapse; margin: 1em 0; }
  th, td { border: 1px solid #ddd; padding: 8pt; text-align: left; }
  th { background: #f5f5f5; }
</style>
</head>
<body>
  <h1>文档标题</h1>
  <p>正文内容...</p>
</body>
</html>
"""

HTML(string=html_content).write_pdf('output.pdf')
```

### 方案二：Markdown → PDF

```bash
pip install markdown2 weasyprint
```

```python
import markdown2
from weasyprint import HTML

with open('document.md', 'r', encoding='utf-8') as f:
    md_text = f.read()

html_body = markdown2.markdown(md_text, extras=['tables', 'fenced-code-blocks'])

html_full = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<style>
  body {{ font-family: "SimSun", serif; font-size: 12pt; line-height: 1.8; padding: 2cm; }}
  h1 {{ font-size: 22pt; }}
  h2 {{ font-size: 16pt; border-bottom: 1px solid #ddd; }}
  pre {{ background: #f5f5f5; padding: 1em; border-radius: 4px; }}
  code {{ font-family: "Consolas", monospace; font-size: 10pt; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 6pt; }}
</style></head><body>{html_body}</body></html>"""

HTML(string=html_full).write_pdf('output.pdf')
```

### 方案三：纯 Python 生成（reportlab，适合发票/证书等固定布局）

```bash
pip install reportlab
```

```python
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 注册中文字体（需要用户提供字体文件路径）
# pdfmetrics.registerFont(TTFont('SimSun', 'C:/Windows/Fonts/simsun.ttc'))

c = canvas.Canvas('output.pdf', pagesize=A4)
width, height = A4

# 绘制文字
c.setFont('Helvetica', 24)
c.drawString(200, height - 100, 'Document Title')

# 绘制线条
c.line(50, height - 120, width - 50, height - 120)

c.save()
```

### 方案四：PDF 处理（合并/拆分/旋转/提取）

```bash
pip install pypdf
```

```python
from pypdf import PdfReader, PdfWriter, PdfMerger

# 合并
merger = PdfMerger()
for pdf_path in ['file1.pdf', 'file2.pdf']:
    merger.append(pdf_path)
merger.write('merged.pdf')

# 拆分（提取第1-3页）
reader = PdfReader('input.pdf')
writer = PdfWriter()
for i in range(3):
    writer.add_page(reader.pages[i])
writer.write('split.pdf')

# 提取文本
reader = PdfReader('document.pdf')
for page in reader.pages:
    print(page.extract_text())
```

## 方案选择指南

| 场景 | 推荐方案 | 理由 |
|------|---------|------|
| 长篇报告/电子书 | HTML → PDF (weasyprint) | 排版灵活，支持 CSS 分页 |
| Markdown 文档 | Markdown → PDF | 最大程度保留原文格式 |
| 发票/证书/名片 | reportlab | 精确控制元素位置 |
| 从网页生成 | weasyprint (URL) | 直接渲染网页 |
| 合并/拆分/提取 | pypdf | 轻量无依赖 |
| 复杂中文排版 | weasyprint + 系统字体 | CSS @font-face 指定中文字体 |

## 工作流程

1. **需求确认**：来源格式、目标布局、中文字体需求
2. **方案选择**：根据场景选择上述 4 种方案之一
3. **脚本生成**：写出完整的 Python 脚本
4. **运行生成**：执行脚本产生 PDF
5. **验证**：检查页数、字体、边距是否正确

## 关键提示

- 中文字体是最大坑点：weasyprint 需要通过 CSS @font-face 或系统字体路径指定
- Windows 中文字体路径：`C:/Windows/Fonts/simsun.ttc`（宋体）、`C:/Windows/Fonts/simhei.ttf`（黑体）
- A4 尺寸：210mm × 297mm
- 打印用 PDF 建议 margin ≥ 2cm
- weasyprint 不支持 JavaScript，动态内容需要先渲染成静态 HTML

## 版式增强规范（design-stylist 对齐, 2026-08-15）

- **三套版式预设**：学术报告 / 商业提案 / 白皮书（页边距、页眉页脚、标题层级、图题表题、参考文献样式各一套）
- **生成方案（2026-08-15 环境验证定稿）**：
  - 主方案 **Typst**：`pip install typst`（D 盘 venv 已装 0.15.0）；`typst.compile("文件.typ", output="out.pdf")` —— input 必须传**文件路径**，非源码字符串；页脚页码 `#set page(footer: context "第 " + counter(page).display() + " 页")`（勿用 `context { }` 块，会报 `label <page> does not exist`）
  - HTML→PDF 兜底：**Edge headless**（`msedge --headless=new --disable-gpu --print-to-pdf="out.pdf" file:///...`，系统自带零依赖）
  - **weasyprint 弃用**：Windows 需 GTK3 runtime（import 报 `cannot load library 'libgobject-2.0-0'`）
- **中文字体**：Microsoft YaHei（系统自带，实测可用）
- **输出前自查**：页数、字体、页边距 ≥2cm、页眉页脚页码