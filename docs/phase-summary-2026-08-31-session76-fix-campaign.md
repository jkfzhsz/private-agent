# session-76 崩溃修复战役总结

> 日期：2026-08-31 · 记录：2026-09-03
> 范围：mempalace 3.8.0 接入 PA 后「无涯识别成功、后续任务连催两次零输出」的根因排查与修复
> 产出：**5 个原子 commit · 3 个缺陷修复 · 1 份诊断报告 · 12 个新增测试函数（26 个 pytest 用例）**

---

## 一、结论先行

| 问题 | 结论 |
|---|---|
| mempalace 3.8.0 升级本身 | ✅ **成功**。45 工具（升级前 36）、354 drawer、SQLite `ok` / error_count=0 |
| 无涯识别 mempalace | ✅ **正常**。`mcp_browse list` 全量索引 + `mcp__mempalace__list_drawers` 实测返回 245 条 |
| 后续任务失败 | ❌ **PA 后端两个缺陷叠加导致整轮 turn 崩溃**，升级只是把它撞出来了 |

**根因不是升级，是代码欠防御。** 升级新增了返回 JSON 数组的工具（`event_list` / `find_tunnels` / `search` 等），而 PA 侧有一条只防「非法 JSON」、不防「合法但非 dict」的解析路径。

---

## 二、根因（代码级）

### 2.1 异常产生点 — `react_loop._parse_assembly_marker`（修复前 L63-77）

```python
try:
    data = json.loads(output)
except Exception:
    return None
marker = data.get("__mcp_assembly")   # ← 在 try 外：data 可为 list/str/int/float/bool
```

`json.loads` 只保证「是合法 JSON」，不保证是 dict。解析出 `list` 就抛 `AttributeError`，且 **except 兜不住**。

**触发路径唯一**：`mcp_browse action=exec`。
- `list` → 纯文本（安全）
- `assemble` / `remove` → dict（安全）
- **`exec` → 返回值由被调用的 MCP server 决定，完全不可控**

B 任务正好要测 mempalace 3.8.0 新增的这批返回数组的工具 → 必崩。

### 2.2 异常放大器 — `run_turn` L1449 `asyncio.gather`

修复前未传 `return_exceptions=True` → 单个工具解析异常炸掉整轮，**其他已成功工具的成果全部丢弃**，前端零回复。这就是「连催两次仍无反应」的直接原因。

### 2.3 硬证据

| 证据 | 内容 |
|---|---|
| 生产日志 | `backend/logs/agent.log` 中 session=76 三次崩溃，栈完全一致；最后一次 `00:29:18Z` 紧贴 session 导出 `00:32:09Z` |
| 本机最小复现 | 零改动实跑：数组 / 裸数组 / 字符串标量 / 数字 **4 类输入全部 CRASH**，与生产日志逐字吻合 |

---

## 三、修复清单

| # | commit | 改动 | 位置 | 风险 |
|---|---|---|---|---|
| A-1 | `995b7a0` | `json.loads` 后加 `isinstance(data, dict)` 闸门，非 dict 一律返回 None | `react_loop.py` L63-90 | 低 |
| A-2 | `689724d` | 并行工具 `gather(..., return_exceptions=True)`，异常降级为 `ToolResult(error=...)` | `react_loop.py` L1449 | 低 |
| B | `2474995` | `mcp_server_list` / `mcp_server_add` 改读合并配置 | `mcp_config_manager.py` | 低 |
| — | `bfc2ce9` | 诊断报告（230 行） | `docs/diagnosis-2026-08-31-mempalace-turn-crash.md` | — |
| C-1 | `2644229` | startup mission cleanup 自行 `acquire()` 连接 | `main.py` L2148 | 极低 |

代码变更合计：**+7 / -1 至 +29 / -3 量级，5 个文件，未触及任何成功路径语义**。

### 三处关键实现细节（易踩坑，记录下来）

1. **`CancelledError` 不得降级**。若把它一并转成 `ToolResult`，轮次会继续跑，**idle 超时与用户中断机制会被吞掉**——300s 超时正是靠取消实现的。代码中显式 `raise out` 向上传播。
2. **`mcp_server_add` 只构造不落库**。初判担心的「写入 config.yaml 与运行时分叉」经读码**证伪**：L146-157 注释明确「仅返回待写入条目，由 apply_optim 或用户手动落库」。风险由「中」降为「低」，两处一并修改。
3. **B 还揪出一个此前未识别的缺陷**：`mcp_server_add` 的**重复 id 检测形同虚设**（检测源是恒空的 yaml），可构造出与已有 server 同名的条目。

---

## 四、验证

### 4.1 新增测试

| 文件 | 函数 | pytest 用例 | 结果 |
|---|---|---|---|
| `tests/test_react_loop_assembly_marker.py` | 5 | 19 | ✅ 全绿 |
| `tests/test_react_loop_parallel.py` | +1（隔离用例） | +1 | ✅ 全绿 |
| `tests/test_mcp_config_manager.py` | 6 | 6 | ✅ 全绿 |
| **合计** | **12** | **26** | |

### 4.2 Red 验证（防「测试是假的」）

每处修复都做了**还原旧实现 → 跑同一批测试 → 期望失败**的反向验证：

| 项 | 修复前 | 修复后 |
|---|---|---|
| A-1 | 复刻旧实现喂 8 个用例 → **8/8 抛 AttributeError** | 8/8 安全返回 None |
| A-2 | `gather` 无 `return_exceptions` → 抛 RuntimeError 且 `good()` 成果被丢弃 | 异常隔离，成果保留 |
| B | 旧实现下 **3 failed**，其中一条实锤输出 `MCP server mempalace 配置已构造(待落库)`（mempalace 明明已存在却放行） | 6 passed |

### 4.3 回归

| 轮次 | 范围 | 结果 |
|---|---|---|
| A 后子集 | react_loop 系 + mcp_tools，9 文件 | **101 passed** |
| A 后全量 | `pytest --ignore=test_eval_full_cycle.py` | **1810 passed / 0 failed**（26分59秒） |
| B 后子集 | mcp 系 11 文件 + admin 配置相关 | **92 passed** |
| B 后全量 | 同命令 | **⚠️ 挂起，已终止**（见第六节） |

**A 全量基线的交叉验证价值高于「全过」本身**：旧基线 1790 + 本次新增 20（19+1） = **1810，精确吻合**。既没破坏存量测试，也没有测试被意外跳过或静默失效。

### 4.4 C-1 的真实链路验证

C-1 按蒋先生要求**未跑回归**。验证改走真实路径：8-31 11:07 打开 PA 时的启动日志

```
[INFO] DB schema migrated (idempotent)
[INFO] KB embedding consistency verified
```

**`mission cleanup failed at startup` 消失**（此前 8-30、8-31 两次启动必现）。这比跑 pytest 更有说服力——走的是用户实际触发的那条链路。

---

## 五、次生发现（未修复，仅记录）

| 级别 | 事项 | 状态 |
|---|---|---|
| P2 | **内存硬件瓶颈**：物理 7.60GB vs 提交 15.2GB（两倍），重度依赖页面文件换页 | **蒋先生裁决：硬件限制，软件优化无意义，不再推进** |
| P2 | `code_execution` 300s 超时归因未坐实：「读 Python 包版本元数据」耗时远超 16s 冷启动基线，疑为遍历 site-packages 或调 pip | 待单独归因 |
| P3 | safe-delete shim 拦截 pytest tmpdir 回收 → `Temp\pytest-of-zongxin` 堆积 106 目录 / 55MB | 已知噪声，EXIT=0 仍可信；清理需蒋先生确认 |
| — | 工作区仍有 8-28 遗留的未提交改动（ws 未知消息类型记日志），已刻意剥离、未混入本次提交 | 待蒋先生处置 |

---

## 六、过程中的失误与教训

### 6.1 违反了自己的铁律：PA 运行时跑全量 pytest

B 的全量回归跑到 66.9 分钟只产出 20 个测试点。**诊断结论：挂起，不是变慢。**

| 指标 | 数值 |
|---|---|
| 运行时长 | 66.9 分钟 |
| 累计 CPU 时间 | **45.6 秒** |
| 最近 5 秒 CPU 增量 | **0.00s** |
| RSS | 106MB → 43.8MB（工作集被换出到页面文件） |

排除了两个常见嫌疑：**不是数据库锁**（`pg_stat_activity` 无活动连接、无锁等待）、**不是死循环**（死循环会吃满 CPU，此为零 CPU）。定性为内存颠簸（thrashing）。

**已写入铁律**：全量 pytest 必须在 PA 桌面端未运行时跑，且跑的过程中不要开 PA。
**排查捷径**：判断「测试怎么这么慢」，先算 **累计 CPU 时间 / 墙钟时间**——挂起与变慢症状相似，处置方式完全不同。

### 6.2 记忆漂移：误提 Trae Code

曾脱口而出「要不要在 Trae Code 落盘」，蒋先生指出**本环境根本没有 Trae**。这是云端长期记忆里 8 月初的旧分工残留（8-15 已被明确推翻）。已在 `~/.workbuddy/MEMORY.md` 协作方式顶部写入压倒性条目。

### 6.3 采集脚本的两个坑（复用时注意）

1. 按 `'mempalace' in cmdline` 分类会**命中采集脚本自身**（脚本源码含该字符串），须排除自身 PID 或排除含 `psutil` 的命令行 —— 当天踩了两次。
2. PA 后端 cmdline 是 `-m private_agent.main`（**下划线**），按 `'private agent' in cl` 判不出，会被误分到「其他第三方」。

### 6.4 拆分同文件双 hunk 提交的手法

A-1 与 A-2 在同一文件、相隔 1400 行，`git add -p`（交互式）被禁。采用：**备份 → 脚本按上下文精确剥离其中一个 hunk → 提交 → `cp` 还原 → 提交**，规避 CRLF 转换与 patch 匹配问题。

两个衍生坑：
- `react_loop.py` 中 4 处 `_wrap_merged_mcp_cfg()` 调用有 2 处是 `mcp_browse` 原有的，简单 replace all 会误伤 —— 靠 `assert count == 1` 挡下。
- `main.py` 是 **CRLF 行尾**，脚本按 LF 匹配直接 `count=0`。git 提示的 "LF will be replaced by CRLF" 指下次 checkout 才转，工作副本当下就是 CRLF。

---

## 七、B 的验证充分性说明

B 改动仅 2 处、核心是切换配置读取源。现有验证：新增单测 6 passed、Red 验证 3 failed 坐实缺陷、子集回归 92 passed、A 的全量基线 1810 passed。

**判断：够了。** 为 2 行读取源切换再花 27 分钟全量，边际收益很低。若坚持要全量，须在 PA 关闭时前台跑（约 27 分钟，进度实时可见）。

---

## 八、方法论沉淀

本次战役可复用的四条：

1. **异常产生点与放大器分开定位**。单点缺陷（解析无防御）只是 bug，叠加放大器（gather 无隔离）才升级为「整轮崩溃、用户侧零输出」。修复要两处都做，只做前者仍会丢整轮成果。
2. **Red 验证不可省**。新增测试通过只能证明新代码没坏，不能证明旧代码是坏的。还原旧实现跑一遍，才能确认测试真的抓住了回归——本次三次 Red 全部实锤，其中 B 的一次直接打印出「mempalace 已存在却仍放行」的证据。
3. **先证伪风险再定级**。初判 B 有「写入分叉」风险、定级中、建议只改一半；读码后发现 `mcp_server_add` 根本不写盘，风险证伪，才敢整体修改。**读码成本远低于误判成本**。
4. **验证路径必须等于真实触发路径**。C-1 的验证靠「用户打开 PA 时的启动日志」，而非 pytest——后者可能绕过真实装配链路。

---

## 附：相关文件

| 文件 | 说明 |
|---|---|
| `docs/diagnosis-2026-08-31-mempalace-turn-crash.md` | 完整诊断报告（日志取证时间线、代码级根因、A/B/C 分期方案） |
| `backend/tests/test_react_loop_assembly_marker.py` | A-1 类型防御单测 |
| `backend/tests/test_react_loop_parallel.py` | A-2 并行异常隔离单测 |
| `backend/tests/test_mcp_config_manager.py` | B 配置读取源单测 |
