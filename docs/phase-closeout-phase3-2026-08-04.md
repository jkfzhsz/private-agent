# 阶段三收尾报告：人在环中的可控协作与最小内核落地

> 完成时间：2026-08-04（当日实施完成，压缩计划周期 08-05~08-21 → 当日全部完成）
> 依据计划：`docs/phase3-iteration-plan-2026-08-04.md`（v1.1）
> 测试基线：**后端 1073 passed**（973 基线 + 100 新增）+ **前端 13 passed** + **tsc 0 error**
> 关联调研：`docs/round2-benchmark-research-2026-08-04.md`（B-1~B-14 借鉴点）

---

## 一、实施成果总览

| 批次 | 内容 | 状态 | 关键交付 |
|---|---|---|---|
| **批次 0 准备** | 基线回归 + 规则 DSL 骨架 + S-1 前置修复 | ✅ | `tools/permission.py` PermissionRule DSL；`build_compress_adapter` 主模型回退 |
| **批次 1 权限规则化（P0）** | 规则求值层 + 5 权限模式 + 审批矩阵 + 风险分级 + 注入强化 | ✅ | `permission_manager.py` 重构；`sessions.permission_mode` 列；admin 端点；前端模式切换/风险徽标 |
| **批次 2 Hooks（P0）** | 六事件 + 三类实现 + 决策回写 + admin CRUD | ✅ | 新增 `core/hooks.py`；ReactLoop 四事件接入 |
| **批次 3 最小内核 + 纠正沉淀（P1）** | Skill 权限声明 + is_kernel 下沉 + correction 记忆 | ✅ | `skills/models.py` rules 声明；selector 内核锚点；`maybe_extract_from_correction`；前端编辑重发 |
| **批次 4 审批挂起恢复（P1）** | approval_defer + 挂起续等 | ✅ | `PermissionManager.defer`（shield 修复）；WS 消息；前端"稍后决定"按钮 |

**目标达成**：G-1~G-7 全部完成（对照计划 §1.1）。

## 二、关键实现细节与修复

### 2.1 S-1 compress_adapter 前置修复（批次 0）
- 根因：`config_runtime` 无 `compress_model` 记录、前端无配置入口 → `build_compress_adapter` 恒返回 None → 记忆提取从未真正可用（M2 handoff P0-1 遗留）。
- 修复：`models/registry.py` 增加 `compress_fallback_main`（默认 true）回退 fallback_chain 首选（主模型兼压缩）；config.yaml 加开关。**真机验证：生产配置下返回 `OpenAICompatibleAdapter -> deepseek-flash`**（此前 None）。

### 2.2 权限规则化（批次 1）
- **规则 DSL**：`action:Tool(specifier)`（如 `deny:file_write(//**/.env)`），deny 优先于一切 allow，source 优先级 session > skill > config（对齐 Claude Code 语义）。
- **5 权限模式**：default/plan/acceptEdits/cautious/deny_all（OpenClaw 预置档命名封装，`sessions.permission_mode` 持久化 + admin GET/PUT + 设置页切换 UI）。
- **风险分级**：`ToolDef.risk_level` + `assess_risk` 启发式（.env/内网/危险命令 → high），确认卡片渲染徽标 + 来源解释（可解释决策）。

### 2.3 Hooks 系统（批次 2）
- `core/hooks.py`：六事件（user_prompt_submit/pre_tool_use/post_tool_use/stop/pre_compact/permission_request）+ 三类实现（command 子进程 JSON 协议 / http 过 SSRF / mcp_tool 注入回调）。
- **默认空列表 = 行为不变**（AC-9 零回归）；失败放行 + 5s 超时 + 审计进 results。
- **关键 bug 修复**：`asyncio.wait_for` 超时会取消内部 future → defer 后无法续等。用 `asyncio.shield` 保护（B-14 得以实现）。

### 2.4 最小内核与纠正沉淀（批次 3）
- **is_kernel 下沉**：设计演进——最初意图"白名单豁免内核"会破坏 Skill 白名单强过滤语义（office 禁 http_request 失效）；最终定稿为**内核工具作为 ToolSelector 隐含锚点始终注入**（calculator/datetime/web_search/code_execution/file_*/http_request），非内核（search_knowledge/read_artifact）靠评分竞争 → 白名单语义不变 + 实现"非场景工具不主动注入"。
- **correction 记忆**：`maybe_extract_from_correction`（LLM 定向提取 + 启发式降级），type=correction（importance 0.9），admin 端点 + 前端"✎ 编辑"按钮触发（编辑重发 → 异步沉淀）。

### 2.5 审批挂起（批次 4）
- `PermissionManager.defer(confirmation_id)`：60s 超时后不立即拒绝，继续等待 defer_timeout（默认 10 分钟），期间可 resolve；**fail-closed 默认不变**（未 defer 仍 60s 拒绝）。
- WS `approval_defer` 消息 + 前端"稍后决定"按钮。

## 三、测试统计

| 维度 | 基线 | 阶段三后 | 增量 |
|---|---|---|---|
| 后端 pytest | 973 | **1073** | +100（7 个新测试文件：permission_rules 23 / permission_modes 30 / admin_permission 6 / hooks 26 / kernel_downgrade 14 / memory_correction 7 / approval_defer 6 + 存量更新） |
| 前端 vitest | 13 | **13** | 全绿（改动无测试回归） |
| tsc | 0 error | **0 error** | — |

## 四、PA1.0 同步与部署状态

- **后端代码**：18 个改动/新增文件已同步至 `D:\PA1.0\backend`（md5 逐文件校验一致）——桌面版重启即生效（轻度打包设计）。
- **前端改动**（确认卡片风险徽标/权限模式切换/编辑重发/Hooks 管理 UI）：exe 内置，**需重新打包 `build-electron.bat` 生效**（待办）。
- **GitHub**：待提交推送（本地 commit + 用户本机 push 或网络恢复后执行）。

## 五、遗留项与建议（阶段四输入）

| # | 遗留项 | 说明 | 建议归属 |
|---|---|---|---|
| L-1 | 前端 Hooks 管理 UI | admin CRUD 端点已就绪，设置页 HooksSection 未实现 | 阶段四 |
| L-2 | 前端重新打包 exe | 阶段三全部前端改动生效依赖 | 发布时 |
| L-3 | B-10 技能递归进化（skills/generator.py） | 依赖 correction 沉淀（已就绪）+ 评估基线（已就绪） | **阶段四优先** |
| L-4 | B-7 会话树回放 / B-9 子代理 | P2 项，依赖 Hooks 基座（已就绪） | 阶段四/五 |
| L-5 | reviewer 模型自动审批（B-13） | auto 模式增强，成本敏感 | 阶段五可选 |
| L-6 | MemPalace 纠正同步 | correction 沉淀仅落 user_memories，未同步记忆宫殿 | 阶段四 |
| L-7 | `api/` 目录残留 migrations.py/schema.sql（PA1.0） | 误复制残留，无导入影响（safe-delete 拦截 rm） | 清理时处理 |

## 六、教训（沉淀到工作日志）

1. **wait_for 的 cancellation 陷阱**：`asyncio.wait_for(fut)` 超时会取消 fut，后续 resolve/defer 无效——凡"超时后仍可继续操作"的场景必须 `asyncio.shield(fut)`。
2. **is_kernel 设计演进**："白名单豁免内核"会破坏 Skill 白名单语义（测试立刻暴露）——内核锚点应落在 ToolSelector（注入层）而非白名单（约束层），分层职责要分清。
3. **批量 cp 同步是危险操作**：一次性 cp 多文件到单目录会全错位——应逐文件/逐目录验证（用 md5 校验脚本兜底），本次 PA1.0 同步出现 13 个文件错位已修复。
4. **pytest 并发互踩再次验证**：中途跑 admin 测试与全量回归并发 → DROP SCHEMA 冲突致 5 个假失败——测试库并发是硬约束。
5. **Hooks 失败放行语义**：hook 是增强不是门禁（默认放行），但 permissionDecision=deny 是终局——决策合并用 deny 优先。

---

*本报告由 WorkBuddy 生成，作为阶段三存档与阶段四计划输入。*
