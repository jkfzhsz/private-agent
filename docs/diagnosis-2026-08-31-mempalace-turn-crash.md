# 诊断报告：mempalace 3.8.0 接入后 session-76 后续任务连续失败

- **诊断时间**：2026-08-31 08:35 (GMT+8)
- **诊断对象**：Private Agent session-76（无涯 / monitor）、mempalace 3.8.0 接入链路
- **诊断方式**：后端日志取证 → 源码定位 → 最小复现验证（未做任何代码改动）

---

## 一、结论先行

| 项 | 结论 |
|---|---|
| mempalace 升级是否成功 | ✅ **成功**。3.8.0 + chromadb 1.5.9 正常服务，45 个工具（升级前 36 个），354 drawer / 18 wings / 40 rooms，SQLite 完整性 `ok`（error_count=0） |
| 无涯识别是否正常 | ✅ **正常**。`mcp_browse list` 返回全量索引，`mcp__mempalace__mempalace_list_drawers` 实测读取 245 条成功 |
| 后续任务失败根因 | ❌ **PA 后端代码缺陷**（`react_loop._parse_assembly_marker` 缺类型防御），**与 mempalace 本身无关**，但升级是触发条件 |
| 失败表现 | 用户「执行B和C」及两次「请继续」后，**智能体零输出**——整轮 turn 抛异常终止，前端无任何回复 |

**关键区分**：升级不是"坏"的原因，PA 侧一个 2026-08-23 引入的旧缺陷被新工具集"撞上"了。工具数 36 → 45 后，返回值形态变多（新增 event/artifact/task/tunnel 类工具多返回数组），命中了未防御的分支。

---

## 二、硬证据

### 2.1 后端日志（`D:\Private agent\backend\logs\agent.log`）

三次崩溃，异常完全一致：

```
2026-08-30T09:47:37  step=9/run_turn_start_r1 session=76
2026-08-30T09:48:12  ERROR user_message handling failed        ← 35s 后崩溃
2026-08-31T00:28:52  step=9/run_turn_start_r1 session=76
2026-08-31T00:29:18  ERROR user_message handling failed        ← 26s 后崩溃
```

异常栈（三次一致）：

```
File "main.py", line 1939, in _handle_user_message
    await _turn_task
File "core\react_loop.py", line 1449, in run_turn
    outcomes = await asyncio.gather(
File "core\react_loop.py", line 1379, in _exec_plan
    marker = _parse_assembly_marker(result.output)
File "core\react_loop.py", line 76, in _parse_assembly_marker
    marker = data.get("__mcp_assembly")
AttributeError: 'list' object has no attribute 'get'
```

时间线对齐：session-76 导出时间 `2026-08-31T00:32:09Z`，最后一条日志 `00:29:18Z` 崩溃 —— **最后一次「请继续」就是崩溃的那轮**。

### 2.2 最小复现验证（本机实跑，零改动）

```
[OK   ] mcp_browse list 输出(纯文本)     -> None
[OK   ] assemble 标记(dict)              -> {'action': 'assemble', 'server_ids': ['mempalace']}
[CRASH] exec 返回 JSON 数组              -> AttributeError: 'list' object has no attribute 'get'
[CRASH] exec 返回裸数组(字符串元素)       -> AttributeError: 'list' object has no attribute 'get'
[CRASH] exec 返回 JSON 标量              -> AttributeError: 'str' object has no attribute 'get'
[CRASH] exec 返回 JSON 数字              -> AttributeError: 'int' object has no attribute 'get'
```

与生产日志的 `AttributeError` 逐字吻合。

---

## 三、根因分析（代码级）

### 缺陷 A（P0，致命）

**位置**：`backend/private_agent/core/react_loop.py` L63-77

```python
def _parse_assembly_marker(output: str) -> dict | None:
    if not output:
        return None
    try:
        data = json.loads(output)
    except Exception:          # noqa: BLE001
        return None
    marker = data.get("__mcp_assembly")   # ← L76: data 可能非 dict
    return marker if isinstance(marker, dict) else None
```

**问题**：`json.loads` 只保证"是合法 JSON"，不保证"是 dict"。成功解析出 `list` / `str` / `int` / `float` / `bool` 时，`.get()` 抛 `AttributeError`，且**该行在 `try` 块之外，`except` 兜不住**。

**调用点**：同文件 L1373-1379

```python
if (plan["tool_name"] == "mcp_browse"
        and result is not None
        and not result.error):
    marker = _parse_assembly_marker(result.output)   # ← 崩溃点
```

**为何只在 `exec` 动作上触发**：

| action | 返回形态 | json.loads | 结果 |
|---|---|---|---|
| `list` | 纯文本索引（"MCP 全局工具索引…"） | 失败 | ✅ 安全返回 None |
| `assemble` / `remove` | `{"__mcp_assembly": {...}}` | dict | ✅ 正常 |
| **`exec`** | MCP 工具原始返回值，**由被调用方决定** | **list / 标量** | ❌ **崩溃** |

`exec` 走 `mcp_tools.exec_tool()` → `ToolResult(output=mcp_result_to_text(result))`，输出完全取决于 MCP server 返回什么。mempalace 3.8.0 新增的 `event_list` / `find_tunnels` / `list_tunnels` / `search`(hits) / `kg_timeline` / `mesh_peers` 等，返回数组是常态。

**与升级的因果关系**：B 任务（"验证 3.8.0 新增工具实际用法——事件流/artifact 交接连通性"）必然要调这批新工具；为"不占上下文"，模型首选 `mcp_browse exec` 中转 → 撞雷 → 整轮崩溃 → 零输出。

### 缺陷 B（P0，放大器）

**位置**：`react_loop.py` L1449-1451

```python
outcomes = await asyncio.gather(
    *(_exec_plan(p) for p in parallel_plans)
)
```

**问题**：`gather` 未传 `return_exceptions=True`。**任意一个**并行工具抛异常 → 整个 `gather` 抛出 → `run_turn` 终止 → `main._handle_user_message` 记录 "handling failed" → 该轮所有其他工具的成果全部丢弃，前端无任何回复。

单个工具的解析异常不应具备"炸掉整轮"的破坏力。两个缺陷叠加，才造成"用户催两次仍零响应"。

### 缺陷 C（P1，`mcp_server_list` 恒报 0）

**位置**：`tools/builtins/mcp_config_manager.py` L58、L112

```python
async def _mcp_server_list_handler(args: dict) -> ToolResult:
    mcp_cfg = _load_mcp_cfg()      # ← 只读 config.yaml
```

而 `mcp_browse` 用的是 `_wrap_merged_mcp_cfg()`（L216，读 config.yaml + config_runtime 合并）。**MCP server 实际存 config_runtime**（设置页动态管理），config.yaml 的 `servers` 为空数组 → `mcp_server_list` 恒返回 0。

这与 session-76 中 `mcp_server_list` 报"MCP servers 共 0 个"完全吻合，也**误导了无涯对 MCP 现状的判断**。

> 附带风险：L112 `_mcp_server_add_handler` 同样走 `_load_mcp_cfg()`，新增 server 写回 config.yaml —— 与运行时读取路径分叉，新增的 server 能否生效取决于合并算法（**待验证，未证实**）。

### 缺陷 D（P1，启动期报错）

```
main.py L2148 → mission_runner.py L634
asyncpg.exceptions._base.InterfaceError:
    cannot call Connection.execute(): connection has been released back to the pool
```

`_on_startup` 中其他 DB 操作均用 `async with db._pool.acquire() as conn:`（正确），唯独 mission cleanup 直接复用了外层**已 release** 的 `conn` 变量。每次启动必报，被 `try/except` 吞掉，不阻塞启动，但 mission 重启恢复功能实际失效。

### 缺陷 E（P2，环境资源）

本机实测：**RAM 负载 94%，总量 7.6GB，可用仅 0.39GB**。session-76 中 mempalace 服务进程占 694MB，WorkBuddy 自身约 2GB。

这是 session-76 中 `code_execution` **300s 超时**的直接诱因：内存压力下新 Python 子进程启动 + 杀软实时扫描 → 冷启动时间被拉长到超过 300s 硬超时。（用户已知基线：Python 冷启动约 16s。）

---

## 四、影响面评估

| 缺陷 | 影响 | 触发频率 |
|---|---|---|
| A | `mcp_browse exec` 调到返回非 dict JSON 的工具 → 整轮崩溃 | 只要用 exec 调数组型返回工具，**必现** |
| B | 单工具异常 → 整轮全丢，用户零反馈 | 每次 A 触发时放大 |
| C | `mcp_server_list` 恒 0；无涯误判 MCP 配置状态 | **每次调用必现** |
| D | mission 重启恢复失效 | 每次启动必现（静默） |
| E | 长任务/code_execution 超时 | 内存水位高时 |

**不受影响**：mempalace 直接工具调用（`mcp__mempalace__*`）、`mcp_browse list` / `assemble` / `remove`、普通对话与 LLM 调用。

---

## 五、修复方案（待批准，未执行）

### 方案 A —— 最小止血（推荐，2 处改动）

1. **`react_loop.py` L73 后插入类型防御**：

```python
    try:
        data = json.loads(output)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):   # ← 新增：list/str/int/float/bool 一律忽略
        return None
    marker = data.get("__mcp_assembly")
```

2. **`react_loop.py` L1449 gather 加异常隔离**：

```python
outcomes = await asyncio.gather(
    *(_exec_plan(p) for p in parallel_plans),
    return_exceptions=True,
)
# 异常项转成 ToolResult(error=...)，保证单工具失败不炸整轮
```

- **风险**：低。纯防御性改动，不改变任何既有成功路径的语义。
- **验证**：复现脚本 6 个用例全绿 + 现有 pytest 回归。

### 方案 B —— A + 配置路径对齐

把 `_mcp_server_list_handler` / `_mcp_server_add_handler` 的 `_load_mcp_cfg()` 换成 `_wrap_merged_mcp_cfg()`（与 `mcp_browse` 对齐）。

- **风险**：中。`mcp_server_add` 的写入目标需先确认合并算法（yaml 是否参与 merge），建议只先改 `list`，`add` 单独评估。

### 方案 C —— B + 启动期与资源

1. `_on_startup` mission cleanup 改用 `async with db._pool.acquire() as conn:`
2. RAM：给 mempalace 加内存约束 / 改按需启动；或评估 `code_execution` 超时阈值

- **风险**：中。涉及启动流程与资源策略，建议延后单独排期。

---

## 六、建议实施顺序

```
A（止血，必做） → 回归验证 → B-list（配置读取对齐） → C（启动期 + 资源）
```

按用户 TDD 与原子提交约定，建议拆为：

1. `fix(react_loop): _parse_assembly_marker 非 dict 输出类型防御 + gather 异常隔离` + 新增单测
2. `fix(mcp_config): mcp_server_list 读合并配置(对齐 mcp_browse)`
3. `fix(main): startup mission cleanup 连接池生命周期`

---

## 七、附：session-76 中的两个观察（非故障，供参考）

1. **无涯读到的 `mempalace-server.log` 是陈旧内容**：`D:\mempalace\mempalace-server.log` 最后修改时间为 `2026-08-06 09:35`，且内容是 **HTTP transport 模式**（`listening on http://127.0.0.1:8137/mcp`）的历史痕迹；当前 PA 用的是 **stdio** 模式，日志不写该文件。无涯据此外推"服务运行中（POST /mcp 200）"属于**证据误用**——结论碰巧正确，但取证路径不成立。建议后续把"服务存活"判定改为直接 `mempalace_status` 调用。

2. **`status` 报 354 drawer 与 `list_drawers` 报 245 的差异**：前者统计全部条目类型，后者按 drawer 类型过滤，属正常口径差异，非故障（无涯当时的判断正确）。
