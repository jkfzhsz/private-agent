# 输出美化：DesignStylist 技能 + 载体规范增强（P0）

> 日期：2026-08-14
> 状态：设计评审稿（待确认后动工）
> 关联：`docs/next-phase-plan-2026-08-12-skill-enhancement.md`（附加技能机制，已落地）、
> `docs/next-phase-plan-2026-08-08-reasonix-skills.md`（playbook 技能转化）、
> `backend/skills/{pptx,pdf,frontend_design}/`（载体技能现状）

---

## 1. 背景与目标

用户提出"提升 Agent 输出 HTML / PDF / PPT 精美高级感"的六类方案并请求评估。评估结论（2026-08-14 记录）：

- 方案方向正确（"以约束取代自由发挥"），但为通用清单，约 1/4 内容与 PA 错位或无法证实；
- 经核实：**Reasonix Design Subagent / Open Agent Studio Design Skill Set 两个引用均不实**；
  reasonix-skills 实测无 design 类技能；PA 已有 18 个目录技能 + 附加技能机制（`session_supplementary_skills` 已落地）；
- 建议采纳 P0 纯提示词方案（DesignStylist 附加技能），不追外部仓库、不启用美化子代理、不引入 LangGraph。

本设计文档固化 **P0（可立即落地）**，并概要规划 P1/P2 供后续评审。

### 目标
1. 建立一套 PA 统一的"高级感"设计规范层（design-stylist 附加技能），可叠加在任意生成类主技能上；
2. 增强 pptx / pdf / frontend_design 三个载体技能的 system_prompt，落实载体专项约束；
3. 为 P1（模板库 + 图表主题 + 视觉评审）与 P2（Typst 编译链 + 封面合成）预留接口与待验证项。

## 2. 现状盘点（证据）

| 维度 | 现状 | 结论 |
|---|---|---|
| 技能体系 | 18 个目录技能（docx/pptx/xlsx/pdf、office/frontend_design/data_analysis、reasonix 转化 11 个） | 新技能直接落座现有目录结构 |
| 附加技能 | `session_supplementary_skills` 已落地（schema.sql + migrations.py + main.py + admin.py + `test_admin_supplementary_skills.py`） | "主技能 + design-stylist" 挂载路径现成 |
| pptx 技能 | 已含 5 套配色、10/20/30 法则、6×6 原则、页面类型清单 | 本次为增量增强 |
| pdf 技能 | 已含 weasyprint / reportlab / pypdf 四方案与中文字体要点 | 本次补齐版式预设与规范 |
| frontend_design（清和） | 已承担美化职责（scene_profile.rules 第 2 条），`max_frozen_token: 4000` | 规范层与 persona 解耦，不合并 |
| 视觉能力 | glm-vision 链已可用（2026-08-14） | P1 视觉评审前置条件已具备 |
| 沙箱 | 仅 Python（JS 沙箱未实现，M2 移交 P0#2） | JS 生态工具（Satori/Chart.js 服务端）首期不可用 |

## 3. 不采纳项与理由（决策记录）

| 方案项 | 决策 | 理由 |
|---|---|---|
| 美化子代理（delegate_subtask） | 首期不启用 | 审美评审无需独立上下文；delegate 有每轮 ≤3 / 总时长 300s 硬约束；沿用"主代理直连、少嵌套"经验。二期可试验"并行评审多风格变体" |
| LangGraph 工作流 | 不引入 | PA 无 LangGraph；流水线映射为 ReactLoop 单轮内顺序工具调用 + 提示词阶段指令 |
| Reasonix Design Subagent / Open Agent Studio Design Skill Set | 不追 | 两个名称均无法证实存在；openagentskill.com 的 HTML PPT Studio（24 主题/31 布局）为真实替代，但首期不依赖外部，自建更可控 |
| Satori + Resvg（服务端封面） | 首期不用 | Node 生态，沙箱 Python-only；P2 以 PIL/matplotlib 合成替代 |
| 强制"先套模板再填充" | 不硬性化 | 模板匹配做"技能内模板片段 + 模型按用途读取"（P1），不做强制装配逻辑 |

## 4. P0 设计

### 4.1 design-stylist 技能（新增 `backend/skills/design-stylist/`）

定位：**通用版式规范层**（playbook 型附加技能），与 persona（清和等）解耦，可叠加在任意主技能上。

`skill.yaml` 草案：

```yaml
name: design-stylist
version: "1.0.0"
description: "版式设计规范与视觉评审：统一配色/字体/留白/层次约束，审稿并优化 HTML/PPT/PDF 文稿高级感。"
scenario: design
author: "private-agent"
created_at: "2026-08-14"
enabled: true
display_name: "版式设计"

dependencies:
  tools:
    - name: file_read
      safety_level_override: safe
    - name: file_write
      safety_level_override: elevated
    - name: code_execution
      safety_level_override: safe

permissions:
  allow_file_write: true
  allow_network: false
  sandbox_enabled: true
  max_file_size_mb: 20

knowledge_base:
  enabled: true
  scenario: design
  auto_retrieve: true

max_frozen_token: 4000
```

`system_prompt.md` 核心内容草案（P0 主体，最终以实施时定稿为准）：

```
# 版式设计规范与视觉评审（design-stylist）

你是一位专业平面与版式设计师，职责是让 Agent 输出的 HTML / PPT / PDF 文稿具备高级感与一致性。
本技能以附加技能形式叠加在生成类主技能之上：内容由主技能生成，你负责规范约束、审稿与修订。

## 工作模式
- generate：要求"生成精美文稿"时，按选定风格预设输出完整代码/文稿（附设计说明）。
- revise：已有初稿时，先按评审清单打分，再输出结构化修复清单与修订后的完整版本。

## 风格预设（未指定则按文档类型推断）
- business-minimal：商务极简。白底/深灰文字/单一强调色，无装饰，大留白。
- tech-dark：科技暗调。深蓝黑底/青蓝强调/浅灰文字，适合技术产品。
- academic-clean：学术专业。米白底/深蓝标题/衬线标题+无衬线正文，严谨层级。
- light-luxury：轻奢哑光。低饱和莫兰迪色系/哑光质感/细字重，适合品牌提案。

## 强制设计规范（所有风格通用）
1. 色彩：主色+辅助色+中性黑白灰，彩色总数 ≤4；低饱和度优先；禁止高饱和撞色。
2. 排版：8pt 网格系统；段落留白充足；标题粗/正文常规/注释轻量三档字重。
3. 文字：标题 ≤2 行；正文行宽 45–75 字符；禁止大段无分割文字。
4. 视觉层次：标题→摘要→正文→注释逐级区分，用间距而非线条分割；少用边框。
5. 装饰：渐变/阴影/图标克制使用；禁止廉价闪烁与多重纹理。
6. 图表：无 3D、去冗余网格线、统一色板；图题表题规范。
7. 载体特则：PPT 每页一个核心观点、≤6 行/页；PDF 页边距 ≥2cm、页眉页脚页码；
   HTML 响应式、卡片柔和圆角、低透明度阴影。

## 评审清单（revise 模式必用，0–5 分 + 修复项）
1. 信息层级清晰度  2. 色彩和谐度（违规色数）  3. 留白与版式平衡
4. 字体一致性（字体家族数）  5. 图文比例与拥挤度
输出：评分表 + 按优先级排序的修复清单（位置、修改方式、预期效果）。

## 硬性禁忌
高饱和色彩、过多装饰、密集文字、3D 图表、杂乱动画、多种字体混用、无层级大段文本。

## 工作流程
1. 识别文档用途与受众 → 选定风格预设。
2. 重构信息层级，拆分拥挤页面，提炼核心标题。
3. 规范配色、字体、留白、图文布局。
4. 输出优化后的完整代码/文稿 + 修改说明。
```

### 4.2 载体技能增量（修改三个现有 system_prompt.md，追加段落）

**pptx（`backend/skills/pptx/system_prompt.md`）**：
- 统一字体家族：标题/正文同一家族（Inter / 思源黑体 / Calibri），全稿 ≤2 个字体家族；
- 每页一个核心观点，一页超载自动拆分多页（保留 6×6 原则）；
- 图表统一：matplotlib 无 3D、去冗余网格线、数据标签简洁、色板取 design-stylist 风格预设；
- 统一页眉（主题/品牌）、页码、页脚（日期/作者）；python-pptx 无动画，天然满足"禁动画"；
- 原有 5 套配色表改为"引用 design-stylist 4 风格预设色板"。

**pdf（`backend/skills/pdf/system_prompt.md`）**：
- 新增三套版式预设：学术报告 / 商业提案 / 白皮书（页边距、页眉页脚、标题层级、图题表题、参考文献样式各一套）；
- **生成方案（2026-08-14 环境验证后定稿）**：主方案 **Typst**（`pip install typst`，Python 绑定自带引擎，已验证中文/表格/页脚页码/标题编号全通过，0.28s/页级）；HTML→PDF 兜底用 **Edge headless**（`msedge --headless=new --print-to-pdf`，系统自带零依赖，已验证 45KB 中文 PDF）；**弃用 weasyprint**（Windows 需 GTK3 runtime，`libgobject-2.0-0` 加载失败）；
- 中文字体：Microsoft YaHei（系统自带，已验证可用）优先；思源黑体作为 P2 可选增强（装 D 盘字体目录）；
- 输出前自查：页数、字体、页边距 ≥2cm、页眉页脚页码。

**frontend_design（`backend/skills/frontend_design/system_prompt.md`）**：
- 引用 design-stylist 规范（HTML 专项：极简样式、卡片圆角、低透明度阴影、响应式、可选暗色模式）；
- 生成物内嵌图表资产时优先本地 vendored（mermaid.js / chart.js 本地文件），避免 CDN 离线依赖。

### 4.3 挂载与交互

- 挂载路径：复用现有附加技能机制——用户/主技能生成时，前端"选择技能"多选挂载 design-stylist，或会话内 `/design-stylist` 召唤（08-12 方案 Phase 3 若已落地）；工具白名单取主技能 ∪ design-stylist（并集，冲突取更严格 safety_level，机制已就位）。
- 注入预算：design-stylist system_prompt 控制在 ~4000 token（max_frozen_token: 4000）；若与主技能叠加超预算，附加技能注入精简版（核心规范 + 评审清单，省略风格预设详述）。

## 5. P1 / P2 概要（后续评审）

| 阶段 | 内容 | 前置 |
|---|---|---|
| P1 | 模板库：design-stylist `kb_assets/` 放 HTML 封面/报告页/看板骨架、pptx 布局、pdf 版式预设片段，按用途索引 | P0 稳定后 |
| P1 | 图表统一主题：matplotlib/plotly 主题函数（色板 + 去网格 + 字号）作为技能内 code 模板 | P0 稳定后 |
| P1 | 视觉评审增强：HTML 渲染截图回流 → glm-vision 评审（需先验证渲染/截图通道；不可行则保持纯清单评审） | 验证截图通道 |
| P1 | Typst 编译链（**已提前，验证通过**）：pip 包 `typst`（0.15.0，装 D 盘 venv，29.4MB wheel）；pdf 技能增 Typst 方案（API 注意：`typst.compile` 传**文件路径**；页脚页码用 `footer: context "第 " + counter(page).display() + " 页"`，勿用 `context { }` 块） | 已完成环境验证 |
| P1 | Edge headless 兜底（已验证）：`msedge --headless=new --disable-gpu --no-sandbox --print-to-pdf="out.pdf" file:///...`，HTML→PDF 零依赖 | 已完成环境验证 |
| P2 | 封面/横幅：PIL 合成模板（技能内 code）；artifact 内嵌 mermaid.js/chart.js 本地资产 | 资产本地化 |

## 6. 实施步骤（P0）

1. 新建 `backend/skills/design-stylist/`：`skill.yaml` + `system_prompt.md`（定稿 4.1 内容）；
2. 修改 `pptx / pdf / frontend_design` 三个 system_prompt.md（追加 4.2 增量段，保留原内容）；
3. 前端零改动（技能库列表与附加技能挂载走既有机制）；确认"选择技能"弹层可多选挂载；
4. 测试：新增 `tests/test_design_stylist.py`——技能加载/解析、附加技能挂载（复用 supplementary 机制）、三技能 prompt 关键约束存在性（字体家族/评审清单/版式预设关键词）；
5. 真实链路验证：会话挂载 design-stylist + pptx 主技能，生成一份示例 PPT 与一份 HTML 报告，核对规范遵守度。
6. **沙箱依赖落地（§9.1 决策）**：`pip install typst pypdf`（已装 D 盘 venv）；config_runtime 覆盖 `sandbox.languages.python.command` → venv python 绝对路径；回归既有测试（GBK/UTF-8、包差异）。

工作量：后端 ~3h，测试 ~1h，真实链路验证 ~1h，合计 **~5h（1 天）**。

## 7. 验证方式

- 单测：`test_design_stylist.py` 全过 + 既有 54+ 回归无失败；
- 真实链路：附加技能挂载后 frozen_zone 重建正确（frozen_hash 变化触发 replace_frozen_zone，机制已有）；
- 效果验收：示例 PPT/HTML 满足评审清单 5 项得分均 ≥4（人工复核）。

## 8. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 附加技能叠加导致 prompt 超预算 | 中 | system_prompt 精简版注入（§4.3）；max_frozen_token 4000 硬上限 |
| 规范过强导致模型输出僵化/变慢 | 低 | 评审清单限 5 项；revise 仅一轮；规范以"硬禁忌 + 建议"分级 |
| 风格预设与 pptx 旧配色表冲突 | 低 | 统一以 design-stylist 为准，pptx 配色表改为引用 |
| 中文字体失败（pdf） | 低 | Typst 已实测通过（微软雅黑）；Edge headless 兜底；思源黑体 P2 可选 |
| vision 截图评审通道不可行 | 低 | P1 保持纯清单评审降级 |

## 9. 环境验证结果（2026-08-14 实测完成）

### 9.1 沙箱执行环境与依赖（实测）

- **沙箱解释器 = 系统 Python 3.10**（`C:\Users\zongxin\AppData\Local\Programs\Python\Python310`），**不是** backend/.venv（config `sandbox.languages.python.command: "python"`）；site-packages 在 C 盘。
- 沙箱内已可用：python-pptx 1.0.2 ✅、matplotlib 3.10.9 ✅、PIL 12.3.0 ✅；缺失：weasyprint/reportlab/pypdf。
- **依赖安装铁律冲突**：新增依赖若装系统 python 会落 C 盘 site-packages（违反铁律一）。**决策：新增 PDF 依赖装 D 盘 venv（backend/.venv），并把 `sandbox.languages.python.command` 改为指向 venv 的 python（config_runtime 覆盖，实施步骤新增第 6 条）**；python-pptx/matplotlib 已存在于系统 python，暂不迁移。
- 验证脚本留存：`backend/scripts/verify_env_capabilities.py`（可复现，驱动真实 SandboxService）。

### 9.2 沙箱网络策略（实测）

| 场景 | 结果 | 结论 |
|---|---|---|
| 默认禁网（allow_network=False） | urllib 被死代理 `http://127.0.0.1:9` 拦截失败；**socket 直连成功** | 应用层隔离有效但非强制（已知边界，docs/security-model.md） |
| 显式放行（allow_network=True） | urllib 直连 200 ✅ | code_execution 工具 `network=true` 经权限确认后出网可用 |

**决策**：HTML artifact 内嵌图表/样式资产**一律本地 vendored**（不依赖 CDN 与沙箱出网）；沙箱内 pip install 需显式 `network=true`。

### 9.3 PDF 生成技术选型（实测）

| 方案 | 结果 | 决策 |
|---|---|---|
| weasyprint（69.0，pip 装 D 盘 venv 成功） | **import 即失败**：`cannot load library 'libgobject-2.0-0'`（需 GTK3 runtime，Windows 痛点） | **弃用**（除非装 GTK runtime 到 D 盘，性价比低） |
| Typst（pip 包 `typst` 0.15.0，装 D 盘 venv，29.4MB） | 中文/表格/页脚页码/标题编号全通过，0.28s，41KB PDF ✅ | **PDF 主方案**，P2 提前至 P0/P1 |
| Edge headless（系统自带） | `--headless=new --print-to-pdf` 中文 45KB PDF ✅ | HTML→PDF 零依赖兜底 |

**Typst API 注意（实测踩坑）**：`typst.compile()` 的 input 是**文件路径**（非源码字符串）；页脚页码需写
`#set page(footer: context "第 " + counter(page).display() + " 页")`（`context { }` 块语法会报
`label <page> does not exist`）；`font()` 函数不存在（应 `#show table.cell: set text(size: 10pt)`）。

### 9.4 其余待确认

1. design-stylist 保持"1 个通用技能"而非按载体拆 3 个——确认无异议；
2. 沙箱 python 切换 venv（§9.1）需回归：GBK/UTF-8 行为、包差异、既有 54+ 测试。

---

## 10. P0 实施记录（2026-08-15 落地）

**实施清单（§6 步骤 1-4 + 6）**：
1. 新建 `backend/skills/design-stylist/`（skill.yaml + system_prompt.md）。**定稿调整**：`knowledge_base.enabled=false`（纯规范技能，避免空场景库检索噪音，与 §4.1 草案差异）；
2. 增强 `pptx / pdf / frontend_design` 三个 system_prompt.md（追加"版式增强规范"段，原内容保留）；
3. 新增 `tests/test_design_stylist.py`（17 项：manifest 解析 / 4 风格预设 / 评审清单 / 硬性禁忌 / Typst 技术指引 / 三载体增量段断言）；
4. **沙箱依赖落地（§9.1 决策）**：`backend/.venv` 补装 python-pptx 1.0.2 + matplotlib 3.10.9（原已装 typst 0.15.0/pypdf 6.16.0/PIL）；`config_runtime` 写入 `sandbox.languages.python.command` = venv python 绝对路径（脚本 `backend/scripts/configure_sandbox_python.py`，幂等可复现）；**真实沙箱验证**：EXE 已切换 venv，pptx/matplotlib/PIL/typst/pypdf 五库 OK；
5. **回归**：test_design_stylist + test_scene_skill + test_admin_supplementary_skills + test_admin_sandbox_config = **52 passed**，零失败。

**遗留项（步骤 3 确认结果 + 步骤 5）**：
- **前端"选择技能"多选挂载 UI 未实现**（08-12 方案 Phase 2 前端部分未落地；后端 API 与 WS `supplementary_skills` 透传已就位）。影响：design-stylist 目前仅能通过 `POST /admin/sessions/{sid}/supplementary-skills` API 挂载，桌面 UI 无入口。建议后续补前端多选弹层 + 附加技能 chip（08-12 Phase 2/3），或本期先接受 API 挂载。
- **真实链路验证（§6 步骤 5）待验收**：桌面启动后，会话挂载 design-stylist + pptx 主技能，生成示例 PPT/HTML，核对评审清单得分 ≥4（人工复核）。
