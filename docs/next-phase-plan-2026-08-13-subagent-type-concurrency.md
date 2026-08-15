# 子代理类型感知并发限流方案

**日期**: 2026-08-13
**状态**: 待评审
**作者**: 智能设计助手（蒋先生设计决策）
**关联事故**: 2026-08-12 晚「六张网」深挖任务 3 个子代理全被取消（300s 硬超时），
主会话后续 web_search 全部 30s 超时（Searchpin 通道被拖死）

---

## 一、背景与问题

### 1.1 事故复盘（2026-08-12 20:32-20:41, session 14435）

- 用户要求"深挖六张网完整项目清单"，模型 `delegate_subtask` 一次性分派 **3 个搜索类子代理**
  （water_power_grid / compute_comms / pipes_logistics）
- 3 个子代理并行跑 300s（各 27/16/17 次工具调用）仍未完成 → 被 300s 硬超时级联取消
- 取消后主会话 web_search 全部 30s 超时 —— Searchpin 通道被 4 个 ReactLoop（主会话 + 3 子代理）
  的并发请求占死

### 1.2 根因（此前已修复 A-F，本方案解决剩余的架构层问题 G）

| 层 | 现状 | 问题 |
|---|---|---|
| 委派层 | `delegate_subtask` 不区分任务类型，最多并行 3 个 | **同类型任务无上限**：3 个搜索子代理同时打外部网站 |
| 执行层 | `Semaphore(5)` 是 **ReactLoop 级**；MCP client 是**进程级单例** | **跨 ReactLoop 无进程级限流**：4 个 ReactLoop 可同时 4×5=20 个 Searchpin 请求 |
| 外部风险 | — | **网站反爬限流**：同来源请求频率过高 → 429/封 IP，搜索质量下降 |

### 1.3 蒋先生设计意图（2026-08-13 明确）

> 搜索并发限制是必要的，不光是避免管道拥挤，还要确保不被网站反爬限流。
> **主进程分派子任务时，同一类型的任务不要分派多个子任务——搜索的就搜索、分析的就分析。**

即：**任务类型感知的并发隔离**——按类型（搜索/分析/代码）分别限流，同类型不分派多个子代理。

---

## 二、目标与非目标

### 目标

1. **委派层类型去重**：同轮 `delegate_subtask` 同一类型最多 1 个子代理，超限让模型合并重规划
2. **执行层类型限流**：进程级（MCP client 级）类型感知并发上限，防多会话并发打爆共享通道
3. **反爬保护**：搜索类请求全局串行/低并发，天然压低外部网站请求频率
4. **可观测**：类型标注随子代理落库，管理端/全局智能体可查

### 非目标

- 不做请求级频率窗口（如"每分钟 N 次"）——类型并发上限已间接控制频率，暂不需要滑动窗口
- 不做自动合并多个同类型 prompt（蒋先生明确：拒绝重规划，非自动合并）
- 不改 Searchpin server 本身

---

## 三、总体设计

```
┌─────────────────────────────────────────────────────────────┐
│ delegate_subtask (委派层)                                     │
│   subtasks[] 每个元素: {id, prompt, type?}                    │
│   → 类型判定(type 显式 > 关键词推断 > 默认 search)            │
│   → 同轮同类型去重: 类型计数 > same_type_max → 拒绝重规划      │
│   → 进程级类型并发检查: 已有同类型 running → 排队/拒绝          │
└──────────────────────────┬──────────────────────────────────┘
                           │ 每个子代理继承父会话工具集(不含 delegate)
┌──────────────────────────▼──────────────────────────────────┐
│ 执行层 (MCP client 进程级单例)                                 │
│   MCPToolManager._clients[Searchpin] 全会话共享               │
│   → 新增进程级类型限流: search 类工具全局并发 ≤ type_limits    │
│   → 写锁(已有) 防管道交错 + rid 快照(已有) 防响应错乱          │
│   → 超限请求排队等待(不取消, 不丢失)                           │
└─────────────────────────────────────────────────────────────┘
```

**两层分工**：委派层**减少**同类并发（治本：从源头不产生），执行层**兜底**（治标：
即使多会话并发也压住搜索频率）。两层都实现后，无论模型是否遵守类型标注，
搜索类请求的全局并发都被限制在配置值内。

---

## 四、详细设计

### 4.1 任务类型定义与判定

**类型枚举**（新概念，贯穿委派层与执行层）：

| type | 含义 | 典型工具 | 反爬敏感度 |
|---|---|---|---|
| `search` | 网络搜索/调研/检索 | web_search, web_fetch, mcp__Searchpin__* | **高**（访问外部网站） |
| `analysis` | 数据分析/计算/统计 | code_execution, calculator | 低（本地计算） |
| `code` | 代码编写/文件操作 | file_read, file_write, code_execution | 低（本地沙箱） |
| `other` | 其他/混合 | 默认兜底 | 中 |

**判定优先级**（三选一，依次降级）：

1. **显式声明**：`delegate_subtask` schema 的 `subtasks[].type` 字段（可选）。模型给出即用。
2. **关键词推断**（模型未填时，服务端兜底）：对 `prompt` 做粗分词匹配
   - `search`：搜|搜索|查|检索|调研|查询|调研|找|资料|信息|web|search|fetch|report 相关
   - `analysis`：分析|计算|统计|处理|整理|汇总|评估|analysis|calc|统计
   - `code`：写|编写|创建|修改|代码|脚本|文件|生成文件|write|create|script
   - 多个命中取第一个（按 search > analysis > code 的敏感度优先？**取"最敏感"即 search 优先**，
     因为 search 的限流最重要——宁可保守判为 search）
3. **默认 `search`**（保守：类型不确定时按最敏感的搜索处理，确保反爬保护生效）

**判定函数**：`infer_task_type(prompt: str, explicit: str | None) -> str`
- 显式且合法 → 返回显式值
- 显式非法（不在枚举）→ 忽略，走推断
- 推断无命中 → `search`

### 4.2 委派层：同类型去重 + 进程级并发检查（`delegate_subtask.py`）

在 `_delegate_handler` 的校验段（现 `subtasks` 校验之后、建行之前）插入：

```
1. 类型判定: 对每个 subtask 调 infer_task_type → types[i]
2. 同轮去重: 统计 Counter(types)
   - 若任一类型计数 > same_type_max(默认1):
     → 返回 ToolResult(error=f"同类子任务已含 {type}×{n}, 请合并为一个子任务后重试")
     → 不建行、不 spawn（本轮委派整体拒绝，模型自行合并重规划）
3. 进程级并发检查: 对每个类型查"进程内 running 子代理的类型计数"
   - 已有同类型 running 且 ≥ 上限 → 排队等待(见 4.3) 或返回错误
```

**进程级 running 类型计数**：新增模块级 `SubagentTypeRegistry`（进程单例）：

```python
class SubagentTypeRegistry:
    """进程级: running 子代理的类型并发计数(跨会话/跨轮)。"""
    _counts: dict[str, int]      # type → 当前 running 数
    _locks: dict[str, asyncio.Lock]
    _cond: dict[str, asyncio.Condition]

    async def acquire(self, typ: str, max_conc: int) -> None:
        """类型并发超限时等待(可带 timeout, 默认 30s)。"""
    def release(self, typ: str) -> None:
        """子代理终态(成功/失败/取消)时减计数。"""
```

- runner 启动时 `acquire`（超限等待），runner 终态（`_finish`/`_mark_cancelled`/异常）时 `release`
- 计数从 `subagents.status='running'` 初始化（进程重启后恢复一致性）
- **等待超时**（默认 30s）仍不可用 → 返回错误让模型重规划（与同轮去重语义一致）

### 4.3 执行层：进程级类型感知限流（`mcp_client.py` / `mcp_tools.py`）

MCP client 已是进程级单例（`MCPToolManager._clients` 按 server 缓存），在其上新增
**按工具类别的进程级并发限制**：

```python
# mcp_client.py: 每 client 新增类型信号量
self._type_sems: dict[str, asyncio.Semaphore] = {}   # tool_type → Semaphore

async def call_tool(self, name, args, tool_type="other"):
    sem = self._type_sems.setdefault(
        tool_type,
        asyncio.Semaphore(self._config.type_limits.get(tool_type, 5)),
    )
    async with sem:
        return await self._call_tool_impl(name, args)   # 原逻辑(写锁+rid 不变)
```

**工具→类型映射**（执行层判定，与委派层共用 `infer_task_type` 的敏感度逻辑）：

| 工具名前缀 | 类型 |
|---|---|
| `web_search`, `web_fetch`, `mcp__Searchpin__*` | `search` |
| `mcp__hexin-ifind-ds-*`（金融数据） | `analysis` |
| 其余 | `other` |

**配置**（`config.yaml tools.mcp.type_limits`）：

```yaml
tools:
  mcp:
    concurrent_limit: 5        # 现有: ReactLoop 级总并行
    type_limits:               # 新增: 进程级类型并发上限(MCP client 级)
      search: 1                # 全局最多 1 个搜索请求在飞行(反爬保护核心)
      analysis: 3
      code: 3
      other: 5
```

**排队语义**：`async with sem` 天然排队（不取消、不丢失）。搜索请求排队的副作用是
单请求可能等更久 → 配合现有 `timeout_sec`（Searchpin 60s）兜底。

### 4.4 配置项汇总（`config.yaml`）

```yaml
tools:
  subagent:
    same_type_max: 1        # 委派层: 同轮同类型子代理上限(默认 1 = 同类型只开 1 个)
    type_wait_timeout_sec: 30  # 委派层: 同类型并发等待超时(超时返回错误让模型重规划)
  mcp:
    type_limits:            # 执行层: 进程级类型并发上限(见 4.3)
      search: 1
      analysis: 3
      code: 3
      other: 5
```

### 4.5 数据与可观测

- `subagents` 表加 `task_type VARCHAR(20) NOT NULL DEFAULT 'other'`（migration 幂等加列）
  - 建行时写入 `infer_task_type` 结果
  - 前端子代理卡片展示类型标签（可选，Phase 2）
- 全局智能体 `subagent_status` 工具（已实现）增加 `type` 过滤支持（可选）

---

## 五、接口变更

| 接口 | 变更 |
|---|---|
| `delegate_subtask` 工具 schema | `subtasks[].type` 新增可选字段（enum: search/analysis/code/other） |
| `delegate_subtask` 行为 | 同轮同类型 > `same_type_max` → 返回错误（拒绝重规划）；同类型并发等待 > `type_wait_timeout_sec` → 返回错误 |
| 后端日志/埋点 | 子代理建行/终态打 `task_type`；类型等待/拒绝事件写父会话 react_events（`event_type='subagent'`） |
| config.yaml | `tools.subagent.same_type_max` / `type_wait_timeout_sec` / `tools.mcp.type_limits` |

---

## 六、测试计划

### 单元测试（新增 `tests/test_delegate_type_limits.py`）

1. `infer_task_type`：显式优先 / 关键词推断（中英文）/ 空兜底默认 search
2. 同轮同类型去重：3 个 search 子任务 → 拒绝，返回明确错误（不建行）
3. 混合类型：search + analysis + code → 全通过（互不冲突）
4. 显式非法 type → 忽略走推断
5. `SubagentTypeRegistry.acquire/release`：并发计数正确增减、超限等待、超时返回

### 集成测试

6. `delegate_subtask` handler：类型去重 + 进程级并发检查端到端（mock conn/registry）
7. MCP client 类型限流：模拟并发 search 调用，断言同时飞行数 ≤ `type_limits.search`

### 回归

8. 既有 `test_subagent*.py` / `test_admin_supplementary_skills.py` 全过

---

## 七、实施步骤与工作量

| 步骤 | 内容 | 工作量 |
|---|---|---|
| 1 | `infer_task_type` 判定函数 + 单测 | ~1.5h |
| 2 | `SubagentTypeRegistry` 进程级并发计数 + 单测 | ~2h |
| 3 | `delegate_subtask` schema 加 type + handler 去重/等待/拒绝 + 集成测 | ~3h |
| 4 | `subagents.task_type` migration + 建行写入 + 埋点 | ~1.5h |
| 5 | MCP client 类型信号量 + config `type_limits` + 集成测 | ~3h |
| 6 | 回归 + 文档收尾 | ~1h |
| **合计** | | **~12h（约 1.5 个工作日）** |

---

## 八、风险与权衡

| 风险 | 缓解 |
|---|---|
| **模型不填 type / 填错** | 关键词推断兜底；默认 search（保守）；执行层类型限流独立兜底，不依赖模型 |
| **搜索请求排队变慢** | search 全局并发 1 是最坏场景；实际搜索子代理通常短请求；`type_wait_timeout_sec` 防无限等 |
| **同轮拒绝可能导致模型反复试** | 错误信息明确给出"请合并"指令；模型合并后一次通过（DeepSeek 系列遵循度高） |
| **frozen hash 影响** | `delegate_subtask` 不进 frozen_tools（现有设计），schema 变更不影响 hash |
| **多会话竞争 search 配额** | 这是预期行为——反爬保护优先于并发收益；分析类不受影响 |
| **兼容性** | `type` 字段可选，旧 prompt 无 type 走推断，行为向后兼容 |

---

## 九、验收标准

1. 「六张网」类场景：3 个搜索子任务 → 被拒绝为 1 个（模型合并后 1 个搜索子代理执行）
2. 全局任意时刻 search 类 MCP 请求飞行数 ≤ `type_limits.search`（多会话并发验证）
3. 混合类型委派（search + analysis + code）不受限流影响，正常并行
4. 所有类型等待/拒绝都有明确错误信息（LLM 可理解、用户可查）
5. 全部测试通过，无回归
