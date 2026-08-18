# next-phase-plan: P1-3 上传链路收口（multipart 双格式）

日期: 2026-08-17 | 类型: 阻塞项收口 | 前置: P0/P1 实机验收完成

## 背景与目标

审计 I4（上传主线程冻结）+ 路线图 P1-3: 知识库文件上传当前走 base64 JSON
（前端读文件 → base64 → JSON body → 后端解码）。10MB 文件 base64 编码
在主线程执行会导致界面冻结; multipart 由浏览器原生处理, 不占 JS 主线程。

**目标**: 上传链路改为 multipart/form-data 优先 + base64 JSON 兼容回退,
前端上传中进度反馈（已在上一轮落地）; 全链路验证通过。

## 现状取证

- 后端: `POST /admin/knowledge/upload-file`（admin.py:1068）仅接受 JSON
  `KnowledgeFileUploadRequest{filename, content_base64, scenario}`。
- 依赖: pyproject.toml **未声明** python-multipart（运行时环境已装 0.0.32,
  backup/skill_zip 已用 UploadFile, FastAPI 依赖 multipart 但未显式声明）。
- 测试: test_admin_knowledge.py 5 处 `json=` base64 调用, 2 个用例。
- 前端: KnowledgeView.uploadFile 单路径 base64; 进度反馈 + 失败 Toast 已落地。

## 方案设计

### 1. 后端: 单一路径双格式（不破坏旧前端）

`POST /admin/knowledge/upload-file` 改签名: `async def knowledge_upload_file(request: Request)`。
按 Content-Type 分派:

- `multipart/form-data`: `await request.form()` → `file: UploadFile` + `scenario: Form|None`。
  读取字节(≤10MB 校验) → 与原 JSON 分支共用同一处理函数
  `_kb_ingest(filename, content_bytes, scenario)`（抽公共函数）。
- `application/json`（deprecated, 保留一个版本周期, 标记注释）: 走原
  base64 解码逻辑 → 同一 `_kb_ingest`。

新旧路径共享 `_kb_ingest`: b64 分支先解码, multipart 分支直接用字节;
之后（大小校验 → utf-8/gbk 解码 → process_document）完全一致。

### 2. pyproject.toml: 声明 python-multipart

dependencies 加 `"python-multipart>=0.0.9"`（FastAPI 表单解析所需,
消除"运行时能用但未声明"的隐式依赖）。

### 3. 前端: multipart 优先 + base64 回退

KnowledgeView.uploadFile 双路径:

```
try multipart(FormData: file + scenario) → ok?
  → 成功: 更新库列表 + Toast(成功)
  失败(HTTP !=2xx): 抛错
catch → 回退 base64 JSON(原路径)
```

回退条件: multipart 端点异常（后端未部署新代码/网络层失败）。
回退路径标记 console.warn 便于诊断。保持上一轮已落地的
"正在上传 N MB…" 进度文案 + 完成 Toast。

### 4. 测试

- 后端 test_admin_knowledge.py 新增:
  - `test_kb_upload_multipart`: `files={"file": ("a.md", b"...", "text/markdown")}`
    + `data={"scenario": "test"}` → 200 且 doc_id/chunks > 0。
  - 现有 json= 用例保留（验证兼容分支不回归）。
- 前端: uploadFile 双路径逻辑抽 `uploadKbFile()` 纯函数 + 2 用例
  （multipart 成功 / multipart 失败回退 base64）。

### 5. 验证

- 后端 pytest（加载 .env, 单进程, 测试库私有 schema）全过。
- 前端 tsc 0 错 + vitest 全过。
- 实机: 10MB 文本上传 → 无冻结、完成 Toast、文档可检索。

## 风险与回退

- multipart 解析依赖 python-multipart: 环境已装; pyproject 声明后 pip install
  会补齐（用户环境 unset PYTHONPATH 后再装, 项目惯例）。
- 若 multipart 在打包版（extraResources 内置 backend）未带 multipart:
  前端回退 base64 保证可用（兼容分支价值所在）。
- 不删除 JSON 分支: 标记 deprecated, P3 清理周期再移除。

## 验收标准

1. 前端上传 10MB 文件主线程无冻结;
2. 成功/失败均走 Toast;
3. multipart 主路径与 base64 回退路径均有测试覆盖;
4. pytest + tsc + vitest 全过。
