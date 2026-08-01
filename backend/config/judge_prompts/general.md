# 通用 LLM-as-Judge 评分模板

> 蓝图 §8.8 LLM-as-Judge(GLM-4-Flash)
> 输入:用户请求 / Agent 响应 / 期望输出
> 输出:严格 JSON,含 response_quality / task_completion / quality_reason / completion_reason

---

## 评分任务说明

你是一个严格的评估员。请根据以下三段输入,对 Agent 的响应质量进行评分。

### 输入

**用户请求**:
```
{user_input}
```

**Agent 响应**:
```
{agent_response}
```

**期望输出(参考答案)**:
```
{expected_output}
```

### 评分维度

1. **response_quality**(1-5 分):响应整体质量
   - 5:完全正确、结构清晰、无冗余
   - 4:基本正确,有轻微瑕疵
   - 3:方向正确但有明显错误或遗漏
   - 2:部分错误,核心信息缺失
   - 1:完全错误或无意义

2. **task_completion**(1-5 分):任务完成度(对照 expected_output)
   - 5:完全覆盖 expected_output 的所有要点
   - 4:覆盖 80% 以上要点
   - 3:覆盖 50-80% 要点
   - 2:覆盖 20-50% 要点
   - 1:几乎未覆盖 expected_output 要点

### 输出格式(严格 JSON,不要包裹 markdown code fence)

```
{
  "response_quality": <1-5 整数>,
  "task_completion": <1-5 整数>,
  "quality_reason": "<对 response_quality 评分的简短理由,不超过 100 字>",
  "completion_reason": "<对 task_completion 评分的简短理由,指出缺失或错误的要点,不超过 100 字>"
}
```

### 评分规则

- 仅根据用户请求、Agent 响应、期望输出三段输入评分,不引入外部知识
- 若 Agent 响应与期望输出冲突,以期望输出为准扣分
- 若 Agent 响应包含 expected_output 中未要求的内容,不加分但也不扣分
- 若 Agent 响应拒绝回答或返回错误信息,task_completion 直接 1 分
- 输出必须是合法 JSON,任何额外文本(包括 markdown 围栏)都视为评分失败
