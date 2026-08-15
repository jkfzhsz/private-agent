# 技能功能扩展与优化实施方案

> 2026-08-12 蒋先生提出，Plan 模式（先不执行）
> **实施状态（2026-08-15 全面审视确认）**：已实施——需求1（/ 召唤技能，session_supplementary_skills 'slash'）、需求2（上传 zip 一键识别安装，test_admin_skill_upload_zip）、需求3（切换→选择多技能，supplementary 机制）；提交 d93264f。

## 一、背景与目标

当前 PA 技能体系为**单技能锁定架构**：每个会话通过 `activate_skill` 锁定一个技能（`sessions.locked_skill_name`），注入该技能的 system_prompt + 工具白名单。切换技能 = 新建会话。三个需求将技能体系从"单技能锁定"升级为"多技能灵活组合"。

| 需求 | 核心变化 |
|---|---|
| 1. 对话中 `/` 召唤技能 | 输入框命令解析 + 技能浮层 + 临时挂载 |
| 2. 上传压缩包一键识别安装 | 增强 upload-zip：中文翻译 + 结构校验 + 失败原因 |
| 3. 切换技能→选择技能（多技能） | 架构变更：单技能锁定 → 主技能 + 附加技能叠加 |

## 二、现状分析

### 2.1 技能数据结构（SkillManifest）
- 标识：`name`（标识符）、`display_name`（用户可改名）、`scene_name`（场景名如子瞻/白圭/清和）
- 描述：`description`（简介）、`scenario`（适用场景）、`scene_profile`（角色/人格/工作流结构化）
- 依赖：`dependencies.tools`（工具白名单 ToolDependency[]）、`permissions`（文件/网络/沙箱权限）
- 模型：`model_scope`（限定模型，空=通用）
- 文件：每个技能 = `skill.yaml`（manifest）+ `system_prompt.md`（提示词）+ `tools.yaml`（工具声明）+ 可选 `examples/`、`kb_assets/`

### 2.2 技能激活流程（当前）
```
前端 handlePickMode(skill) → POST /admin/sessions/{sid}/activate {skill_name}
后端 SkillManager.activate_skill:
  1. loader.load(skill_name) → 加载 skill.yaml + system_prompt.md
  2. _validate_tools → 校验工具白名单引用都在 ToolRegistry 中
  3. 查 sessions.locked_skill_name → 如果已锁定不同 skill → 409 SkillSwitchNotAllowed
  4. build_system_prompt → 模板替换 + 少样本注入
  5. tool_registry.list_tools_for_session(whitelist) → 过滤工具
  6. ContextManager.ensure_initial → 构建 Frozen Zone + frozen_hash
  7. UPDATE sessions SET locked_skill_name, locked_skill_version, frozen_hash
```

### 2.3 技能注入入口（main.py `_get_system_prompt`）
```
locked_skill = SELECT locked_skill_name FROM sessions WHERE id = sid
if session.kind == "monitor": base_prompt = _monitor_system_prompt()
elif not locked_skill: base_prompt = "You are a helpful assistant."
else: base_prompt = SkillManager.build_system_prompt(skill, ...)
# 追加 MCP 工具速查指南
```

### 2.4 前端入口
- **首页模式卡**（HomeView）：三场景 → `handlePickMode` → 新会话激活
- **对话中切换技能弹层**（skillPickerOpen）：单选 → `handlePickMode` → 新会话
- **技能库页**（AgentLibraryView）：`onActivate` → `handlePickMode`
- **输入框**：纯 textarea，无命令解析

### 2.5 上传压缩包（当前）
- 前端 SkillsSection：`uploadZip` → `POST /admin/skills/upload-zip`（FormData）
- 后端：解压 → 解析 → 返回 `{name, files}` 或 `{skills: [...]}`
- **缺失**：无中文翻译、无结构化校验、失败原因不详细

## 三、需求 1：对话中 `/` 召唤技能

### 3.1 交互设计
1. 用户在输入框输入 `/` → 弹出技能浮层（类似 VS Code 命令面板）
2. 浮层显示：技能名（display_name/scene_name）+ 简介（description）+ 适用场景（scenario）
3. 支持模糊搜索（输入 `/off` 匹配 office）+ 键盘上下导航 + Enter 选中 + Esc 关闭
4. 选中后：输入框插入 `/技能名 ` 标记，浮层关闭
5. 发送时：前端解析 `/技能名` 标记 → 提取技能列表 → 附在消息元数据中发送

### 3.2 技术方案

**前端（App.tsx 输入框增强）**：
```tsx
// 新增状态
const [skillSlashOpen, setSkillSlashOpen] = useState(false);
const [slashQuery, setSlashQuery] = useState("");
const [slashIndex, setSlashIndex] = useState(0);

// textarea onKeyDown / onChange 增强
onChange={(e) => {
  const val = e.target.value;
  const lastSlash = val.lastIndexOf("/");
  if (lastSlash !== -1 && val.slice(lastSlash + 1).match(/^[\w\-]*$/) &&
      (lastSlash === 0 || val[lastSlash - 1] === " " || val[lastSlash - 1] === "\n")) {
    setSkillSlashOpen(true);
    setSlashQuery(val.slice(lastSlash + 1));
  } else {
    setSkillSlashOpen(false);
  }
  setInput(val);
}}
```

**技能浮层组件（SkillSlashPicker）**：
- 位置：浮动在输入框上方
- 数据源：`availableSkills`（已有，从 `/admin/skills` 加载）
- 过滤：`slashQuery` 模糊匹配 name/display_name/description
- 每项：display_name（粗体）+ description（灰色）+ scenario（小字）
- 选中：插入 `/skill_name ` 到输入框 + 关闭浮层

**发送时解析**：
```tsx
const sendMessage = () => {
  // 解析 /skill 标记
  const slashRegex = /\/([\w\-]+)/g;
  const mentionedSkills = [];
  let match;
  while ((match = slashRegex.exec(input)) !== null) {
    const skill = availableSkills.find(s => s.name === match[1] && s.enabled);
    if (skill) mentionedSkills.push(match[1]);
  }
  // 附在 WS 消息中
  sendWs({ type: "user_message", session_id, content: input, supplementary_skills: mentionedSkills });
  setInput("");
};
```

**后端（WS user_message 处理增强）**：
```python
# main.py handle_user_message
supplementary_skills = msg.get("supplementary_skills", [])
if supplementary_skills:
    # 临时挂载附加技能到会话(不改变 locked_skill_name)
    for skill_name in supplementary_skills:
        await conn.execute(
            "INSERT INTO session_supplementary_skills (session_id, skill_name, added_turn) VALUES ($1, $2, $3)",
            session_id, skill_name, current_turn
        )
    # 触发 frozen_zone 重建(附加技能改变了 prompt + tools)
    await _rebuild_frozen_zone(session_id, conn)
```

### 3.3 关键决策
- `/` 召唤的技能**持续到会话结束**（加入 session_supplementary_skills 表），用户可在对话顶部"附加技能"区域手动移除
- 同一条消息可 `/技能A /技能B` 召唤多个技能
- 召唤的技能与主技能（locked_skill_name）叠加，不替换

## 四、需求 2：上传压缩包一键识别安装

### 4.1 交互设计
1. 设置 → 技能 Skills → 上传 zip
2. 上传后后端自动：解压 → 校验 → 解析 → 翻译/生成中文元数据
3. 成功：展示技能卡片（名称/简介/适用场景/工具列表/文件数）
4. 失败：展示具体原因（缺少文件/字段/工具引用/格式错误）

### 4.2 技术方案

**后端 upload-zip 增强（admin.py 5419）**：
```python
@router.post("/skills/upload-zip", response_model=None)
async def upload_skill_zip(file: UploadFile):
    # 1. 解压到临时目录
    tmpdir = tempfile.mkdtemp()
    try:
        zipdata = await file.read()
        zipfile.ZipFile(io.BytesIO(zipdata)).extractall(tmpdir)
        # 找 skill.yaml（支持根目录或子目录）
        yaml_path = find_skill_yaml(tmpdir)
        if not yaml_path:
            return JSONResponse(400, {
                "ok": False,
                "errors": [{"field": "skill.yaml", "reason": "压缩包内未找到 skill.yaml"}]
            })
        # 2. 解析 skill.yaml
        manifest = parse_skill_yaml(yaml_path)
        # 3. 结构校验
        errors = validate_skill_structure(manifest, yaml_path.parent)
        if errors:
            return JSONResponse(400, {"ok": False, "errors": errors})
        # 4. 中文名/简介/场景生成（LLM 翻译）
        if not manifest.display_name or is_english(manifest.display_name):
            generated = await generate_chinese_metadata(manifest, cfg)
            manifest.display_name = generated["display_name"]
            manifest.description = generated["description"]
            manifest.scenario = generated["scenario"]
        # 5. 安装（复制到 skills 目录）
        install_skill(manifest, yaml_path.parent, skills_dir)
        # 6. 返回完整信息
        return {
            "ok": True,
            "name": manifest.name,
            "display_name": manifest.display_name,
            "description": manifest.description,
            "scenario": manifest.scenario,
            "tools": [t.name for t in manifest.dependencies.tools],
            "files": count_files(yaml_path.parent),
        }
    finally:
        shutil.rmtree(tmpdir)
```

**结构校验规则（validate_skill_structure）**：
| 校验项 | 失败原因 |
|---|---|
| skill.yaml 存在 | "压缩包内未找到 skill.yaml" |
| name 字段非空 | "skill.yaml 缺少必填字段: name" |
| version 字段非空 | "skill.yaml 缺少必填字段: version" |
| scenario 字段非空 | "skill.yaml 缺少必填字段: scenario" |
| system_prompt.md 存在 | "缺少 system_prompt.md 文件" |
| 工具白名单引用存在 | "工具 '{tool_name}' 不在工具注册表中" |
| name 符合标识符规则 | "name '{name}' 含非法字符（仅允许字母/数字/连字符）" |
| name 不与已有技能冲突 | "技能 '{name}' 已存在，请先删除或改名" |

**LLM 中文翻译/生成（generate_chinese_metadata）**：
```python
async def generate_chinese_metadata(manifest, cfg):
    prompt = f"""请为以下技能生成中文元数据，返回 JSON：
技能标识: {manifest.name}
英文名: {manifest.display_name or manifest.name}
原始描述: {manifest.description}
原始场景: {manifest.scenario}
系统提示词前200字: {system_prompt[:200]}

返回格式: {{"display_name": "中文名(2-4字)", "description": "一句话简介", "scenario": "适用场景描述"}}
要求: 中文名简洁有力，简介说明核心能力，场景说明适用范围。"""
    result = await call_llm(prompt, cfg)  # 用当前模型
    return json.loads(result)
```

**前端 SkillsSection 增强**：
```tsx
// 上传结果展示
{uploadResult?.ok ? (
  <div className="skill-install-card">
    <div>✅ 技能「{uploadResult.display_name}」安装成功</div>
    <div>简介: {uploadResult.description}</div>
    <div>适用场景: {uploadResult.scenario}</div>
    <div>工具: {uploadResult.tools.join(", ")}</div>
    <div>文件数: {uploadResult.files}</div>
  </div>
) : (
  <div className="skill-install-error">
    <div>❌ 安装失败</div>
    {uploadResult?.errors?.map(e => (
      <div key={e.field}>• {e.field}: {e.reason}</div>
    ))}
  </div>
)}
```

## 五、需求 3：切换技能→选择技能（多技能调用）

### 5.1 架构变更

**从单技能锁定到"主技能 + 附加技能"叠加**：

```
当前: sessions.locked_skill_name = "office" (单值)
变更: sessions.locked_skill_name = "office" (主技能, 不变)
新增: session_supplementary_skills 表 (附加技能, 多值)
```

### 5.2 数据库变更

```sql
-- 新增表: 会话附加技能
CREATE TABLE session_supplementary_skills (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    skill_name VARCHAR(100) NOT NULL,
    added_turn INT NOT NULL,        -- 哪一轮添加的
    added_by VARCHAR(20) NOT NULL,  -- 'slash' (/召唤) | 'picker' (弹层选择)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(session_id, skill_name)  -- 同一技能不重复挂载
);
CREATE INDEX idx_sss_session ON session_supplementary_skills(session_id);
```

### 5.3 注入逻辑变更（_get_system_prompt）

```python
async def _get_system_prompt(cfg, session_id, conn):
    locked_skill = await conn.fetchval(
        "SELECT locked_skill_name FROM sessions WHERE id = $1", session_id
    )
    # 主技能 prompt
    if session_kind == "monitor":
        base_prompt = _monitor_system_prompt(...)
    elif not locked_skill:
        base_prompt = "You are a helpful assistant."
    else:
        base_prompt = await skill_mgr.build_system_prompt(skill, ...)

    # 附加技能 prompt 叠加
    supplementary = await conn.fetch(
        "SELECT skill_name FROM session_supplementary_skills WHERE session_id = $1 ORDER BY id",
        session_id
    )
    if supplementary:
       附加_sections = []
        for row in supplementary:
            skill = await loader.load(row["skill_name"], conn)
            附加_sections.append(f"## 附加技能: {skill.manifest.display_name}\n{skill.system_prompt}")
        base_prompt += "\n\n---\n\n# 附加技能（按需调用以下能力）\n\n" + "\n\n".join(附加_sections)

    return base_prompt
```

### 5.4 工具白名单合并

```python
# main.py _build_tools_for_session (当前仅主技能白名单)
async def _build_tools_for_session(cfg, session_id, conn):
    # 主技能白名单
    locked_skill = await conn.fetchval("SELECT locked_skill_name ...", session_id)
    whitelist = set()
    if locked_skill:
        skill = await loader.load(locked_skill, conn)
        whitelist.update(t.name for t in skill.manifest.dependencies.tools if t.enabled)
    # 附加技能白名单(取并集)
    supplementary = await conn.fetch("SELECT skill_name FROM session_supplementary_skills ...", session_id)
    for row in supplementary:
        skill = await loader.load(row["skill_name"], conn)
        whitelist.update(t.name for t in skill.manifest.dependencies.tools if t.enabled)
    return tool_registry.list_tools_for_session(list(whitelist))
```

### 5.5 冲突检测与处理

| 冲突类型 | 检测 | 处理 |
|---|---|---|
| **工具白名单冲突**（同名工具不同 safety_level） | 遍历所有技能的 dependencies.tools | 取**更严格**的 safety_level（safe > elevated > dangerous） |
| **权限冲突**（一个 allow_network=true 另一个 false） | 遍历 permissions | 取**更宽松**（能力叠加，true 优先） |
| **模型冲突**（附加技能 model_scope 不含当前模型） | 对比 session.model_id 与 model_scope | **提示不阻止**（前端显示⚠️，用户可选择是否挂载） |
| **场景冲突**（两个技能 scene_scope 互斥） | 对比 scene_scope | 仅提示，不阻止（scene_scope 为空=通用） |

### 5.6 前端变更

**技能选择弹层改为多选**：
```tsx
// skillPickerOpen 弹层
// 从单选 button → checkbox 列表
{availableSkills.filter(s => s.enabled).map(s => (
  <label key={s.name} style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 14px" }}>
    <input
      type="checkbox"
      checked={supplementarySkills.includes(s.name)}
      onChange={(e) => {
        if (e.target.checked) {
          setSupplementarySkills([...supplementarySkills, s.name]);
        } else {
          setSupplementarySkills(supplementarySkills.filter(n => n !== s.name));
        }
      }}
    />
    <div>
      <div style={{ fontWeight: 600 }}>{s.display_name || s.name}</div>
      <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{s.description}</div>
      <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>场景: {s.scenario}</div>
      {hasModelConflict(s) && <span style={{ color: "var(--warning-text)" }}>⚠️ 当前模型不在推荐范围</span>}
    </div>
  </label>
))}
// 确认按钮
<button onClick={() => {
  void addSupplementarySkills(supplementarySkills);
  setSkillPickerOpen(false);
}}>挂载选中技能</button>
```

**对话顶部显示附加技能**：
```tsx
// chat 顶部 chip 区域, 主技能 chip 旁边
{supplementarySkillList.map(s => (
  <span key={s.name} style={{
    fontSize: 12, padding: "2px 10px", borderRadius: 10,
    background: "var(--accent-soft-bg)", color: "var(--accent-soft-text)",
    cursor: "pointer",  // 点击可移除
  }} onClick={() => void removeSupplementarySkill(s.name)}>
    {s.display_name} ×
  </span>
))}
```

**顶部"切换技能"按钮文案改为"选择技能"**：
```tsx
// 原: 🔄 切换技能
// 改: ➕ 选择技能
<button onClick={() => setSkillPickerOpen(true)} title="选择附加技能（可多选）">
  ➕ 选择技能
</button>
```

## 六、实施步骤与工作量

### Phase 1: 需求 2（上传增强）— 最小改动、独立交付
| 步骤 | 文件 | 工作量 |
|---|---|---|
| 1. 后端 upload-zip 增加结构校验 | admin.py | 2h |
| 2. 后端 LLM 中文翻译/生成 | admin.py + 新 helper | 2h |
| 3. 前端上传结果卡片 + 失败详情 | SettingsView.tsx | 1.5h |
| 4. 测试 | 新建 test | 1h |
| **小计** | | **~6.5h** |

### Phase 2: 需求 3（多技能架构）— 核心变更、影响面大
| 步骤 | 文件 | 工作量 |
|---|---|---|
| 1. DB migration: session_supplementary_skills | migrations.py, schema.sql | 1h |
| 2. 后端注入逻辑: _get_system_prompt 合并 | main.py | 2h |
| 3. 后端工具白名单合并 | main.py | 1.5h |
| 4. 后端冲突检测 | 新建 conflict_checker.py | 2h |
| 5. 后端 API: add/remove supplementary skills | admin.py | 1.5h |
| 6. 后端 frozen_zone 重建机制 | main.py | 1h |
| 7. 前端弹层改多选 | App.tsx | 2h |
| 8. 前端附加技能 chip 展示 + 移除 | App.tsx | 1h |
| 9. 前端按钮文案改"选择技能" | App.tsx | 0.5h |
| 10. 测试 | 新建 test | 2h |
| **小计** | | **~14.5h** |

### Phase 3: 需求 1（/ 召唤）— 依赖 Phase 2 的附加技能机制
| 步骤 | 文件 | 工作量 |
|---|---|---|
| 1. 前端输入框 / 命令解析 | App.tsx | 2h |
| 2. 技能浮层组件 SkillSlashPicker | 新建组件 | 2h |
| 3. 发送时解析 / 标记 + 附带元数据 | App.tsx | 1h |
| 4. 后端 WS user_message 处理 supplementary_skills | main.py | 1.5h |
| 5. 测试 | 新建 test | 1h |
| **小计** | | **~7.5h** |

### 总工作量：~28.5h（约 3.5 个工作日）

## 七、风险评估

| 风险 | 等级 | 缓解 |
|---|---|---|
| **Frozen Zone hash 不稳定**：附加技能加入后 frozen_hash 变化，旧会话走 replace_frozen_zone 重建 | 中 | 已有 replace_frozen_zone 机制（V2 P2），验证附加技能增删后 hash 重建正确 |
| **多技能 prompt 过长**：叠加多个 system_prompt 可能超出上下文窗口 | 中 | 附加技能只注入精简版（核心指令 + 工具说明），不注入完整 system_prompt |
| **工具白名单合并冲突**：同名工具不同配置 | 低 | 取更严格 safety_level，有日志告警 |
| **LLM 中文翻译质量**：生成的中文名/简介不准确 | 低 | 用户可在安装后手动修改 display_name/description（已有 PUT /skills/{name}/meta） |
| **`/` 命令误触发**：用户输入路径（如 /outputs/file.png）误触发技能浮层 | 低 | 仅在 `/` 前为空格/行首时触发；路径含 `/` 但前后非空格不触发 |
| **DB migration 风险**：新增表可能影响已有会话 | 低 | 新表与 sessions 通过 FK CASCADE，不影响已有数据；附加技能默认为空 |

## 八、建议实施顺序

1. **Phase 1 先行**（需求 2）：独立、低风险、用户立即可用
2. **Phase 2 核心**（需求 3）：架构变更，需充分测试 frozen_zone 重建 + 工具合并
3. **Phase 3 最后**（需求 1）：依赖 Phase 2 的附加技能机制，在前者稳定后叠加

---

> 本方案为 Plan 模式产出，待蒋先生确认后进入执行。
