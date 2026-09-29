# 模型接入层整体优化方案（2026-09-29）

> 触发背景：2026-09-29 发图轮"全部模型调用失败"事故（见
> `diagnosis-2026-09-29-vision-chain-empty.md`）。该事故的直接原因是 vision 链
> 悬空引用，但**它只是一类更普遍问题的首个显性病例** —— 模型迭代快、key 更迭频繁，
> 而接入层的"配置一致性"与"默认值"仍有人工维护的硬编码缺口。
>
> 本文为设计方案，**未实施**，待蒋先生批准后按批次推进。

---

## 一、现状盘点（事实，均有代码位置）

### 1.1 已经做对的部分（不要在重构中破坏）

| 能力 | 实现 | 位置 |
|---|---|---|
| 任意 provider 免代码接入 | `ensure_registered` 未注册名自动走 `OpenAICompatibleAdapter` | `models/registry.py:27-37` |
| 端点/模型名运行时配置 | `base_url` / `model_name` 存 config_runtime | `api/admin.py:2681-2684` |
| 配置分层 | `config.yaml` ← `config_runtime` 点分覆盖 | `config/loader.py:61-121` |
| key 加密存储 | AES-256-GCM，`api_key_encrypted` | `api/admin.py:2733` |
| 出网不被系统代理劫持 | `trust_env=False` 硬隔离 | `adapters/__init__.py:103-105` |

**结论：provider 的"接入协议"层面已基本去硬编码 —— 模型名与 base_url 都不需要改代码。**
问题集中在**链路一致性、默认值、密钥权威源、路径/协议扩展**四处。

### 1.2 缺口清单

| # | 缺口 | 证据 | 影响 |
|---|---|---|---|
| **G1** | 三条链只维护一条 | `delete_provider` 仅清理 `fallback_chain`（`admin.py:2875-2888`）；`update_provider` 的 `enabled` 分支**完全不碰链**（`admin.py:2685-2686`） | **本次事故根因**：禁用 glm-vision 后 `vision_chain` 悬空 |
| **G2** | `text_chain` / `vision_chain` 无管理入口 | `grep text_chain\|vision_chain api/admin.py` → **零匹配**；前端仅 `fallback-chain`（`SettingsView.tsx:644,2477`） | 两条链是"配置黑盒"，写下即冻结，出错无 UI 可修 |
| **G3** | 链自愈不覆盖禁用态 | `update_fallback_chain` 的 `valid_names` 只判 `not p.get("deleted")`（`admin.py:2911-2914`），不判 `enabled` | 即使走 UI 也无法把 `enabled=false` 的 provider 从链中剔除 |
| **G4** | 硬编码默认模型名 ×2 | `admin.py:6344` 兜底 `"deepseek-flash"`（且读的是 `models.fallback_chain` —— **路径本身是错的**，实际在 `models.router.fallback_chain`，故该兜底**恒定命中**）；`tools/builtins/eval_runner.py:119` 兜底 `"deepseek-v4-flash"` | 换模型/换厂商后，这两条路径仍指向旧模型 → 静默失败或走错 provider |
| **G5** | API 路径写死 | `url = f"{self.base_url}/chat/completions"`（`adapters/__init__.py:121,190`） | 非标准路径的兼容网关（部分中转/私有部署）无法接入；无 `extra_headers` / `extra_body` |
| **G6** | key 有多个权威源 | `.env` 的 `PA_*_API_KEY` + config_runtime 密文 + 进程内 `os.environ`（`admin.py:2736`、`main.py:2430`） | 已知"双钥匙陷阱"：改一处不改另一处 → 静默 401 |
| **G7** | 能力声明与真实装配脱钩 | `main.py:796` 原只看 `multimodal` 不看 `enabled`（**已修 A-3**） | 同类别风险仍在：`context_window` 等能力字段无一致性校验 |

---

## 二、设计目标

| 目标 | 可验证判据 |
|---|---|
| **T1 配置自愈** | 任何 provider 的增/删/禁/启，三条链自动保持引用有效；启动期自动修复脏数据并告警 |
| **T2 零硬编码** | 源码中不出现任何具体模型名作为**运行期默认值**（仅允许出现在注释/示例/测试） |
| **T3 换厂商零改码** | 接入一个全新 OpenAI 兼容服务 = 设置页填 4 个字段（name / base_url / model_name / api_key），可选覆盖 path 与 headers |
| **T4 单一密钥权威源** | key 只在**一个地方**改，改完即热生效，且能一键验证有效性 |
| **T5 能力不谎报** | 系统提示中的能力声明**由链解析结果生成**，绝不扫描未启用的 provider |

---

## 三、方案设计（P1–P5，按优先级排序）

### P1 · 模型链一致性治理（解决 G1/G2/G3）—— 最高优先级

**核心：把"链"从散落的三处 config_runtime key 升级为一等公民。**

**P1-1 统一链维护入口（后端）**

新增 `_sync_chains(conn, cfg, name, action)`，在 provider 的 `create` /
`update` / `delete` 三处统一调用，`action ∈ {add, enable, disable, delete}`：

| action | fallback_chain | text_chain | vision_chain |
|---|---|---|---|
| add / enable | 追加尾部 | 若 provider 非多模态 → 追加尾部 | 若 provider 多模态 → 追加尾部 |
| disable | 剔除 | 剔除 | 剔除 |
| delete | 剔除 | 剔除 | 剔除 |

> 语义说明：`text_chain` 是"纯文本优先"链，`vision_chain` 是"多模态优先"链。
> 多模态 provider 是否进 text_chain 需产品决策 —— **建议：不进**（保持语义单纯，
> 多模态模型留给 vision 链），避免"文本轮误用视觉模型"。

**P1-2 启动期一致性校验与自愈**

在配置加载后（`load_config_with_overrides` 之后、Sidecar ready 之前）执行：

```
for chain_name in ("text_chain", "vision_chain", "fallback_chain"):
    refs = cfg.models.router[chain_name]
    for r in refs:
        if r 不存在 or deleted or not enabled:
            标记为悬空
if 悬空项非空:
    写回 config_runtime（剔除悬空项）
    logger.warning("chain {name} 悬空引用已自愈: {dropped}")
    落 react_events(session_id=0, event_type="config_selfheal")
    # 若某条链被清空（如 vision_chain 移除唯一 provider）→ 额外明确告警
```

**关键：这一步是本次事故的"制度性解药"** —— 即使未来忘记清理链，启动即修复。

**P1-3 前端补齐链管理入口（解决 G2）**

设置页新增"模型链"面板（可折叠，不堆悬浮图标）：
- 三条链并列展示，每条链显示当前 provider 顺序 + 拖拽排序
- **悬空项红标 + "一键修复"按钮**（调 P1-2 同一后端逻辑）
- `vision_chain` 为空时明确提示"当前无法识别图片上传"

**P1-4 治理 `update_fallback_chain` 的过滤条件（G3）**

`valid_names` 增加 `enabled` 判断；但**保留"显式加入已禁用 provider"的能力**需权衡 ——
建议**禁止**（已禁用的 provider 入链毫无意义，只会造成 G1 同类隐患）。

### P2 · 零硬编码默认值（解决 G4）

| 位置 | 现状 | 改法 |
|---|---|---|
| `admin.py:6344` | 读 `models.fallback_chain`（**错误路径**）→ 恒为空 → 兜底 `"deepseek-flash"` | 改读 `models.router.fallback_chain`；仍为空则**返回 None 并跳过该功能**（技能元数据翻译降级），**不设默认模型** |
| `eval_runner.py:119` | `model_id="deepseek-v4-flash"` | 从 cfg 解析（`models.eval_model` 配置项，未配置则回退 `fallback_chain[0]`，均为空则报明确错误） |

**P2-2 守护测试**：新增 `tests/test_no_hardcoded_models.py`
- 正则扫描 `private_agent/**/*.py`（排除注释与 docstring）中的模型名模式
- 断言不出现**运行期默认值**形式的模型名
- 已知例外（示例字符串）走白名单

### P3 · API 端点与协议灵活性（解决 G5）

**P3-1 provider 级可选项**（全部有默认值，零回归）：

| 新字段 | 默认 | 用途 |
|---|---|---|
| `chat_path` | `/chat/completions` | 兼容非标准路径（私有部署/中转网关） |
| `models_path` | `/models` | 能力探测（P5）用 |
| `extra_headers` | `{}` | 需要额外鉴权头的服务（如某些厂商的 `X-Api-Key`） |
| `extra_body` | `{}` | 强制注入的请求体字段 |
| `api_style` | `openai` | 预留：未来支持非 OpenAI 协议时区分 |

实现点：`OpenAICompatibleAdapter.__init__` 增加上述参数；`chat` / `chat_stream`
的 `url` 与 `headers` / `body` 由配置驱动，默认值与当前完全一致。

**P3-2 端点健康检查**

`POST /settings/providers/{name}/test` 已存在（`admin.py:2946`），建议增强：
返回结构化结果（连通性 / 模型名有效性 / 能力探测结果），供 P5 复用。

### P4 · 密钥单一权威源（解决 G6）

**P4-1 明确权威源与优先级**

```
权威源：config_runtime.models.providers.{name}.api_key_encrypted（设置页录入）
优先级：config_runtime 密文  >  进程环境变量 PA_{NAME}_API_KEY  >  无（明确报错，不用 test-key 兜底）
```

现状是环境变量优先（`admin.py:2956-2959` 读 `os.environ`），与"UI 录入为权威"矛盾。
建议改为**密文优先**，环境变量仅作无 UI 场景（dev/脚本）的引导。

**P4-2 key 轮换是一等操作**

设置页"轮换密钥"：一次操作完成
1. AES 加密写 config_runtime
2. 进程内热更新 `os.environ`
3. 自动调一次连通性测试（P3-2）
4. 结果落库（`key_rotated_at` + `last_test_ok`，**不记明文**）

**P4-3 消除双钥匙陷阱**

`.env` 的 `PA_MASTER_KEY` 与 `%APPDATA%/Private Agent/backend.env` 不一致会静默 401
（历史已踩）。建议：启动时若两处 master key 同时存在且不一致 → **明确 ERROR 并在
健康检查暴露**，而不是静默走其中一个。

### P5 · 能力声明与真实装配一致（解决 G7）

**P5-1 能力声明改由链解析结果生成**

现状（A-3 已修 enabled 条件）仍依赖"扫描 providers 字典"。更稳的做法：
由 `build_fallback_chain(cfg, "vision_chain")` 的实际结果决定：

```python
vision_capable = len(vision_chain._adapters) > 0   # 已有 has_vision 属性可用
```

即：**声明什么能力，就看链里真的有什么**。彻底杜绝"配置字典与链不一致"导致的谎报。

**P5-2 能力探测（可选，降低手工填错）**

新增 provider 时可选调用 `{base_url}{models_path}`：
- 校验模型名是否在服务端列表中
- 探测失败不阻断保存，仅在 UI 标注"未验证"

---

## 四、批次划分与依赖

| 批次 | 内容 | 依赖 | 预估改动面 |
|---|---|---|---|
| **批次 1（止血+制度）** | P1-1、P1-2、P1-4 | 无 | `admin.py` + 新 `chain_guard.py` + 启动钩子 + 测试 |
| **批次 2（可见可修）** | P1-3、P2、P5-1 | 批次 1 | 前端 SettingsView + 后端 2 处默认值 + 测试 |
| **批次 3（灵活接入）** | P3、P4 | 无（可与批次 2 并行） | `adapters/__init__.py` + `admin.py` + 前端表单 |
| **批次 4（可选增强）** | P5-2 | 批次 3 | 探测端点 + UI 标注 |

**建议：批次 1 与本方案批准后立即做**（本次事故的制度性闭环）；
批次 2/3 可合并为一轮迭代。

---

## 五、与已实施修复的关系

| 已实施（2026-09-29，方案 A） | 本方案对应 | 关系 |
|---|---|---|
| A-2 `react_loop.py:735` 判定改"存在且非空" | P1-2 | A-2 是**消费端兜底**（不崩、给人话提示）；P1-2 是**源头治理**（不让悬空产生）。两者互补 |
| A-3 `main.py:796` 能力声明补 `enabled` | P5-1 | A-3 是 P5-1 的最小实现；P5-1 把它升级为"看链不看字典" |

**A 已闭合"崩溃"与"谎报"两个症状；本方案解决其成因而非症状。**

---

## 六、验收清单（整体）

- [ ] 禁用/删除任一 provider 后，三条链均无悬空引用（自动化用例覆盖）
- [ ] 手工往链里塞入已禁用 provider → 重启后自动剔除并告警
- [ ] 源码扫描：无运行期默认模型名硬编码
- [ ] 接入一个全新厂商只需设置页填 4 字段，零代码改动（实测一个非标准 path 的服务）
- [ ] key 只在一处更新，轮换后一键验证通过
- [ ] `vision_chain` 为空时，前端明确提示且系统提示不声明视觉能力
- [ ] 回归：后端 pytest 全量（需 PA 停机）+ 前端 tsc 0 错 + vitest 全过

---

## 七、我的倾向与待决问题

**倾向**：
1. **批次 1 优先做**，它是本次事故的制度性解药，且改动集中在后端、风险可控。
2. **P5-1 一并做**（成本极低，`has_vision` 属性已存在，只是把判定源换成链）。
3. **P2 的守护测试值得单独做** —— 硬编码是"会复发的病"，靠测试守比靠人记可靠。
4. **P4-1 的优先级反转是破坏性变更**，需蒋先生确认（会改变现有 `.env` 优先的行为）。

**待蒋先生决策**（**已于 2026-09-29 裁定，结论见 §八**）：
- **D1**：多模态 provider 是否同时进 `text_chain`？（我建议：**不进**，保持语义单纯）
- **D2**：key 优先级是否反转为"config_runtime 密文优先"？（会改变现有 `.env` 行为）
- **D3**：批次 2 与批次 3 是否合并为一轮迭代？

---

## 八、决策记录（2026-09-29 蒋先生裁定）

| 编号 | 决策 | 影响 |
|---|---|---|
| **D1** | 多模态 provider **按需进 text_chain**：协助文本模型完成特定识别任务后退出，主对话仍由文本模型主导 | 见 §8.1（**推翻我在 §七 的"不进"建议**） |
| **D2** | key 优先级 **不反转**（保持现状：环境变量优先） | **P4-1 取消**；P4-2/P4-3 保留 |
| **D3** | 批次 2（可见可修）与批次 3（灵活接入）**合并为一轮迭代** | 见 §8.2 |
| 附带 | 批准修复 `chat_stream` 的 `continue` 缺陷 | 已实施，见 §8.3 |

### 8.1 D1 的实现含义

"按需进 text_chain，用完退出，主对话仍由文本模型主导"**不需要在运行时增删链成员** ——
它恰好是 `FallbackChain.require_vision` 的既有语义：

| 轮次类型 | `_messages_contain_image` | 链上行为 | 语义 |
|---|---|---|---|
| 发图轮 | True | 跳过纯文本模型，从多模态 provider 开始 | **按需进入** |
| 后续纯文本轮 | False（只看最后一条 user 消息） | 从**链首**文本模型开始 | **任务完成后退出**，文本模型主导 |

因此落地方式为：
- 多模态 provider **追加在 text_chain 尾部**（不占链首）—— 常驻即可，无需动态改链；
- `chain_guard._chain_should_contain` 对 `text_chain` **一律返回 True**（不剔除多模态），
  语义排他性只保留给 `vision_chain`；
- **全程无状态**：不写链、不引入并发风险。

> 修正记录：初次实现（`edfc4aa`）把"多模态不进 text_chain"作为默认，
> 与 D1 冲突，已按 D1 改为"一律可入 + 尾部追加"。测试同步更新为
> `test_enable_multimodal_appends_to_text_chain_tail`。

### 8.2 合并后的迭代范围（批次 2 + 3）

| 项 | 内容 |
|---|---|
| P1-3 | 前端"模型链"管理面板（三链可视化 + 悬空项红标 + 一键修复） |
| P2 | 零硬编码：修 `admin.py:6344` 路径 bug + `eval_runner.py:119` 默认值 + 守护测试 |
| P3 | API 灵活性：`chat_path` / `extra_headers` / `extra_body`（默认值零回归） |
| P4 | 密钥管理（**P4-1 优先级反转取消**；保留 P4-2 轮换为一等操作、P4-3 双钥匙显式告警） |
| P5-1 | 能力声明改为"看链不看字典"（复用 `FallbackChain.has_vision`） |

### 8.3 附带修复：`chat_stream` 同一 provider 被调用两次

`models/base.py` 的 `FallbackChain.chat_stream` 在流式失败 `break` 后**缺少 `continue`**，
控制流落入下方"无流式能力兜底"分支 → 同一 provider 被调用两次
（`chat_stream` 失败 → `chat` 再失败），`failed_providers` 出现重复项。

- **判据**：错误串中 provider 名重复 —— `['step-3.7-flash','step-3.7-flash']`（09-29 实测）；
  `['glm-5.2','glm-5.2']`（09-08）同源。
- **危害**：认证类 401/403 本不可重试却白跑一次网络往返；失败列表重复**误导排查**
  （看起来像配了两个 provider）。
- **修复**：`break` 后补 `continue`。新增 3 项回归测试（不可重试不兜底 / 重试耗尽不兜底 /
  失败后正常降级到下一个 provider）。

---

**取证方式**：源码静态阅读（`registry.py` / `adapters/__init__.py` / `admin.py` /
`loader.py` / `main.py` / `eval_runner.py`）+ 前端 grep + config_runtime 只读查询。
本文档初稿为设计稿；实施结果见 §九。

---

## 九、实施记录

| 批次 | 状态 | commit |
|---|---|---|
| 方案 A（事故止血：空链判定 + 能力声明） | ✅ 已实施 | `e27590e` |
| 批次 1（P1-1 链统一维护 / P1-2 启动自愈 / P1-4 自愈条件） | ✅ 已实施 | `edfc4aa` |
| 附带修复（`chat_stream` 重复调用） | ✅ 已实施 | `a0cba4f` |
| D1 修正（text_chain 按需容纳多模态） | ✅ 已实施 | `27fd702` |
| 批次 2+3 后端（P1-3 端点 / P2 / P3 / P4 / P5-1） | ✅ 已实施 | `ae76a8f` |
| 批次 2+3 前端（P1-3 链管理面板） | ✅ 已实施 | `cecf1ec` |
| P3/P4 前端表单入口 | ✅ 已实施 | `9291059` |

### 9.1 新增端点一览

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/admin/settings/chains` | 三链视图（`dangling` 标注 + `vision_capable`） |
| PUT | `/admin/settings/chains/{chain_name}` | 设置链顺序（自愈剔除 + `dropped` 回报） |
| POST | `/admin/settings/chains/repair` | 一键修复悬空引用 |
| POST | `/admin/settings/providers/{name}/rotate-key` | 密钥轮换（加密落库 + 热更新 + 验证 + 审计） |

### 9.2 新增 provider 配置字段（全部可选，缺省零回归）

| 字段 | 默认 | 用途 |
|---|---|---|
| `chat_path` | `/chat/completions` | 非标准路径的兼容网关（私有部署/中转） |
| `extra_headers` | `{}` | 额外鉴权头 |
| `extra_body` | `{}` | 厂商特有请求参数 |

### 9.3 关键实现约定（勿破坏）

1. `text_chain` / `vision_chain` **未配置则不创建**（保持"回退 fallback_chain"
   语义）；`fallback_chain` 作为 `build_fallback_chain` 的默认链**必须有基线**。
2. `audit_chains` 只剔无效项、**不做语义归位**（语义归位仅由 `sync` 在 provider
   状态变更时触发），避免启动时擅自改写用户既定链路。
3. 链管理端点的 PUT **不自动追加成员**（text/vision），仅 `fallback_chain` 保留
   既有"补齐到尾部"语义 —— 否则多模态 provider 会占据 text_chain 链首（违背 D1）。
4. `vision_capable` / 系统提示的能力声明 / 发图轮的实际筛选**三者同源**，都走
   `build_fallback_chain(cfg, "vision_chain").has_vision`。

### 9.4 验证基线

- 后端：21 个测试文件 **169 passed / 0 failed**（239.12s）
- 前端：`tsc -p tsconfig.json --noEmit` **0 错**；`vitest run` **14 files / 96 tests passed**
- 全量 pytest 未跑（需 PA 停机）
- 零硬编码守护：`tests/test_no_hardcoded_models.py`（AST 扫描运行期字符串常量 +
  扫描器自证用例，防假绿）

### 9.5 遗留

- ~~**P3/P4 前端表单入口**~~ ✅ **已实施**（`9291059`）：`ProviderRow` 新增
  "高级 → 端点与请求参数"（**默认收起**）与密钥轮换入口（填新 Key 即走
  `rotate-key`，可勾选"保存时验证"，并回显上次轮换时间与验证结果）。
- **`step-3.7-flash` 凭据失效**（非代码问题）：它是当前唯一多模态 provider，
  故发图轮必然 401 —— 需更新其 key 或接入新的视觉 provider。

### 9.6 待办（下一轮可选）

- **P5-2 能力探测**：新增 provider 时调 `{base_url}{models_path}` 校验模型名是否
  存在，降低手工填错概率（当前 `chat_path` 已可配，但无连通性预检）。
- **P2 守护测试的白名单**：目前仅有 `# model-name-ok` 行内豁免；若将来需在代码里
  合法写入模型名（示例/提示语），可按需扩展为模块级白名单。
