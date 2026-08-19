# MVP 运行实测 BUG 与修复记录

> 记录时间:2026-08-01
> 场景:浏览器模式实机运行 MVP(vite 前端 5173 + Python Sidecar 后端 8765 + PostgreSQL),接入 deepseek-v4-pro 测试聊天功能
> 状态:全部 BUG 已修复并验证,全量 793 个测试通过

## 一、实测环境与启动方式

| 组件 | 端口 | 启动命令(backend 目录) |
| --- | --- | --- |
| 后端 Sidecar | 8765 | `$env:WORKSPACE="D:\Private agent\backend"; $env:PA_DB_PASSWORD="123123"; $env:PA_DEEPSEEK_API_KEY="<key>"; python -m private_agent.main` |
| 前端 vite | 5173 | `npm run dev`(frontend 目录) |

必需环境变量:

| 变量 | 说明 |
| --- | --- |
| `PA_DB_PASSWORD` | PostgreSQL 密码,缺省则启动时 DB 连接失败(仅 warning,不阻断启动) |
| `PA_DEEPSEEK_API_KEY` | deepseek provider 的 API Key;其余 provider 未配置时默认 `test-key` 会 401,由 fallback 链降级 |
| `WORKSPACE` | 工作区根目录,驱动 config.yaml 中 `${WORKSPACE}` 占位符 |

## 二、BUG 清单

### BUG-1:前端打不开,http://localhost:5173/ 返回 404

- **现象**:访问前端根路径返回 404,页面空白。
- **根因**:frontend 根目录缺少 vite 入口 `index.html`(只有 renderer 目录下的 React 源码),vite 根路径无可服务文件。
- **修复**:新建 [frontend/index.html](file:///d:/Private%20agent/frontend/index.html)(`#root` 容器 + 引用 `/renderer/main.tsx`)与 [frontend/renderer/main.tsx](file:///d:/Private%20agent/frontend/renderer/main.tsx)(`createRoot` 挂载 `<App />`)。
- **验证**:`GET /` 返回 200;`/renderer/main.tsx`、`/renderer/App.tsx` vite 转译均 200;浏览器无报错。

### BUG-2:后端启动 DB 连接失败(环境配置)

- **现象**:启动日志 `DB pool creation failed at startup: 环境变量 PA_DB_PASSWORD 未设置`。
- **根因**:config.yaml `database.password_env: "PA_DB_PASSWORD"` 从环境变量读密码,未设置导致连接池创建失败(启动钩子仅 warning 不阻断,后续 WS 消息会失败)。
- **修复**(运行侧):启动前设置 `PA_DB_PASSWORD` 环境变量,重启后日志出现 `DB schema migrated (idempotent)`,健康检查 OK。
- **备注**:属环境配置问题,非代码缺陷;已在"实测环境与启动方式"中固化。

### BUG-3:deepseek-v4-pro 纯推理模型 content 恒为空

- **现象**:直连 `https://tokenrhythm.studio/v1/chat/completions` 返回 HTTP 200,但 `choices[0].message.content` 恒为空字符串,全部输出消耗在 `reasoning_content` 字段。
- **根因**:deepseek-v4-pro 为纯推理模型,输出只走 `reasoning_content`,不走 `content`。
- **修复**([adapters/__init__.py](file:///d:/Private%20agent/backend/private_agent/models/adapters/__init__.py)):`_parse_openai_response` 中 content 为空时回退读取 `reasoning_content`。
- **测试**:新增 2 个用例(content 空回落 reasoning / content 非空时忽略 reasoning),共 10 个全部通过。
- **验证**:WS 聊天链路返回完整回复内容。

### BUG-4:WS 聊天返回 user_message_failed(1)——sessions 外键失败

- **现象**:`_test_chat.py` 通过 WS 发送 `user_message` 后收到 `{"type":"error","message":"user_message_failed"}`。
- **根因**:`ContextManager.ensure_initial` → `build_initial` 向 `messages` 表插入 Frozen Zone 记录时,`session_id` 在 `sessions` 表不存在,违反外键 `messages_session_id_fkey`(`ForeignKeyViolationError`)。
- **修复**([main.py](file:///d:/Private%20agent/backend/private_agent/main.py)):WS `user_message` 分支增加**会话懒创建** —— `SELECT 1 FROM sessions WHERE id=$1` 为空时先 `INSERT INTO sessions (id, title)` 再走后续流程。
- **验证**:本地复现脚本 + 端到端 WS 均通过,收到 thinking/final 事件。

### BUG-5:WS 聊天返回 user_message_failed(2)——ToolDef 不可 JSON 序列化

- **现象**:修完 BUG-4 后仍返回 `user_message_failed`,日志无 traceback(JSON formatter 吞掉异常详情)。
- **根因**:`ReactLoop.run_turn` 直接把 `ToolDef` 对象列表传给 `adapter.chat(tools=...)`,httpx 序列化 body 时抛 `TypeError: Object of type ToolDef is not JSON serializable`。adapter 期望 OpenAI tools schema dict。
- **修复**([react_loop.py](file:///d:/Private%20agent/backend/private_agent/core/react_loop.py)):构造函数预计算 `self._tool_schemas = [t.to_openai_schema() for t in tools]`,`run_turn` 调用 adapter 时改传 `_tool_schemas`。
- **验证**:本地复现脚本打印 `run_turn OK` + thinking/final 事件;WS 端到端通过;相关 37 个测试(react_loop/adapters/ws)全部通过。
- **备注**:排查技巧 —— 后端日志 JSON formatter 未记录 exception traceback,采用独立脚本完整复现 WS 分支逻辑定位根因。

### BUG-6:前端技能列表加载失败(跨域 CORS 拦截)

- **现象**:页面显示"加载技能列表失败,请确认后端服务已启动",实际 `GET /admin/skills` 返回 HTTP 200 且含 3 个技能。
- **根因**:前端页面运行在 `http://localhost:5173`,请求 `http://localhost:8765/admin/skills` 属跨域,后端 FastAPI 未配置 CORS 中间件,浏览器拦截响应 → 前端走 catch 分支。
- **修复**([main.py](file:///d:/Private%20agent/backend/private_agent/main.py)):添加 `CORSMiddleware`(`allow_origins=["*"]`,本地 sidecar 场景;`allow_credentials=False`)。
- **测试**:新增 [test_main_cors.py](file:///d:/Private%20agent/backend/tests/test_main_cors.py) 验证带 `Origin` 请求返回 `access-control-allow-origin` 头,通过。
- **验证**:`Invoke-WebRequest` 携带 `Origin: http://localhost:5173` 返回 `CORS: *` + 3 个技能 JSON。

### BUG-7:点击激活技能返回 session_not_found

- **现象**:技能列表正常显示,点击"激活"后提示 `session_not_found`。
- **根因**:前端 URL 无 `session_id` 参数时,`getSessionIdFromUrl()` 生成随机占位 ID;激活发生在首条 WS 消息之前,`POST /admin/sessions/{id}/activate` 校验 session 必须已存在 → 404。
- **修复**([admin.py](file:///d:/Private%20agent/backend/private_agent/api/admin.py)):`activate_skill` 端点与 WS 一致增加**会话懒创建**(session 不存在先 INSERT 再激活)。
- **测试**([test_admin_activate_skill.py](file:///d:/Private%20agent/backend/tests/test_admin_activate_skill.py)):404 用例改为"懒创建成功"用例(mock conn 增加 execute 记录),5 个用例全过。
- **验证**:真实 DB 随机 session_id 激活返回 `200 {"locked_version":"1.0.0","frozen_hash":"..."}`。

### BUG-8:前端端口占用(运行环境)

- **现象**:新起 vite 时提示 `Port 5173 is in use, trying another one...`,实例跑到 5174。
- **根因**:旧 vite 实例仍在监听 5173(启动于 BUG-1 修复前,vite 动态读文件,已自动服务修复后的入口)。
- **处理**:保留 5173 旧实例(已服务最新文件),停止多余的 5174 实例。

## 三、修复共性小结

1. **会话生命周期**:生产聊天/激活流程均未创建 `sessions` 行,存在两处外键/404 失败;统一采用"懒创建"(WS `user_message` + `activate_skill`)解决,前端随机 session_id 即可全流程使用。
2. **类型契约**:adapter 层期望 OpenAI tools schema dict,`ContextManager`/`ReactLoop` 持有 `ToolDef` 对象,传递前需 `to_openai_schema()` 转换。
3. **浏览器模式跨域**:Sidecar 服务前端浏览器访问必须配置 CORS,否则 fetch 静默失败且后端看起来正常。
4. **日志可观测性**:当前 JSON formatter 不落 exception traceback,线上排查需依赖独立复现脚本,建议后续改进日志配置记录 exc_info。

## 四、最终验证状态

- 全量测试:`793 passed, 0 failed`(含本次新增 CORS/懒创建/reasoning 回退用例)。
- WS 聊天链路:`user_message` → ReactLoop → deepseek-v4-pro → thinking/final 事件,回复完整。
- 技能激活链路:技能列表(3 个场景)→ 激活锁定(懒创建 session)→ 返回 locked_version + frozen_hash。
- 前端页面:http://localhost:5173/ 技能选择 → 激活 → 聊天,全流程可用。
