# PA 集成 modlens 视觉能力 — 配置与实战记录

- **日期**：2026-08-14
- **依据**：WorkBuddy 会话《modlens-安装配置与实战记录-2026-08-14》（智谱 GLM-4.6V-Flash 验证成功经验）
- **结论**：PA 已具备发图自动读图能力。真实链路（WS user_message → ReactLoop 双链 → vision_chain）验证通过，模型真实读到图片并正确描述。

---

## 一、落地方式

modlens 的成功经验（智谱官方 OpenAI 兼容端点 + GLM-4.6V-Flash + 429 重试）直接落地为 **PA 原生 vision provider**，走 PA 既有双链架构，零外部 CLI 依赖：

| 项 | 值 |
|:--|:--|
| provider 名 | `glm-vision` |
| base_url | `https://open.bigmodel.cn/api/paas/v4` |
| model_name | `glm-4.6v-flash` |
| multimodal | `true` |
| max_output_tokens | `8192`（智谱上限 32768 内） |
| vision_chain | `["glm-vision"]` |
| text_chain | 未配置 → 回退 fallback_chain（纯文本链路零影响） |
| API key | 复用 modlens 智谱 key；`PA_GLM_VISION_API_KEY` 写 backend/.env（Electron 启动加载）+ AES-256-GCM 密文存 config_runtime（重启 `_restore_keys_from_runtime` 恢复，双保险） |

## 二、兼容性实测（智谱端点 vs PA 请求格式）

| 项 | 结果 |
|:--|:--|
| 纯文本 chat | ✅（偶发 429 限流，重试即过） |
| image_url base64 data URL | ✅ 200，真实看图 |
| tools 参数 | ✅ 200（reasoning 模型，tool calling 可用） |
| stream 流式 | ✅（FallbackChain 指数退避重试兜底 429） |

## 三、代码修复（真实链路暴露）

**问题**：发图轮次 `upstream 400 max_tokens参数非法(范围[1,32768])`。
**根因**：`provider_limits` 按 text 链首选（deepseek-flash, `max_output_tokens=49152`）解析，切 vision 链后沿用超上限。
**修复**（`backend/private_agent/core/react_loop.py`，0.5.2）：`require_vision` 切换 `vision_adapter` 时，按 vision 链首 provider 重新解析 `max_output_tokens` 覆盖——参数上限与实际模型绑定。

## 四、真实链路验证结果

验证路径 = 用户真实触发路径（WS `user_message` 含图片引用 → run_turn 装配），非 test/直连：

```
WS 发图 → require_vision=True → vision_chain=[glm-vision]
→ GLM-4.6V-Flash 流式输出 delta → turn_end
→ messages 表落库完整描述 ✅
```

模型对参考文档同一张图的描述与 modlens 实测**完全吻合**：
- 左侧厚重积云（深蓝/紫/暖黄层次）
- 右侧深邃星空/银河
- 暖黄光晕过渡、冷暖对比
- 数字插画/动漫幻想风格

回归：react_loop 相关 49 passed + provider 生命周期 5 passed，无回归。

## 五、已知行为（智谱免费档，记录不修改）

1. **偶发 429 限流**（`访问量过大`）：FallbackChain 已有 0.5s→1s→2s 指数退避重试，重试即可。
2. **带 tools 偶发空 tool_call**（`{"id":"","function":{"name":"","arguments":"{}"}}`）：模型自身输出不稳定；PA 忠实执行 → `unknown tool` 错误 → 下一轮模型自愈正常回答。**不静默丢弃**（避免误吞真实调用）；如后续频发可考虑 executor 层防御。

## 六、复现与回滚

- 配置写入（幂等可复现）：`backend/scripts/configure_glm_vision.py`
- 真实链路验证：`backend/scripts/verify_modlens_vision.py`
- 回滚：删除 `models.providers.glm-vision.*` 与 `models.router.vision_chain` 的 config_runtime 记录即可。

---

*记录生成：2026-08-14 | 依据：真实链路实测（WS 发图 + DB 落库校验）+ pytest 回归*
