# 示例:Excel 数据汇总

## 用户请求

我有一个销售数据 Excel 文件 `sales_q3.xlsx`,包含日期、产品、销售额三列,请帮我汇总各产品 Q3 总销售额并生成新表。

## 助手响应

我将按 `file_read` → `code_execution` → `file_write` 流程处理。

[file_read] 读取 `sales_q3.xlsx` 成功,共 230 行。

[code_execution] 执行 pandas 聚合:
```python
import pandas as pd
df = pd.read_excel("sales_q3.xlsx")
summary = df.groupby("产品")["销售额"].sum().reset_index()
summary.to_excel("outputs/sales_q3_summary.xlsx", index=False)
```

[file_write] 输出 `outputs/sales_q3_summary.xlsx`。

## 汇总结果

| 产品 | Q3 总销售额 |
|------|------------|
| 产品A | 156,800 |
| 产品B | 98,500 |
| 产品C | 245,300 |

文件路径:`outputs/sales_q3_summary.xlsx`
