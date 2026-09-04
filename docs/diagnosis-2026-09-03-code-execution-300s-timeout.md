# code_execution 300s 超时归因诊断

- 日期：2026-09-03
- 对象：session-76（2026-08-30）中一次 `code_execution` 工具超时
- 结论：**模型生成的代码执行了 16 次全树递归 glob，其中 `C:\Users\zongxin` 单项目录占实测耗时的 98.7%。内存颠簸导致的冷缓存惩罚把 137s 的作业推过 300s 上限。网络、PYTHONPATH shim、孤儿进程三个嫌疑均已排除。**

---

## 1. 时间线（数据库取证）

来源：`react_events` 表，session_id=76。

```
id=575851  09:23:51.022  tool_call    code_execution
id=575852  09:23:51.031  tool_call    http_request  (url=mempalace.net)
id=575853  09:29:22.704  tool_result  +331.67s  【程序异常】工具 code_execution 执行超时(300s)
id=575854  09:29:22.704  tool_result  +  0.00s  ConnectTimeout  (http_request)
```

`tool_call` 事件落库了完整 `arguments.code` —— 这是本次能拿到真实代码、而非依赖推测的关键。

## 2. 真实执行的代码（决定性证据）

```python
import os, glob, subprocess, sys

candidates = []
for base in [r"D:\mempalace",
             r"D:\Private agent\tools",
             r"D:\Private agent",
             os.path.expanduser("~")]:        # ← C:\Users\zongxin
    for pat in ["**/mempalace*/METADATA",
                "**/mempalace*/PKG-INFO",
                "**/mempalace*/version.py",
                "**/mempalace/__init__.py"]:
        for f in glob.glob(os.path.join(base, pat), recursive=True):
            ...
```

**4 个基目录 × 4 个模式 = 16 次全树递归遍历**，且每次都是从头走一遍（`glob` 不复用上一次的遍历结果）。

## 3. 量化复现（本机实跑，非估算）

原样复刻该代码逐段计时（`pa_ce_repro.py`）：

| 基目录 | 条目数 | 4 次 glob 耗时 | 占比 |
|---|---:|---:|---:|
| `D:\mempalace` | 56 | 0.01 s | 0.0% |
| `D:\Private agent\tools` | 1,513 | 0.08 s | 0.1% |
| `D:\Private agent` | 88,594 | 1.77 s | 1.3% |
| **`C:\Users\zongxin`** | **548,728** | **135.69 s** | **98.7%** |
| **合计** | 638,891 | **137.55 s** | 100% |

单次遍历明细（`C:\Users\zongxin`，暖缓存）：34.94s / 32.77s / 29.16s / 38.83s。

**命中结果：1 个文件。** 63.9 万条目的遍历换来 1 个候选，且第一个模式（`**/mempalace*/METADATA`）就已命中——**剩余 15 次遍历全部是无效功**，代码没有任何早退逻辑。

## 4. 因果分解：两个因子缺一不可

### 因子 1（必要条件）：16 次全树遍历 = 137.55s 的 I/O 工作量

即使一切正常，这段代码也要 137.55s。

### 因子 2（触发超时的条件）：冷缓存惩罚约 4.5×

| 测量项 | 暖缓存 | 首次冷遍历 | 倍率 |
|---|---:|---:|---:|
| `C:\Users\zongxin` 裸 scandir（548,728 项） | 12.5 s | ≥ 50 s（50s 上限时仅完成 89.7%，外推 ≈ 55.7 s） | **≈ 4.5×** |

事故发生时 RAM 94%（可用 0.39GB）、提交内存 15.2GB vs 物理 7.6GB，系统在重度换页。**页缓存无法驻留 → 每次遍历都退化为冷 I/O**，再叠加杀软实时扫描（用户已知基线：Python 冷启动约 16s）。

- 暖缓存：137.55s → **不会超时**
- 冷/颠簸：首趟即 ≈150s，后续趟次部分回暖 ≈ 34~50s，合计轻松越过 300s

**因此这不是纯粹的「内存问题」，也不是纯粹的「代码写得烂」——是两者叠加。** 单独任一因子都不足以致超时，这解释了为什么同类代码在内存宽裕时会「莫名其妙地又能跑通」。

### 331.67s 的构成（推断，置信度中）

| 段 | 估算 | 依据 |
|---|---:|---|
| `asyncio.wait_for` 墙钟超时 | 300.00 s | 配置 `tools.timeout.categories.code_execution=300` |
| 沙箱 Python 子进程冷启动 | ≈ 16 s | 用户既定基线（杀软扫描） |
| 代码扫描 + 脚本落盘 + 取消清理 + 失败采集 DB 写 | ≈ 15 s | 颠簸下的推断值 |
| **合计** | **≈ 331 s** | 与实测 331.67s 吻合 |

## 5. 三条被证伪的假设（同等重要）

排查中我先后提出三个假设，均被实验推翻。记录在此以免日后重复走弯路。

| 假设 | 验证方式 | 结果 |
|---|---|---|
| **A. 网络阻塞**：代码访问 PyPI/官网，被沙箱死代理 `http://127.0.0.1:9` 挂住 | 审读落库代码 | ❌ 代码零网络调用，`disable_network` 与本次无关。紧随其后 `http_request` 的 ConnectTimeout 是独立的境外网络问题 |
| **B. PYTHONPATH shim 放大开销**：沙箱继承 `PYTHONPATH=D:\workbuddy\...\vendor\shim`，`sitecustomize.py`(46KB) 拦截内建拖慢遍历 | A/B 对照各跑 3 轮（`pa_ce_ab.py`） | ❌ 无 shim 1.73/1.70/1.76s vs 有 shim 1.72/1.70/1.73s，差异 <4%（噪声内）。shim 只 patch `open`/删除类 API，不碰 `scandir` |
| **C. 超时留孤儿进程**：`asyncio.wait_for` 取消传播的是 `CancelledError`，executor 的 `except asyncio.TimeoutError`（L98）捕获不到，`process.terminate()` 走不到 | 实跑 `sleep(600)` + 取消，观察子进程 1/3/6/10s | ❌ 无残留。`finally` 中 `job.close()` 的 `KILL_ON_JOB_CLOSE` 兜底生效，取消耗时 0.00s。**该设计正确，勿改** |

### 假设 B 的一个真实副产品（非本次成因，但值得记）

`EnvSanitizer.sanitize()` 只过滤含 `KEY/SECRET/TOKEN/PASSWORD` 等敏感模式的变量，`PYTHONPATH` 不匹配任何一条 → **被原样透传给沙箱子进程**（`service.py` L145 `sanitize(dict(os.environ))`）。这意味着 WorkBuddy 的 safe-delete shim 会侵入用户沙箱的执行环境。本次证实无性能影响，但属于环境隔离缺口。

## 6. 次生发现

### 6.1 `cpu_timeout_sec` 命名误导（一个数字，两种语义）

同一个 `limits.cpu_timeout_sec = 300` 被用作：

| 用途 | 位置 | 语义 |
|---|---|---|
| Windows Job Object `PerJobUserTimeLimit` | `job.py` L144 | **CPU 时间** |
| `asyncio.wait_for(process.communicate(), timeout=...)` | `executor.py` L94 | **墙钟时间** |
| `asyncio.wait_for(handler(args), timeout=...)` | `react_loop.py` L1295 | **墙钟时间**（这一层先生效） |

对本次这类**纯 I/O 负载**，CPU 时间几乎为 0，Job Object 限制永不触发；真正杀死任务的是最外层的墙钟 `wait_for`。命名与实际语义不符，容易误导后续调参。

### 6.2 并行批次被最慢者拖累

`http_request`（超时 60s）几乎立刻 ConnectTimeout 失败，但它的 `tool_result` 与 `code_execution` **在同一时刻 09:29:22.704 才落库**——`asyncio.gather` 等到最慢的兄弟完成才统一产出。

后果：5 分半钟内，用户对两个工具都收不到任何可用结果。（心跳 `status` 事件仅走 WS、不落库，故 DB 中看不到中间过程。）

## 7. 修复建议（A 已实施 2026-09-03；B/C/D 待批准）

按 ROI 排序：

| 方案 | 做法 | 成本 | 覆盖面 | 判断 |
|---|---|---|---|---|
| **A（已实施 ✅ commit `b767ba2`）** | 在 `CODE_EXECUTION_TOOL.description` 中加一条硬约束：**禁止对 `~` / 项目根做 `glob(recursive=True)`；查包版本用 `importlib.metadata.version()` 或 `pip show`，禁止全盘 glob** | 改 1 处文本 | 覆盖绝大多数同类误用 | ✅ 成本最低，直击本次「读包版本元数据」这类高频场景 |
| **B（已实施 ✅ commit `70957ed`）** | `CodeScanner` 增加「递归遍历写法 + 超大目录起点」双条件阻断，且**命中即在执行前返回**（当前 `scan()` 已在执行前调用（service.py L140），但告警只在执行后拼进 output，来不及阻止） | 3 文件 +245 行 | 结构化拦截 | ✅ 复用现成机制，改造点明确 |
| **C** | 给沙箱加文件遍历预算（如 `sandbox.limits.max_tree_entries`） | 高 | 通用硬约束 | ⚠️ 需侵入 glob/ scandir，跨进程计数难做，性价比低 |
| **D** | 调高 `code_execution` 超时 | 1 行 | — | ❌ 治标；且会让用户多等 5 分钟才看到失败 |

**建议 A + B 组合**：A 从源头减少生成这类代码，B 在漏网时给出前置告警而非干等 300s。

### 7.1 方案 A 实施记录（2026-09-03 完成）

- **改动**：`backend/private_agent/tools/builtins/code_execution.py` —— `CODE_EXECUTION_TOOL.description` 追加「文件系统硬约束」段（共 755 字符）：
  1. 禁止以 `~` / 盘符根 / 项目根为起点做 `glob(..., recursive=True)`、`Path.rglob`、`os.walk`，遍历起点必须是预计条目 < 1 万的窄范围子目录；
  2. 查包版本一律 `importlib.metadata.version("pkg")` / `.metadata("pkg")`（O(1)），禁止用 glob 搜 `METADATA` / `PKG-INFO` / `version.py`；
  3. 定位文件优先精确路径 + `os.path.exists`；确需搜索只做一次浅层匹配且命中即 `break`。
- **测试**：`backend/tests/test_code_execution_tool.py::test_description_forbids_full_tree_glob`（TDD red→green），断言描述中必须含 `recursive=True` / `importlib.metadata` / `~` 三个关键字，防止约束被后续编辑悄悄删掉。
- **端到端取证**：用 `ToolRegistry` + `register_all_builtins` 导出 openai schema，确认 `code_execution` 的 description **755 字符完整传递**。关键结论：`schema_adapter.py` 的 1024 字符截断（`_DESC_MAX_LENGTH`）**只作用于 MCP 工具路径**（`mcp_tool_to_*`），`tooldef_to_mcp_tool` 与 `ToolDef.to_openai_schema()` 均无截断。
- **回归**：`tools` / `sandbox` 相关 7 个测试文件 **83 passed**。
- **正解实测对比**：`importlib.metadata.version()` 在本机 0.0013~0.0097s（未安装时 0.0013s 即返回明确答案），对比原全盘 glob 的 137.55s，加速约 1.4 万倍。
- **局限性（诚实交代）**：A 是**引导性**约束，不是强制拦截——若模型无视描述仍生成全树 glob，仍会超时。真正的硬拦截是方案 B（`CodeScanner` 执行前命中即阻断），**建议后续补 B**。

### 7.2 方案 B 实施记录（2026-09-03 完成，commit `70957ed`）

- **改动 1** `backend/private_agent/sandbox/security.py`：`CodeScanner` 新增两组标记。
  - `RECURSIVE_MARKERS`（遍历写法）：`recursive\s*=\s*True` / `os\.walk\s*\(` / `\.rglob\s*\(`。
  - `HUGE_ROOT_MARKERS`（超大目录起点）：`expanduser("~")` / `Path.home()` / `os.environ["HOME"|"USERPROFILE"]` / 盘符根 `"C:\\"` / POSIX 根 `"/"`。
  - 新增 `find_blocking_violation(code, language)`：**两条件同时命中才拦**，`language != "python"` 直接放行（JS 遍历语义不同，避免误伤），返回含行号与命中片段的 `CodeWarning`。
  - 新增 `TRAVERSAL_BLOCKED_MESSAGE`：拒绝时给出 3 条替代方案（O(1) 取包版本 / 精确路径校验 / 收窄起点 + 命中即 break），而不是单纯报错。
- **改动 2** `backend/private_agent/sandbox/service.py`：在「代码预扫描」之后、`executor.execute()` 之前插入阻断预检（步骤 3.1），命中即返回 `exit_code=-1` 且**不进入 executor**，阻断项一并记入 `warnings` 便于审计。
- **测试** `backend/tests/test_sandbox_traversal_block.py`（13 用例）：9 条阻断判定（含 session-76 事故代码原样回归）+ 4 条放行防误伤（窄目录递归 glob / 只读主目录下单个文件 / 无递归 / JS）+ service 层「`executor.execute` 一次都没被调用」的强证据 + 阻断耗时 <5s。
- **真实链路验证**（经 `code_execution_handler`，即模型实际触发路径）：

  | 场景 | 结果 |
  |---|---|
  | session-76 事故代码 | **0.02s 被拒**（原需 331.67s 才超时），定位到第 5 行 `expanduser("~")` |
  | 正常代码 `print('hello-ok')` | 0.75s 执行成功，无误伤 |

- **回归**：sandbox / code_execution / react_loop 相关 **121 passed**。
- **为什么是双条件而非单模式**：原建议的单一 `glob\.glob\([^)]*recursive\s*=\s*True` 会误伤合法的窄目录递归 glob（如 `glob.glob("src/**/*.py", recursive=True)`）。本次事故代码中起点是循环变量（`os.path.join(base, pat)`），静态无法确定大小，故改为「遍历写法 ∧ 超大目录标记」的双条件判据——牺牲少量漏网率换取零误伤已知合法用法。
- **已知残留漏网**：起点为变量且代码中不含任何 `expanduser`/`Path.home()`/`environ` 字面量时（如 `base = input()`）无法静态识别。此类场景由方案 A（描述约束）+ 300s 墙钟超时兜底。

> 注：本次任务的正确解法是 `importlib.metadata.version("mempalace")`（O(1)，直接读 `*.dist-info/METADATA`），模型选择了最暴力的全盘 glob。这属于**模型代码生成策略问题**，A 方案正是针对它。

## 8. 附：本次取证的可复用手法

1. **`tool_call` 事件落库了完整入参**（`react_events.payload.arguments.code`）。任何 code_execution 相关排查，第一手证据在这里，不要靠猜。库名 `private_agent`，表 `react_events`。
2. **判断「慢」还是「挂」**：比累计 CPU 时间 / 墙钟时间。本次代码是 CPU 接近 0 的纯 I/O，与 8-31 那次 pytest 挂起（CPU 45.6s / 墙钟 66.9min）形态不同。
3. **对照实验排伪**：PYTHONPATH shim 那条假设，若不做 A/B 就会变成又一条错误结论写进记忆。
4. **数据库时间戳字面量比较**：`created_at >= '2026-08-30 09:23:51'` 因时区解释返回 0 行，改用 `id BETWEEN` 区间规避。
