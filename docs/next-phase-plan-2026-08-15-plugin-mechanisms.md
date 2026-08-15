# 设计文档：DSH 插件机制借鉴（A 方案：机制移植）

> 项目：Private Agent
> 日期：2026-08-15
> 决策：蒋先生 2026-08-15 批准 A 方案（借鉴机制，不复刻架构）并澄清 PA 专注 3 场景挖深不拓宽 → 机制2 registry 改造取消，缩水为重复定义收敛；B 方案（轻量插件总线）待 DSH 生态市场验证后再议，0 风险 0 成本挂起。
> 参考：DeepSeek Harness v0.1（2026-08-13 MIT 开源，Cordis"一切皆插件"）
> 状态：设计稿，待蒋先生确认后在 Trae Code 实施

---

## 0. 结论先行

A 方案三个机制经代码核查后**重新聚焦**：

| 机制 | 原评估 | 核查后现状 | 剩余工作 |
|---|---|---|---|
| 1. Hook 流水线 | "散落硬编码，需收拢" | **六事件 Hook 系统已在批次2 落地**（hooks.py 316 行，含决策协议） | 仅剩 C-4 事件级去重（批次3 尾巴）；权限门节点化**暂缓**（留给 B 方案） |
| 2. Preset 声明式场景 | "人格硬编码" | 确认：scope→行为映射散落 ≥5 个文件硬编码字典 | **取消 registry 改造**（2026-08-15 蒋先生澄清：PA 专注 3 场景挖深不拓宽，不新增场景，配置化无消费者；仅保留 ~10 行重复定义收敛） |
| 3. model-visible means logged | "缺纪律" | react_events 已有 thinking/tool_call/tool_result/final/error + confirmation；**上下文注入路径无事件** | 补 `context_injected` 事件 + derive 一致性 |

实际工作量重心 = 机制2（Preset）+ 机制3（事件流不变量）+ C-4 收尾。

---

## 1. 背景：DSH 借鉴点（已验证，多源交叉）

DeepSeek Harness v0.1（2026-08-13，MIT，Node.js/Cordis 微内核）核心机制：

1. **一切皆插件**：模型/工具/会话/沙箱/循环/调度/UI 全部可替换。PA 不复刻（单用户场景收益过剩、技术栈错配）。
2. **四种运行模式 = preset 组合**：标准/PTC/极简/创造，每模式一组插件集合，配置层组合。→ 对应 PA 机制2。
3. **append-only 会话日志 + "model-visible means logged" 不变量**：凡入模型视野（系统提示/注入/工具结果/子 Agent 调度）必落事件流，resume/fork/replay/telemetry 从同一流派生。→ 对应 PA 机制3。
4. **工具调用流水线**：hook→审批→权限→沙箱→超时，可扩展节点。→ PA 已有等价物（hooks.py 六事件 + 内联权限门），节点化暂缓。
5. Composio 实证：同一模型换 harness，30 任务通过 14-20、成本差 4.3 倍——harness 层决定模型上限兑现程度。

---

## 2. PA 现状核查（代码证据）

### 2.1 Hook 系统（已落地，超出原评估）

`core/hooks.py`（316 行，批次2 B-1）：

- 六事件：`user_prompt_submit` / `pre_tool_use` / `post_tool_use` / `stop` / `pre_compact` / `permission_request`
- 三实现：`command`（子进程 stdin/stdout JSON，退出码 2=阻断）/ `http`（SSRF 校验）/ `mcp_tool`
- 决策协议：`permissionDecision`(allow/deny/ask/defer) / `updatedInput` / `additionalContext` / `stop`；deny 优先合并
- 安全语义：默认空列表零回归；hook 失败/超时放行（增强非门禁）
- 配置：`config.yaml hooks: []` + admin CRUD（admin.py:3146）
- react_loop 已接入：pre_tool_use（react_loop.py:874-920，deny 阻断/updatedInput 改参/additionalContext 注入）、post_tool_use（react_loop.py:1205-1229）

### 2.2 权限门（内联，暂不节点化）

react_loop.py:922-983 固定顺序：hook allow 判定 → elevated 走 `permission_manager.check_and_confirm`（WS 60s）→ dangerous 直接阻断。**行为正确、形式未流水线化**——单用户场景下无换件需求，节点化收益低，标记为 B 方案升级项。

### 2.3 场景智能体：scope 贯通但行为映射散落硬编码（本设计主体）

同一 scope 字符串（`office`/`data_analysis`/`frontend_design`）在多处独立维护映射：

| 文件:行 | 映射内容 |
|---|---|
| `memory/memories_repo.py:48-50` | scope → 场景名（子瞻/白圭/清和） |
| `core/context_manager.py:33` | scope → 能力关键词（"办公 文档处理…"） |
| `core/reflection.py:39` | scope → 经验类别（domain_skill） |
| `core/reflection.py:58` | scope → 领域反思模板 |
| `skills/evolution_repo.py:34` | scope → 经验类别（重复定义） |
| `tools/builtins/__init__.py:75,105` | 场景会话不暴露系统级工具（过滤逻辑） |
| `skills/models.py:108` | skill `scene_scope` 字段（声明式，✅ 方向正确） |

问题：新增一个场景智能体需改 ≥5 个文件；两处经验类别定义已出现重复（reflection.py:39 与 evolution_repo.py:34），存在分叉风险。

### 2.4 事件流：append-only 已有，覆盖面与可靠性有缺口

- `storage/react_events.py`：INSERT（session_id, turn, event_type, payload）
- 事件类型：`thinking`/`tool_call`/`tool_result`/`final`/`error` + confirmation 相关 + memory manager 注入事件（memory/manager.py:444,458,551）
- **缺口 a**：上下文注入路径（Stable Zone KB/记忆注入、Hook Context 注入、状态栏注入、system_capabilities 输出）**不落事件**——排查"模型到底看到了什么"时无据可查（蒋先生 2026-08-15 清和验收已复现类似痛点：模型靠 code_execution 绕过读取，见 admin.py:5441 注释）
- **缺口 b**：C-4 事件级去重未完成（architecture-revision.md §12 最后一项未勾）：事件用 turn 粒度 offset 追踪，WS 重连推送与 DB 落库无共享 ID，重复/丢失不可判别

---

## 3. 设计

### 3.1 机制3 + C-4：事件流不变量（批次 A-1，先行）

**原则**（借 DSH）：*model-visible means logged*——凡进入模型请求上下文的注入，必须落 react_events，可从事件流完整重构模型视野。

**设计（2026-08-15 实施修订：event_id 用 DB 自增 id，取消 ULID 方案）**：

1. **新增事件类型 `context_injected`**：
   ```python
   # payload 结构
   {
     "source": "stable_memory" | "stable_lessons" | "stable_kb" | "stable_kb_auto"
             | "hook_additional_context" | "status_bar",
     "bytes": 1024,           # 注入体量（控 token 成本审计用）
     "preview": "...前200字",  # 不存全文（全文在 messages 表）
     "msg_id": 12345          # 关联 messages 行，全文可回查（hook/状态栏无）
   }
   ```
   注入点补齐（仅落库 react_events，**不新增 WS 推送**）：
   - Stable Zone 记忆（`context_manager._inject_memories`）
   - Stable Zone 经验（`context_manager._inject_lessons`）
   - Stable Zone KB（`_inject_kb_context` / `_inject_auto_retrieve_kb`）
   - Hook additionalContext（react_loop pre_tool_use 分支）
   - 状态栏（react_loop 每迭代注入）
   - **修正**：原设计的 `system_capabilities` 注入点取消——它是工具结果，已有 `tool_result` 事件覆盖审计性，再发 context_injected 属重复。

2. **C-4 事件级去重（实施修订）**：
   - **event_id = react_events 自增 id**（取消 ULID/新列）：`_emit_event` 落库后把 DB id 回填进推送事件，实时推送与 replay 重放同源同 id。
   - 前端维护已见 event_id 集合 + 最大 id 锚点：已见即丢弃（在一切副作用之前，含权限弹窗）；replay 请求带 `last_event_id`。
   - 后端 `build_replay_messages` 支持 `last_event_id`：`id > last_event_id` 事件级精确补发（修复 turn N 中途断线 → 该轮全量重放导致 delta 重复累积）；未提供回退 turn 粒度（向后兼容）；`full=True` 强制忽略 last_event_id（跨会话 id 不连续）。
   - 零新依赖、零 DDL 加列（仅 CHECK 约束扩容 context_injected）。

3. **derive 一致性测试**：新增 `tests/test_context_injected.py` + `test_ws_offset.py` 事件级补发用例——注入类消息可经 context_injected 事件 + messages 表联合重构；同轮中途断线按 id 精确补发缺失事件。

### 3.2 机制2：缩水为重复定义收敛（原 registry 设计已取消）

**决策变更（2026-08-15 蒋先生澄清）**：PA 核心理念专注 3 个绝对领域、挖深而非拓宽，不新增场景智能体。原设计的主要卖点（新增场景从"改 ≥5 文件"降为"加 yaml"）失去消费者；registry 改造动 6 个消费点，为消除理论分叉风险引入真实改动风险，**风险大于收益，取消**。

**保留的微缩项（~10 行，可选）**：六处映射中唯一真实分叉点——`reflection.py:39` 与 `evolution_repo.py:34` 各自维护一份 scope→经验类别映射。收敛方案：

```python
# core/scene_meta.py（新增，仅一个常量）
SCOPE_EXPERIENCE_CATEGORY = {
    "office": "domain_skill",
    "data_analysis": "domain_skill",
    "frontend_design": "domain_skill",
}
```

`reflection.py` 与 `evolution_repo.py` 改为 import 此常量。其余五处映射（memories_repo / context_manager / builtins 等）**保持原样不动**——场景定型后为稳定死水，无改动价值。

**明确不做**（备查）：
- ScenePreset registry / config.scenes 段 / admin CRUD——无新增场景需求
- 权限门节点化——行为正确，留 B 方案
- provider 链 per-scene 配置——无差异化需求
- kb_mounts 预留字段——无承载容器，删除该设计

### 3.3 机制1 收尾说明

- C-4 已并入 3.1（它本质是事件流可靠性，与机制3 同源同批）
- 权限门节点化（pre_call→approve→permission→execute→post_call 链式可配置）**明确不做**：行为已正确、单用户无换件需求；待 B 方案启动时作为插件总线的第一个内置节点改造

---

## 4. 实施顺序与依赖

```
批次 A-1（事件流，已实施 2026-08-15，约 2-4 天）：
  ① react_events CHECK 扩容 context_injected（无新增列）
  ② _emit_event 回填 DB id 为 event_id（实时推送与 replay 同源）  ← C-4 收尾
  ③ WS replay 支持 last_event_id 事件级精确补发 + 前端已见 id 去重
  ④ context_injected 事件补齐（5 个注入点）
  ⑤ derive 一致性测试 + 事件级补发测试
批次 A-2（微缩，约 0.5 天，可选）：
  ⑥ core/scene_meta.py 共享常量 + reflection/evolution_repo 两处 import 收敛
每批独立原子提交（根因/实现/验证）+ 全量 pytest 回归 + 前端 tsc/vitest + 真机单场景验证。
```

依赖：A-1 无前置（可立即开工）；A-2 独立（可随时顺带做）。与 v0.5.1 KB embedding 主线不冲突。

## 5. 测试方案要点

- **事件级补发**：同轮中途断线 → 按 event_id 精确补发缺失事件（不全量重放）；full=True 忽略 event_id；未提供回退 turn 粒度
- **重放事件带 event_id**：前端已见 id 去重（修复 delta 重复累积）
- **context_injected**：五注入点各自触发后 react_events 可查（source/bytes/preview/msg_id 齐全）；审计失败静默不阻断注入
- **derive 一致性**：注入消息 = context_injected(preview) + messages(全文) 可重构
- **重复定义收敛**（如做 A-2）：reflection 与 evolution_repo 读同一常量值
- 回归：后端 pytest 全量（加载 .env，--ignore=test_eval_full_cycle.py）+ 前端 tsc 0 错 + vitest 全过

## 6. 风险与兼容

| 风险 | 缓解 |
|---|---|
| event_id 回填依赖 insert 返回值 | persist=False 事件（sandbox_output/turn_paused）无 id，不参与去重（不入库不重放，丢失可接受） |
| context_injected 增加事件量 | 仅注入发生时 emit（会话启动有限次 + hook/状态栏有界）；preview 截断 200 字 |
| 前端 seen 集合内存增长 | 事件 id 为 int，Set 增长可控（7 天 TTL 库） |
| 与打包流程耦合 | event_id 对旧版前端透明（新增可选字段）；版本更新后按惯例由蒋先生重新打包 |

## 7. 验收清单

- [x] C-4 事件级去重完成（architecture-revision.md §12 全勾，批次3 正式收口）
- [x] "模型看到了什么"可完全从 react_events 取证（context_injected 覆盖五注入点）
- **ScenePreset registry 改造已取消**（场景固定，无新增需求，风险大于收益）
- **重复定义收敛**：reflection/evolution_repo 经验类别共享 core/scene_meta.py 常量（如做 A-2）
- [ ] 全量测试基线通过（**2026-08-15 实测受阻**：全量套件存在既有测试缺陷——test_admin_database:94 断言默认值与环境不符、测试间 schema 污染累积。已记入 docs/project-health-review-2026-08-15.md §B.1，作为独立治理项）
- [x] 权限门节点化、B 方案触发条件（DSH 生态市场验证）写入本文档备查

## 8. B 方案挂起备忘（触发条件）

- DSH 插件生态经市场验证成熟（社区插件质量与数量双达标）
- PA 出现真实"运行时换件"需求（如：不停服替换 provider 适配器 / 第三方插件挂载诉求）
- 触发后路线：pluggy 式微内核（服务注册 + disposer 可逆卸载），权限门节点化为第一个内置插件改造项
