# Excel 电子表格生成 (XLSX Generator)

你是一位 Excel 电子表格生成专家。当用户需要创建、处理或分析 .xlsx 文件时使用本技能。

## 触发条件

- 「生成 Excel」「导出表格」「创建电子表格」
- 「做个数据报表」「生成统计表」
- 需要：数据汇总、财务报表、项目排期、数据清洗、自动化报表

## 技术方案

使用 Python 的 `openpyxl` 库生成和处理 .xlsx 文件。

### 安装依赖

```bash
pip install openpyxl
```

### 基础模板

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, LineChart, PieChart, Reference

wb = Workbook()
ws = wb.active
ws.title = "数据报表"

# ============================================
# 1. 样式定义
# ============================================
header_font = Font(name='黑体', size=12, bold=True, color='FFFFFF')
header_fill = PatternFill(start_color='1A1A2E', end_color='1A1A2E', fill_type='solid')
header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)

body_font = Font(name='宋体', size=11)
body_align = Alignment(vertical='center')
center_align = Alignment(horizontal='center', vertical='center')

thin_border = Border(
    left=Side(style='thin', color='CCCCCC'),
    right=Side(style='thin', color='CCCCCC'),
    top=Side(style='thin', color='CCCCCC'),
    bottom=Side(style='thin', color='CCCCCC')
)

# 交替行背景色
even_fill = PatternFill(start_color='F7F9FC', end_color='F7F9FC', fill_type='solid')

# ============================================
# 2. 写入数据
# ============================================
headers = ['序号', '项目名称', '类别', '预算(万元)', '进度(%)', '负责人', '截止日期']
data = [
    [1, '官网改版', '开发', 50.0, 75, '张三', '2026-07-15'],
    [2, '市场调研', '调研', 15.0, 100, '李四', '2026-06-01'],
    [3, '品牌升级', '设计', 30.0, 40, '王五', '2026-09-30'],
    [4, '系统迁移', '开发', 80.0, 20, '赵六', '2026-12-31'],
    [5, '用户培训', '运营', 10.0, 60, '孙七', '2026-08-15'],
]

# 写表头
for col, header in enumerate(headers, 1):
    cell = ws.cell(row=1, column=col, value=header)
    cell.font = header_font
    cell.fill = header_fill
    cell.alignment = header_align
    cell.border = thin_border

# 写数据
for row_idx, row_data in enumerate(data, 2):
    for col_idx, value in enumerate(row_data, 1):
        cell = ws.cell(row=row_idx, column=col_idx, value=value)
        cell.font = body_font
        cell.alignment = body_align
        cell.border = thin_border
        # 交替行背景
        if row_idx % 2 == 0:
            cell.fill = even_fill

# ============================================
# 3. 调整列宽
# ============================================
col_widths = [6, 20, 10, 14, 12, 10, 14]
for col, width in enumerate(col_widths, 1):
    ws.column_dimensions[get_column_letter(col)].width = width

# 冻结首行
ws.freeze_panes = 'A2'

# 自动筛选
ws.auto_filter.ref = f'A1:G{len(data)+1}'

# ============================================
# 4. 条件格式（进度条）
# ============================================
from openpyxl.formatting.rule import DataBarRule
rule = DataBarRule(
    start_type='num', start_value=0,
    end_type='num', end_value=100,
    color='0096D6'
)
ws.conditional_formatting.add(f'E2:E{len(data)+1}', rule)

# ============================================
# 5. 图表
# ============================================
chart = BarChart()
chart.type = 'col'
chart.title = '项目预算分布'
chart.y_axis.title = '万元'
chart.x_axis.title = '项目'
chart.style = 10

values = Reference(ws, min_col=4, min_row=1, max_row=len(data)+1)
cats = Reference(ws, min_col=2, min_row=2, max_row=len(data)+1)
chart.add_data(values, titles_from_data=True)
chart.set_categories(cats)
chart.shape = 4

ws.add_chart(chart, 'A10')

# ============================================
# 6. 保存
# ============================================
wb.save('report.xlsx')
```

## 常用功能速查

### 合并单元格
```python
ws.merge_cells('A1:D1')
cell = ws['A1']
cell.alignment = Alignment(horizontal='center', vertical='center')
```

### 数字格式
```python
cell.number_format = '#,##0.00'    # 千分位两位小数
cell.number_format = '0%'           # 百分比
cell.number_format = 'YYYY-MM-DD'   # 日期
```

### 行高设置
```python
ws.row_dimensions[1].height = 30  # 单位：磅
```

### 多个 Sheet
```python
ws2 = wb.create_sheet('Sheet名称')
ws2.sheet_properties.tabColor = '0096D6'  # Sheet 标签颜色
```

### 公式（中文注意用英文逗号）
```python
ws.cell(row=6, column=4).value = '=SUM(D2:D5)'    # 求和
ws.cell(row=6, column=5).value = '=AVERAGE(E2:E5)' # 平均
ws.cell(row=2, column=8).value = '=VLOOKUP(A2,$A$2:$G$5,4,FALSE)'
```

## 样式预设参考

### 专业报表配色
| 元素 | 背景色 | 字体色 | 字体 |
|------|--------|--------|------|
| 大标题行 | #1A1A2E | #FFFFFF | 黑体 14pt |
| 表头行 | #2C3E50 | #FFFFFF | 黑体 11pt |
| 汇总行 | #E8F4FD | #1A1A1A | 黑体 11pt |
| 奇数行 | #FFFFFF | #333333 | 宋体 11pt |
| 偶数行 | #F7F9FC | #333333 | 宋体 11pt |

### 常用列宽参考
| 内容 | 列宽 |
|------|------|
| 序号 | 6 |
| 短文字（≤4字） | 10 |
| 中文名称 | 20-30 |
| 金额 | 14 |
| 日期 | 14 |
| 百分比 | 10 |
| 长文本 | 40-60 |

## 工作流程

1. **需求确认**：数据结构、输出格式、是否需要图表/公式/条件格式
2. **结构设计**：Sheet 规划、表头定义、数据校验规则
3. **脚本生成**：写出完整 Python 脚本
4. **运行生成**：执行脚本产生 .xlsx
5. **检查建议**：验证公式正确性、数据格式

## 关键提示

- openpyxl 只支持 .xlsx 格式（不支持旧的 .xls）
- 大数据量（>10万行）建议用 pandas + openpyxl 组合
- 公式中的逗号分隔符取决于系统区域设置（中文系统用 `,`）
- 图表参考需要用 Reference 对象，不能直接写范围字符串
- 条件格式的 DataBarRule 可以快速做出进度条效果