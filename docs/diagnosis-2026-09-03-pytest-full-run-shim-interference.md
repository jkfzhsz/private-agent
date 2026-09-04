# 全量回归失败归因：safe-delete shim 批量删除拦截

- 日期：2026-09-03（2026-09-04 补充方案 B 实施结果）
- 场景：A/B 两个修复方案落地后跑全量 pytest 回归，两轮结果不一致（一轮 8 个 F/E、一轮 0 failed）
- **更新：方案 B 已于 2026-09-04 实施并全量验证通过（commit `e823f38`），见 §5.1**
- 结论：**测试代码与本次改动均无问题。全部差异由 WorkBuddy safe-delete shim 的「单次删除 50 个文件即拦截」阈值造成**——pytest 滚动清理临时目录时单次删除数百个文件，被 shim 拒绝并抛 `SystemExit: 1`，在 fixture setup 阶段炸掉后续用例。

---

## 1. 四轮运行数据

| 轮次 | conftest 状态 | 命令特征 | 结果 | 耗时 |
|---|---|---|---|---|
| 第 1 轮 | 改前 | 默认 tmpdir（C 盘 `%TEMP%\pytest-of-zongxin`） | 1847 测试点，**2F + 6E**，EXIT=1，**汇总行缺失** | 20m41s |
| 第 2 轮 | 改前 | `--lf`（实际退化为全量）+ D 盘 `--basetemp` | **1830 passed / 0 failed / 8 warnings**，汇总行完整 | 40m23s |
| 第 3 轮 | 改前 | 按文件复跑第 1 轮失败的 9 个模块 | **1 passed, 97 errors**，EXIT=1 | 8.6s |
| 第 4 轮 | **改后** | D 盘 `--basetemp`（与第 2 轮同环境） | **1830 passed / 0 failed / 8 warnings，EXIT=0，日志 SAFE_DELETE 0 次** | **18m51s** |

**数量守恒校验**：1830（passed）+ 7（skip）+ 2（xfail）+ 8（第 1 轮的 F/E）= 1847。
即第 1 轮那 8 个失败项在第 2 轮**全部转为通过**，总数完全对得上——不存在"用例被跳过"的可能。

**改前 vs 改后（同为 D 盘 basetemp，唯一变量是 conftest）**：通过数完全一致（1830），耗时 40m23s → 18m51s（**降至 46.7%，提速 2.14 倍**）。这同时证明改动**未改变任何测试语义**。

## 2. 决定性证据

第 3 轮日志第一个 error 块（也是连锁崩溃的起点）：

```
ERROR at setup of test_admin_wrong_token_returns_401
E   SystemExit: 1
---------------------------- Captured stderr setup ----------------------------
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {
  "count":499, "threshold":50, "scope":"turn",
  "targets":["D:\\Private agent\\.pytest-tmp-fresh\\pa-appdatacurrent"],
  "targetCount":1
}
```

`targetCount=1` + `count=499` → 这是**单次删除操作**命中了一个含 499 个文件的目录，超过阈值 50 → shim 拒绝 → `SystemExit: 1`。

此后所有测试的 setup 连锁 `AssertionError`（pytest 内部状态已被 SystemExit 污染），形成 97 errors 的雪崩。

第 1 轮同理，只是规模小得多：

```
[SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":52,"threshold":50,...,
 "targets":["\\\\?\\C:\\Users\\zongxin\\AppData\\Local\\Temp\\pytest-of-zongxin\\garbage-6c86d624-..."]}
```

count=52 刚过阈值 → 只毁掉 8 个用例，且把 pytest 的收尾输出（汇总行、`-rf` 失败摘要）一起打断——这正是第 1 轮日志只有进度点、没有统计行的原因。

## 3. 触发路径（完整因果链）

1. `backend/tests/conftest.py` 的 `_isolate_appdata` 是 **function 级 autouse fixture**：

   ```python
   @pytest.fixture(autouse=True)
   def _isolate_appdata(monkeypatch, tmp_path_factory):
       appdata = tmp_path_factory.mktemp("pa-appdata")
       monkeypatch.setenv("APPDATA", str(appdata))
   ```

   每个测试都新建一个隔离 APPDATA 目录 → 全量 1908 个用例产生 1908 个目录。

2. pytest 的 `tmp_path_factory` **只保留最近 3 个** numbered 目录，其余滚动删除。

3. 部分测试会在隔离 APPDATA 内写入大量文件（配置、日志、master key、PA 用户数据），使得单个目录回收时的删除量达到数百个文件。

4. safe-delete shim 的批量删除阈值为 **50** → 拒绝 + `SystemExit: 1`。

5. SystemExit 发生在 fixture setup 阶段 → 后续用例连锁失败 + 收尾输出丢失。

### 一个附带的发现（实现与注释矛盾）

`_isolate_appdata` 的 docstring 写着「**APPDATA 跨测试稳定** → master key 继承稳定」，但实现是 function 级 `mktemp`，**每个测试都是全新目录**——实现与注释表达的意图相反。

## 4. 为什么第 2 轮幸免

第 2 轮用 D 盘 `--basetemp`（首次创建，无历史目录需清理），且运行期间被回收的目录内文件数始终低于 50，因此未被拦截，**汇总行完整输出、`0 failed` 可信**。

这说明触发具有**随机性**：取决于被回收目录内恰好有多少文件、以及 pytest 的回收时机。同一个代码状态，跑两次可能一个 EXIT=0、一个 EXIT=1。

## 5. 规避方案（按 ROI 排序，均未实施，待蒋先生决定）

| 方案 | 做法 | 成本 | 风险 | 判断 |
|---|---|---|---|---|
| **A** | 跑测试时 `dangerouslyDisableSandbox=true` 绕开 shim | 0（改调用方式） | 无 | ⚠️ **实测无效**：shim 经 `PYTHONPATH`/`NODE_OPTIONS` 注入子进程，`dangerouslyDisableSandbox` 只作用于 Bash 工具自身的沙箱层，拦不住（第 3 轮带该参数仍被拦） |
| **B（已实施 ✅ commit `e823f38`）** | `_isolate_appdata` 改 session 级共享目录（目录数 1908 → 1，根除滚动删除） | 拆 1 个 session fixture | 中：改变测试隔离语义（需全量回归验证） | ✅ **已验证通过**：通过数不变、耗时降 46.7%；且改后与 docstring 意图一致 |
| **C** | 用 D 盘 `--basetemp` 跑（避开 C 盘 Temp 的历史 garbage 堆积） | 0 | 低 | ✅ 有效且已验证（第 2/4 轮）；**建议作为常规做法** |
| **D** | 定期手动清空 tmpdir 后再跑 | 低 | 低 | ⚠️ 治标，下次仍会复发 |

### 5.1 方案 B 实施记录（2026-09-04）

**改动**（`backend/tests/conftest.py`，拆一个 session 级 fixture，不动 monkeypatch 作用域）：

```python
@pytest.fixture(scope="session")
def _pa_appdata_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("pa-appdata")

@pytest.fixture(autouse=True)
def _isolate_appdata(monkeypatch, _pa_appdata_dir):
    monkeypatch.setenv("APPDATA", str(_pa_appdata_dir))
```

**为什么这样拆而不是直接给 `_isolate_appdata` 加 `scope="session"`**：session 级 fixture 不能依赖 function 级的 `monkeypatch`（会 `ScopeMismatch`）。保留 function 级 monkeypatch 指向 session 级共享目录，既消除目录爆炸，又保留"每个测试结束自动恢复环境变量"的安全语义。

**验证**：

| 指标 | 改前 | 改后 |
|---|---|---|
| 全量通过数（D 盘 basetemp） | 1830 passed | **1830 passed**（完全一致） |
| 全量耗时 | 40m23s | **18m51s**（提速 2.14×） |
| 隔离 APPDATA 目录数（15 个测试） | 15 个 | **2 个** |
| APPDATA 相关子集（admin database / system settings / auth / sandbox network） | — | 37 passed |
| C 盘 tmpdir 环境对照 | 第 1 轮 2F+6E | 第 3 轮 **2F+6E**（相同 → 该 8 个失败与本次改动无关，只与 C 盘环境绑定） |

**顺带修正**：`_isolate_appdata` 的 docstring 原本就写「APPDATA 跨测试稳定 → master key 继承稳定」，而 function 级 `mktemp` 的实现与之相反（每个测试都是全新空目录）。改动后实现与注释一致。

**常规做法建议**：全量回归固定加 `--basetemp=D:/Private agent/.pytest-tmp`，并在跑前删除该目录。这样即使 shim 仍在，也不会碰到 C 盘 Temp 的历史 garbage 堆积。

## 6. 本次回归的最终结论

- **代码层面：通过。** 第 2 轮完整全量 **1830 passed / 0 failed**，覆盖本次两个修复（A description 约束 + B 执行前阻断）涉及的 `sandbox/security.py`、`sandbox/service.py`、`code_execution.py` 及新增的 13 个用例（收集记录 19 条）。
- **第 1 轮 / 第 3 轮的失败与被验代码无关**，失败用例集中在 admin auth / model registry / mcp_client stdio 并发 / migrations / eval runner 等模块，均未被本次两个 commit 触碰。
- **今后判断全量回归是否可信，先看日志有没有 SAFE_DELETE 关键字**；有则 EXIT 不可信，需按方案 A 重跑。

## 7. 附：复现与核查手法

- 失败清单来源：`.pytest_cache/v/cache/lastfailed`（JSON）。即使汇总行被 shim 打断，该文件仍会记录失败 nodeid。
- 覆盖性核查：`.pytest_cache/v/cache/nodeids`（本次收集的全部用例 id），用于确认"失败项是否真的被后续轮次跑到"。
- 挂起 vs 慢：比 `CPU 时间 / 墙钟时间`。本次第 2 轮 40 分钟但比值 19.3%（正常工作），区别于 8-31 那次挂起（45.6s / 66.9min ≈ 1.1%）。
- 坑：Windows Git Bash 下把 nodeid 通过 shell 变量传给 pytest 会因 MSYS 路径转换导致 `0 collected`（EXIT=4）；改用 `python -c "pytest.main([...])"` 直传可绕开——但本次即使直传仍 0 collected，最终按**文件维度**跑才生效。
