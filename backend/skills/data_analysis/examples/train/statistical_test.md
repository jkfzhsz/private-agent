# 示例:统计检验

## 用户请求

我有两组用户转化率数据 `group_a.csv` 和 `group_b.csv`(各 500 条),请检验 B 组转化率是否显著高于 A 组。

## 助手响应

我将使用 scipy 执行独立样本 t 检验。

[file_read] 读取 `group_a.csv`(500 行)+ `group_b.csv`(500 行)成功。

[code_execution] 执行 scipy.stats.ttest_ind:
```python
import pandas as pd
from scipy import stats

a = pd.read_csv("group_a.csv")["conversion_rate"]
b = pd.read_csv("group_b.csv")["conversion_rate"]

t_stat, p_value = stats.ttest_ind(b, a, alternative="greater")

print(f"均值 A: {a.mean():.4f}")
print(f"均值 B: {b.mean():.4f}")
print(f"t-statistic: {t_stat:.4f}")
print(f"p-value: {p_value:.4f}")
print(f"显著性结论(α=0.05): {'拒绝原假设' if p_value < 0.05 else '不能拒绝原假设'}")
```

## 检验结果

| 项目 | 数值 |
|------|------|
| 检验方法 | 独立样本 t 检验(单尾,B > A) |
| 原假设 H₀ | μ_B ≤ μ_A |
| 备择假设 H₁ | μ_B > μ_A |
| 样本量 | A=500, B=500 |
| 均值 A | 0.1245 |
| 均值 B | 0.1382 |
| t-statistic | 2.8731 |
| p-value | 0.0021 |
| 显著性结论 | 拒绝原假设(p < 0.05) |

结论:B 组转化率(13.82%)显著高于 A 组(12.45%),p-value=0.0021 < 0.05,建议推广 B 组方案。

检验方法:scipy.stats.ttest_ind (alternative="greater")
生成时间:[datetime] 2026-08-01
