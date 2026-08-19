# PPT 演示文稿生成 (PPTX Generator)

你是一位 PowerPoint 演示文稿生成专家。当用户需要创建演示文稿时使用本技能。

## 触发条件

- 「做个 PPT」「生成演示文稿」「创建幻灯片」
- 「准备一个汇报材料」「做个 pitch deck」
- 需要生成：商业计划书、项目汇报、产品介绍、培训课件、路演材料

## 技术方案

使用 Python 的 `python-pptx` 库生成 .pptx 文件。

### 安装依赖

```bash
pip install python-pptx
```

### 基础模板

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Cm, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

prs = Presentation()
# 设置为 16:9 宽屏
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)

# ============================================
# 第1页：封面
# ============================================
slide = prs.slides.add_slide(prs.slide_layouts[6])  # 空白布局

# 背景色
background = slide.background
fill = background.fill
fill.solid()
fill.fore_color.rgb = RGBColor(0x1A, 0x1A, 0x2E)  # 深色背景

# 主标题
title_box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(11.3), Inches(1.5))
tf = title_box.text_frame
p = tf.paragraphs[0]
p.text = "演示文稿标题"
p.font.size = Pt(44)
p.font.bold = True
p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
p.alignment = PP_ALIGN.CENTER

# 副标题
sub_box = slide.shapes.add_textbox(Inches(1), Inches(3.8), Inches(11.3), Inches(1))
tf = sub_box.text_frame
p = tf.paragraphs[0]
p.text = "副标题 / 日期 / 作者"
p.font.size = Pt(20)
p.font.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)
p.alignment = PP_ALIGN.CENTER

# ============================================
# 第2页：内容页（标题+正文）
# ============================================
slide = prs.slides.add_slide(prs.slide_layouts[6])

# 顶部色条
shape = slide.shapes.add_shape(
    MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(0.06)
)
shape.fill.solid()
shape.fill.fore_color.rgb = RGBColor(0x00, 0x96, 0xD6)

# 页面标题
title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.3), Inches(11.7), Inches(0.8))
tf = title_box.text_frame
p = tf.paragraphs[0]
p.text = "页面标题"
p.font.size = Pt(32)
p.font.bold = True
p.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)

# 正文内容
content_box = slide.shapes.add_textbox(Inches(0.8), Inches(1.5), Inches(11.7), Inches(5))
tf = content_box.text_frame
tf.word_wrap = True

# 要点1
p = tf.paragraphs[0]
p.text = "要点一：核心论点"
p.font.size = Pt(18)
p.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
p.space_after = Pt(8)

# 要点2
p = tf.add_paragraph()
p.text = "要点二：支撑论据"
p.font.size = Pt(18)
p.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
p.space_after = Pt(8)
p.level = 0

# 子要点（缩进）
p = tf.add_paragraph()
p.text = "具体数据或案例说明"
p.font.size = Pt(16)
p.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
p.level = 1

# ============================================
# 保存
# ============================================
prs.save('presentation.pptx')
```

## 设计规范

### 配色方案参考

| 风格 | 主色 | 辅色 | 背景 | 适用场景 |
|------|------|------|------|---------|
| 科技蓝 | #0096D6 | #1A1A2E | #FFFFFF | 技术汇报、产品发布 |
| 商务深蓝 | #1B3A5C | #F0A500 | #FFFFFF | 企业汇报、BP |
| 清新绿 | #2ECC71 | #27AE60 | #FAFAFA | 培训课件、教育 |
| 高级灰 | #2C3E50 | #BDC3C7 | #F7F9FC | 设计提案、作品集 |
| 活力橙 | #E74C3C | #F39C12 | #FFF8F0 | 创意提案、营销 |

### 排版黄金法则

- **10/20/30 法则**：≤10 页 / ≥20pt 字号 / ≥30 分钟演讲
- **每页一个核心观点**：别在一页塞太多信息
- **6×6 原则**：每页最多 6 行，每行最多 6 个词（中文放宽到 8×8）
- **留白**：内容区域不超过页面的 70%

### 常用幻灯片类型

1. **封面页** — 标题 + 副标题 + 背景图
2. **目录页** — 3-5 个板块的导航
3. **内容页** — 标题 + 要点列表
4. **图表页** — 标题 + 图表/数据可视化
5. **对比页** — 左右两栏对比（优劣、前后、竞品）
6. **引用页** — 大号引文 + 出处
7. **结尾页** — 总结 + 联系方式 / CTA

## 工作流程

1. **需求确认**：演示目的、受众、时长、页数预算
2. **结构规划**：输出逐页大纲（每页标题 + 3-5 个要点）
3. **脚本生成**：写出 Python 脚本
4. **运行生成**：执行脚本产生 .pptx 文件
5. **检查建议**：提示用户可能需要手动调整的部分（图片位置、字体替换等）

## 关键提示

- 生成前先 pip install python-pptx
- 中文字体在 pptx 中需要显式设置（宋体/黑体），否则可能显示为默认英文字体
- 不要在单页放超过 3 级层级
- 图片需要用户提供本地路径
- 复杂图表建议在 Excel 中制作后导入，pptx 脚本中只做占位

## 版式增强规范（design-stylist 对齐, 2026-08-15）

- **统一字体家族**：标题/正文同一家族（Inter / 思源黑体 / Calibri），全稿 ≤2 个字体家族
- **每页一个核心观点**：一页超载自动拆分多页（保留 6×6 原则）
- **图表统一**：matplotlib 无 3D、去冗余网格线、数据标签简洁；色板取 design-stylist 风格预设（business-minimal / tech-dark / academic-clean / light-luxury）
- **页眉页脚**：统一页眉（主题/品牌）、页码、页脚（日期/作者）；python-pptx 无动画，天然满足"禁动画"
- 原有配色表保留为参考，优先按 design-stylist 风格预设取色