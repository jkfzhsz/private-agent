# 诊断报告：发图轮"全部模型调用失败"（2026-09-29）

## 一、结论

**根因：`vision_chain` 是一个"存在但为空"的链对象，代码用 `is not None` 判定，
判定通过后模型链被替换为空链 → 0 个候选 provider → 必然抛
`AllProvidersFailedError("all 0 providers failed: []")`。**

引发条件（两者同时成立）：

1. `config_runtime` 中 `models.providers.glm-vision.enabled = false`（2026-09-08 设置），
   而 `models.router.vision_chain` 仍指向 `["glm-vision"]` —— 悬空引用。
2. 用户消息**带图片**（`[已上传文件: *.jpg]` / `[用户粘贴图片: ...]`）→ 触发发图路径。

**与"PA 自我分析视觉能力"无因果关系。** 自我分析轮（10:24–10:26）走文本链，全部正常。
真实因果是：那次自我分析给出了"视觉能力存在 ✅"的**错误结论**（因系统提示声明与实际装配
不一致），蒋先生据此上传图片实测，才踩中了 09-08 就已埋下的配置缺陷。

## 二、证据链（全部为 DB / 代码事实）

### 2.1 事件流原始错误

```
react_events.id=717297  2026-09-29 10:53:28.546  session=90  turn=8
react_events.id=717300  2026-09-29 10:54:47.477  session=90  turn=9
payload: {"stage":"resilience",
          "message":"【程序异常】所有模型调用失败(全部)。最后错误:
                     all 0 providers failed: []。…"}
```

关键点：**`0 providers`，失败列表为 `[]`（空数组）**。
语义不是"逐个 provider 试过都失败"，而是"候选列表本身就是空集"。
两次错误分别在用户消息后 **50ms / 112ms** 触发 —— 无任何网络往返，属逻辑层立即判定。

### 2.2 两轮失败消息均带图片

```
messages.id=4907  session=90  turn=8 user  len=116
  [已上传文件: 微信图片_20260929091202_134_2.jpg 路径: D:\Private agent\backend\uploads\…]
  A，请用这张图片测试

messages.id=4910  session=90  turn=9 user  len=119
  [已上传文件: 微信图片_20260929091202_134_2.jpg 路径: …]
  A，请用这张图片来实测验证
```

对照同一会话的自我分析轮（turn 7，纯文本）：`final` + `token_usage` 正常落库，
`model_id=deepseek-flash`，input 41233 / output 916 tokens —— 文本链完全健康。

### 2.3 配置事实

| key | value | updated_at |
|---|---|---|
| `models.providers.glm-vision.enabled` | `false` | 2026-09-08 10:45:27 |
| `models.providers.glm-vision.deleted` | `true` | 2026-09-08 10:45:27 |
| `models.providers.glm-vision.multimodal` | `true` | 2026-08-14（未随禁用清理）|
| `models.router.vision_chain` | `["glm-vision"]` | 2026-08-14（未随禁用清理）|
| `models.router.text_chain` | `["deepseek-flash","deepseek-v4-flash","step-3.7-flash","glm-5.2","kimi-k2.7-code"]` | 2026-08-14 |
| `models.router.fallback_chain` | `["deepseek-flash","deepseek-v4-flash","step-3.7-flash"]` | 2026-09-08 |

会话 `sessions.id=90` 的 `model_id = "deepseek-flash"`（非 auto），
其 `capability.vision = false`（`multimodal=false`）→ 走 `vision = vision_chain` 分支。

### 2.4 代码缺陷定位

**① 链构建未过滤为空的结果** —— `backend/private_agent/models/registry.py:72-77`

```python
for name in chain_names:
    prov = providers.get(name, {})
    if not prov.get("enabled", True):
        continue                      # glm-vision 在此被跳过
    adapters.append(get_adapter(name, cfg))
return FallbackChain(adapters)        # adapters == [] → 返回空链（仍是非 None 对象）
```

**② 空链被误判为"可用"** —— `backend/private_agent/core/react_loop.py:727-728`

```python
if self._vision_adapter is not None:   # 空链对象不是 None → 判定通过！
    self._adapter = self._vision_adapter   # 模型链被替换成空链
```

**③ 优雅兜底分支不可达** —— `react_loop.py:750-776`

```python
else:
    # 兼容旧构造(无 vision_adapter): …仅当全链也无多模态才提示。
    chain_has_vision = getattr(self._adapter, "has_vision", None)
    if chain_has_vision is False:
        …
        msg_text = "当前模型链不支持图片识别(未配置多模态模型)。请先在设置页配置并启用多模态模型。"
```

该分支的入口条件是 `self._vision_adapter is None`。
由于 `_vision_adapter` 是**空链而非 None**，这条本应给出明确提示的路径**永远不会执行**。

**④ 空集抛错** —— `backend/private_agent/models/base.py:178-184, 216-219`

```python
if require_vision:
    vision_adapters = [a for a in self._adapters if …vision…]
    if vision_adapters:      # [] → falsy → 不回退
        adapters = vision_adapters
# adapters 仍为 self._adapters == []
for adapter in adapters:     # 0 次循环
    …
raise AllProvidersFailedError(f"all {len(adapters)} providers failed: {failed}")
#  → "all 0 providers failed: []"
```

与日志字符串逐字吻合。

### 2.5 附带缺陷：系统提示谎报视觉能力

`backend/private_agent/main.py:794-805`

```python
provs = (cfg.get("models") or {}).get("providers", {})
if any(
    isinstance(p, dict) and p.get("multimodal")   # ← 只看 multimodal，不看 enabled
    for p in provs.values()
):
    vision_note = "\n- 你具备图片识别能力(已配置多模态模型)。…"
```

`glm-vision` 的 `multimodal=true` 但 `enabled=false` → 判定为真 → 系统提示持续注入
"你具备图片识别能力(已配置多模态模型)"。这正是 PA 自我分析得出
"机制：多模态模型直读图片 ✅ 能力存在"并给出肯定结论的直接原因。

**两个缺陷叠加**：系统提示谎报 → AI 自信声称有视觉能力 → 用户发图实测 →
空链硬崩，且错误信息为面向开发者的 `all 0 providers failed: []`，用户无法理解。

## 三、影响面（非首次发生，已静默 3 周）

查询全部 `stage=resilience` 错误事件（2026-09-08 起）：

| event_id | session | 时间 | 错误 |
|---|---|---|---|
| 592219 | 68808 | 09-08 10:42 | `all 1 providers failed: ['glm-5.2'…] upstream 402 INSUFFICIENT`（另因，余额）|
| 700026 | 83460 | **09-17 09:46** | `all 0 providers failed: []` |
| 700028 | 83460 | **09-17 09:47** | `all 0 providers failed: []` |
| 700030 | 83460 | **09-17 09:56** | `all 0 providers failed: []` |
| 705963 | 95786 | **09-23 16:36** | `all 0 providers failed: []` |
| 717297 | 90 | **09-29 10:53** | `all 0 providers failed: []` |
| 717300 | 90 | **09-29 10:54** | `all 0 providers failed: []` |

带图消息 → 后续首个事件类型统计：

| 消息类型 | 样本 | 结果 |
|---|---|---|
| **图片**（.jpg / .png） | 09-17 政府公示.png、09-23 粘贴图片.jpg、09-29 微信图片.jpg | **全部 error** |
| 文档（.md / .pdf） | 09-05、09-08、09-11、09-23 潘功胜.md | 正常 final |

**结论：自 2026-09-08 禁用 `glm-vision` 起，所有发图轮 100% 失败，共 7 次，
跨 3 个会话、3 个场景（zizhan / qinghe / 无涯）。文档类上传不受影响。**

## 四、修复方案

### 方案 A（推荐，治本 · 2 处改动）

**A-1｜链构建层：`registry.py:build_fallback_chain`**
在返回前判断，若 `chain_names` 非空但 `adapters` 为空（全部被 enabled 过滤），
记录 WARNING 日志（暴露悬空引用），保持返回空链不变（不改变既有语义）。

**A-2｜消费层：`react_loop.py:727` 判定改为"存在且非空"**

```python
_vision_adapters = getattr(self._vision_adapter, "_adapters", None)
if _vision_adapters:
    self._adapter = self._vision_adapter
    …（原有 max_output_tokens 重解析逻辑不变）
else:
    …（原 L750-776 兜底分支：退到 full_chain 的 vision 子集，或给出人话提示）
```

兼得：既有全链兜底能力恢复可达；无多模态时用户看到
"当前模型链不支持图片识别(未配置多模态模型)。请先在设置页配置并启用多模态模型。"
而不是 `all 0 providers failed: []`。

**A-3｜系统提示层：`main.py:796-799` 判定补充 enabled 条件**

```python
if any(
    isinstance(p, dict) and p.get("multimodal") and p.get("enabled", True)
    for p in provs.values()
):
```

消除"谎报能力"：无启用的多模态 provider 时不再声明具备图片识别能力。
（附注：此改动改变 `frozen_hash` → 旧会话 frozen zone 自动重建，属预期。）

### 方案 B（止血，立即恢复可用）

重新启用 `glm-vision`：`enabled=true`、`deleted=false`
（`base_url=https://open.bigmodel.cn/api/paas/v4`，`model_name=glm-4.6v-flash`，
`max_output_tokens=8192`，`api_key` 为 `.env` 的 `PA_GLM_VISION_API_KEY`）。

- 优点：无需改代码，立即恢复发图能力（需重启 PA 生效）。
- 前提：蒋先生确认该 API key 仍有效（09-08 那次是全模型故障期间的批量清理）。
- 注意：**仅做 B 不做 A，缺陷仍存在**——将来任何一次禁用视觉 provider
  都会再次静默复现，且错误信息依旧不可读。

### 方案 C（配置一致性守护 · 可选）

在 `build_fallback_chain` 或配置加载期，校验 `text_chain` / `vision_chain` /
`fallback_chain` 中引用的 provider 是否均 `enabled=true` 且未被 `deleted`，
不一致则启动时 WARNING 告警。防止再次出现"链里指向已禁用 provider"。

## 五、我的倾向

**A-2 为必改项**（单点、精准、修复根因，且恢复既有兜底逻辑可达）；
**A-3 同批改**（消除误导性能力声明，成本一行）；
**A-1/C 择一**（观测/守护，可后续）。

止血路径取决于 GLM key 是否有效：
- key 有效 → **B 先止血 + A 治本**，一起做；
- key 已失效 → 只做 A，发图轮走"未配置多模态模型"的明确提示（诚实降级，
  优于现在的静默硬崩），待配置新视觉 provider 后自然恢复。

## 六、验收清单

- [ ] 发图轮不再出现 `all 0 providers failed: []`
- [ ] `_vision_adapter` 为空链时，走 full_chain vision 子集或输出人话提示
- [ ] 无启用多模态 provider 时，系统提示不含"你具备图片识别能力"
- [ ] 纯文本轮零回归（text_chain 不受影响）
- [ ] 新增回归用例：`vision_chain` 全禁用 → 发图 → 断言不抛 `AllProvidersFailedError`
- [ ] 测试基线：pytest 全量（需 PA 停机）+ 前端 tsc 0 错

---

**取证方式**：DB 只读查询（`react_events` / `messages` / `config_runtime` / `sessions`）
+ 源码静态阅读（`registry.py` / `react_loop.py` / `base.py` / `main.py`）。
未修改任何代码或配置，等待蒋先生批准。
