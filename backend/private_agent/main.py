"""蓝图 §2.15 Python Sidecar 启动入口 (§2.2 uvicorn+asyncio).

集成 /health 控制面、/ws 数据面,端口与日志由 config.yaml 驱动(蓝图 §9.13)。
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from private_agent.api import admin, eval, files
from private_agent.config import loader
from private_agent.core.checkpoint import CheckpointManager
from private_agent.core.context_manager import ContextManager, FrozenHashMismatchError
from private_agent.core.react_loop import ReactLoop
from private_agent.core.reflection import ReflectionEngine
from private_agent.eval.online_failure_collector import OnlineFailureCollector
from private_agent.eval.repos import ReviewQueueRepo
from private_agent.memory.manager import MemoryManager
from private_agent.memory.memories_repo import MemoriesRepo
from private_agent.observability.logging import setup_logger
from private_agent.security.auth import ensure_admin_token, require_admin
from private_agent.skills.evolution_repo import EvolutionRepo
from private_agent.storage import db, ws_offset

app = FastAPI(title="Private Agent Sidecar", version="0.1.0")

# ── 阶段二批次 1: CORS 白名单收窄(审查 A.3.10/B.2.1) ──────────────────────────
# 原 allow_origins=["*"] 全开 → 白名单(config security.cors.allow_origins 可覆盖)
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",   # vite dev 默认端口
    "http://127.0.0.1:5173",
    "http://localhost:4173",   # vite preview
    "http://127.0.0.1:4173",
    "app://.",                 # Electron 生产(file:// 场景 origin 为 app://.)
]


def _cors_origins() -> list[str]:
    """CORS 白名单: config security.cors.allow_origins > 默认本机 dev 端口。"""
    try:
        origins = loader.load_config().get("security", {}).get("cors", {}).get(
            "allow_origins"
        )
        if origins:
            return list(origins)
    except Exception:  # noqa: BLE001
        pass
    return list(_DEFAULT_CORS_ORIGINS)


def _cors_dev_regex() -> str | None:
    """dev 模式放宽: PA_ENV=dev(vite 端口占用自动切换 5174 等)或 config 显式
    dev_wildcard=true 时, 放行 localhost/127.0.0.1 任意端口。"""
    if os.environ.get("PA_ENV") == "dev":
        return r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    try:
        cfg = loader.load_config().get("security", {}).get("cors", {})
        if cfg.get("dev_wildcard", False):
            return r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    except Exception:  # noqa: BLE001
        pass
    return None


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=_cors_dev_regex(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "X-Admin-Token", "Authorization"],
)

# 阶段二批次 1: admin/eval 控制面统一挂鉴权(router 级单点生效;
# 独立 app 的单元测试不受影响, 生产入口 main.app 全量保护)
# 2026-08-04 修复: files.router 不再挂 require_admin —— /files/outputs/* 是
# 纯产物读取(壁纸/预览图), 前端 <img>/<video> src 直连不带 X-Admin-Token,
# 整体鉴权导致壁纸 401 永远加载失败; 该路由无写/删操作, 公开可读可接受。
app.include_router(admin.router, dependencies=[Depends(require_admin)])
app.include_router(eval.router, dependencies=[Depends(require_admin)])
app.include_router(files.router)

# B1 P1-2: 模块级仅持有 logger 句柄(无 handler),file_path 由 _on_startup / run_sidecar 延迟配置
_logger = logging.getLogger("private_agent.main")

_scheduler = None  # APScheduler 单例(startup 创建,shutdown 停止)
# MCP 外轨工具管理器(进程级单例, 懒连接 + 缓存, shutdown 时关闭)
_mcp_manager = None  # type: ignore[assignment]
# V2 P1: per-session 运行锁(user_message 改 create_task 后防同会话并发 turn 冲突)
_session_locks: dict[int, "asyncio.Lock"] = {}
# 打断/停止: per-session 运行 task 集合(T-3 架构修订 P0-4 修复——
# 原单槽 dict 被并发 user_message 覆盖导致 cancel 打错目标)
_session_tasks: dict[int, "set[asyncio.Task]"] = {}
# V2 P1: per-session 权限确认管理器(蓝图 §5.12, tool_confirmation 消息 resolve)
_permission_managers: dict[int, "PermissionManager"] = {}
# V1.5 项-5: per-session 流程级暂停控制器(生成中挂起, 区别于 cancel 终止)
_pause_controls: dict[int, "_PauseController"] = {}


class _PauseController:
    """会话级流程暂停控制器(项-5)。

    暂停语义: 生成中用户点"暂停" → is_paused()=True + event 清空,
    ReactLoop 迭代开始检查时产出 turn_paused 并 await wait() 挂起;
    "继续" → is_paused()=False + event 置位, 挂起的循环继续。
    初始状态为"未暂停"(event 置位, wait() 立即返回)。
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._event.set()  # 默认未暂停
        self._paused = False

    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True
        self._event.clear()

    def resume(self) -> None:
        self._paused = False
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


def _get_pause_controller(session_id: int) -> _PauseController:
    """惰性创建会话级暂停控制器。"""
    ctrl = _pause_controls.get(session_id)
    if ctrl is None:
        ctrl = _PauseController()
        _pause_controls[session_id] = ctrl
    return ctrl


def _get_permission_manager(session_id: int):
    """惰性创建会话级 PermissionManager(缓存确认结果按会话隔离)。"""
    global _permission_managers
    pm = _permission_managers.get(session_id)
    if pm is None:
        from private_agent.tools.permission_manager import PermissionManager

        pm = PermissionManager(timeout=60.0)
        _permission_managers[session_id] = pm
    return pm


async def _build_skill_permission_rules(conn, session_id: int, cfg: dict) -> list:
    """从会话锁定 Skill 的 manifest.permissions 构建权限规则(阶段三批次1/3)。

    映射(蓝图 §7.2 SkillPermissions + 阶段三 T3.1 细粒度 rules → 规则 DSL):
    - allow_file_write → allow:file_write + allow:file_read
    - allow_network → allow:http_request + allow:web_search
    - permissions.rules[].paths → allow:Tool(path 模式)(source=skill)
    - permissions.rules[].domains → allow:Tool(*domain*)(匹配 args.url)

    Returns:
        PermissionRule 列表(无锁定 Skill 或未声明 → 空列表)。
    """
    from private_agent.skills.loader import SkillLoader
    from private_agent.tools.permission import parse_rule

    locked_skill = await conn.fetchval(
        "SELECT locked_skill_name FROM sessions WHERE id = $1", session_id
    )
    if not locked_skill:
        return []
    try:
        loader = SkillLoader.from_cfg(cfg)
        skill = await loader.load(locked_skill, conn)
    except Exception:  # noqa: BLE001 - Skill 加载失败不阻塞权限链路
        return []
    perms = skill.manifest.permissions
    rules: list = []
    if perms.allow_file_write:
        rules.append(parse_rule("allow:file_write", source="skill"))
        rules.append(parse_rule("allow:file_read", source="skill"))
    if perms.allow_network:
        rules.append(parse_rule("allow:http_request", source="skill"))
        rules.append(parse_rule("allow:web_search", source="skill"))
    # T3.1: 细粒度规则声明 → 带 specifier 的 allow 规则
    for r in getattr(perms, "rules", []) or []:
        for path in r.paths or []:
            try:
                rules.append(
                    parse_rule(f"allow:{r.tool}({path})", source="skill")
                )
            except Exception:  # noqa: BLE001 - 单条非法规则跳过
                continue
        for domain in r.domains or []:
            try:
                rules.append(
                    parse_rule(f"allow:{r.tool}(*{domain}*)", source="skill")
                )
            except Exception:  # noqa: BLE001
                continue
    return rules


async def _sync_permission_manager(pm, conn, session_id: int, cfg: dict) -> None:
    """同步会话级权限模式与 Skill 规则(阶段三批次1 T1.2/T3.1)。

    - 模式变化时 set_mode(内部清缓存, 旧确认不再适用);
    - 规则每次重建, 仅变化时 set_rules(不频繁触发)。
    """
    mode = (
        await conn.fetchval(
            "SELECT permission_mode FROM sessions WHERE id = $1", session_id
        )
        or "default"
    )
    if mode != pm.mode:
        pm.set_mode(mode)
    rules = await _build_skill_permission_rules(conn, session_id, cfg)
    if rules != pm.rules:
        pm.set_rules(rules)


def _get_mcp_manager():
    """惰性初始化 MCPToolManager(避免 import 时产生循环依赖)。"""
    global _mcp_manager
    if _mcp_manager is None:
        from private_agent.tools.mcp_tools import MCPToolManager

        _mcp_manager = MCPToolManager()
    return _mcp_manager


def _build_adapter(cfg):
    """构造模型适配器(默认 FallbackChain,测试可 monkeypatch)。"""
    from private_agent.models.registry import build_fallback_chain
    return build_fallback_chain(cfg)


def _build_session_adapter(cfg, model_id: str | None):
    """按会话选择的模型构建 adapter(对话级模型选择)。

    - model_id 为空或 'auto' → fallback 链(自动模式, 可降级)
    - model_id 为具体 provider → 单模型 FallbackChain(手动模式, 锁定该模型,
      失败不降级, 直接报错提示)
    """
    from private_agent.models.base import FallbackChain
    from private_agent.models.registry import build_fallback_chain, get_adapter

    if not model_id or model_id == "auto":
        return build_fallback_chain(cfg)
    adapter = get_adapter(model_id, cfg)
    return FallbackChain([adapter])


def _build_contextual_adapter(cfg, model_id: str | None):
    """0.5.1(2026-08-10 双链架构): 构建按语境路由的 (text, vision) 双链。

    - 自动模式: text_chain(纯文本优先) / vision_chain(多模态优先),
      均未配置时回退 fallback_chain(向后兼容)。
    - 会话手动锁定: 锁定模型作为 text 链; 若锁定模型是多模态则同时作
      vision 链, 否则 vision 用 vision_chain(发图语境自动切换, 不锁死)。
    """
    from private_agent.models.base import FallbackChain
    from private_agent.models.registry import build_fallback_chain, get_adapter

    text_chain = build_fallback_chain(cfg, "text_chain")
    vision_chain = build_fallback_chain(cfg, "vision_chain")
    if not model_id or model_id == "auto":
        return text_chain, vision_chain
    locked = get_adapter(model_id, cfg)
    locked_vision = getattr(getattr(locked, "capability", None), "vision", False)
    vision = locked if locked_vision else vision_chain
    return FallbackChain([locked]), vision


def _build_compress_adapter(cfg):
    """构造压缩模型适配器(蓝图 §4.2,spec AC-7),测试可 monkeypatch。"""
    from private_agent.models.registry import build_compress_adapter
    return build_compress_adapter(cfg)


def _build_hook_runner(cfg):
    """构造 Hooks 调度器(阶段三批次2 B-1)。

    从 cfg['hooks'](config.yaml + config_runtime 合并)解析 HookConfig;
    空配置 → None(默认零回归)。mcp_tool 类型 hook 注入 MCP 调用回调。
    """
    from private_agent.core.hooks import HookRunner

    hooks = cfg.get("hooks") or []
    if not hooks:
        return None
    try:
        configs = HookRunner.configs_from_list(hooks)
    except Exception:  # noqa: BLE001 - 配置非法时禁用 hooks 不崩溃
        return None
    if not configs:
        return None
    mcp_call = None
    if any(c.type == "mcp_tool" for c in configs):
        async def _mcp_call(server: str, tool: str, payload: dict) -> dict:
            mgr = _get_mcp_manager()
            result = await mgr.call_tool(server, tool, payload)
            return {"result": result}

        mcp_call = _mcp_call
    return HookRunner(hooks=configs, mcp_call=mcp_call)


async def _get_frozen_tools(cfg, session_id: int, conn):
    """内置工具按 skill 白名单过滤(与 activate_skill 完全同源)。

    用于 ContextManager 的 Frozen Zone hash 计算——MCP 工具属运行时扩展,
    不参与锁定 hash, 保证 activate 时与 WS 处理时 hash 一致。

    - session 未 activate → 全部内置工具
    - session 已 activate → skill manifest.dependencies.tools 白名单过滤(AC-3)
    """
    from private_agent.skills.loader import SkillLoader
    from private_agent.tools.builtins import register_all_builtins
    from private_agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_all_builtins(registry)

    locked_skill = await conn.fetchval(
        "SELECT locked_skill_name FROM sessions WHERE id = $1",
        session_id,
    )
    if not locked_skill:
        return registry.list_tools()

    loader = SkillLoader.from_cfg(cfg)
    skill = await loader.load(locked_skill, conn)
    whitelist = [t.name for t in skill.manifest.dependencies.tools if t.enabled]
    return registry.list_tools_for_session(whitelist)


async def _get_tools(cfg, session_id: int, conn):
    """获取工具列表: 内置白名单(frozen 同源) + MCP 外轨(按 skill 绑定过滤)。

    流畅度优化(方向一): MCP server 按当前会话 skill 的绑定列表装配
    (config tools.mcp.skill_binding), 未绑定的 server 不装配 —— 工具池
    从 231 收敛到 skill 相关子集; 每轮再由 ToolSelector 选 top-N 注入模型。

    - frozen_tools 供 ContextManager hash 锁定(与 activate 一致)
    - 全部工具供 ReactLoop 调用(内置 + mcp__ 前缀工具)
    """
    frozen_tools = await _get_frozen_tools(cfg, session_id, conn)
    # skill → MCP server 绑定(会话锁定 skill 决定装配哪些 MCP)
    server_ids: list[str] | None = None
    try:
        skill_name = await conn.fetchval(
            "SELECT locked_skill_name FROM sessions WHERE id = $1",
            session_id,
        )
        if skill_name:
            binding = (
                cfg.get("tools", {}).get("mcp", {}).get("skill_binding", {}) or {}
            )
            bound = binding.get(skill_name)
            if bound is not None:
                server_ids = list(bound)
    except Exception:
        server_ids = None
    mcp_tools = await _get_mcp_manager().get_tools(cfg, server_ids)
    return frozen_tools + mcp_tools


# 项目优化(Hermes SOUL.md 借鉴): 稳定身份层, 永远在 system prompt 首位。
# 单文件静态定义, 跨会话一致; config context.identity 可覆盖。
_DEFAULT_IDENTITY = (
    "你是 Private Agent —— 运行在用户本机的个人桌面智能体。\n"
    "协作规则:\n"
    "1. 提建议时给出明确选项 + 理由, 不要列开放式菜单。\n"
    "2. 安全明确的任务直接执行再汇报, 不先请示; 危险/不可逆操作先确认。\n"
    "3. 回答基于证据、结构清晰、没有废话; 不确定时明确承认。\n"
    "4. 使用与用户一致的语言交流。"
)


def _identity_prompt(cfg: dict) -> str:
    """构造身份段(SOUL 层): config 覆盖 > 内置默认。"""
    configured = (cfg.get("context", {}) or {}).get("identity", "")
    return (configured or _DEFAULT_IDENTITY).strip()


async def _monitor_system_prompt(cfg, session_id: int, conn):
    """0.5.0 P3: 主智能体(monitor)专属系统提示词 + 实时指标摘要注入。

    读取 skills/monitor/system_prompt.md, 末尾附加最近指标摘要
    (collector.latest_summary), 让主智能体会话启动即感知系统状态。
    """
    from pathlib import Path

    # monitor 提示词文件(内置, 非 skill.yaml 驱动)
    prompt_path = Path(__file__).resolve().parents[2] / "skills" / "monitor" / "system_prompt.md"
    try:
        base = prompt_path.read_text(encoding="utf-8")
    except OSError:
        base = "你是系统监控与优化者(monitor)。"
    # 追加实时指标摘要(注入上下文, 供模型分析)
    try:
        collector = getattr(app.state, "metrics_collector", None)
        if collector is not None:
            summary = await collector.latest_summary(conn, since_hours=1.0)
            if summary:
                base = f"{base}\n\n{summary}"
    except Exception:  # noqa: BLE001 - 指标摘要注入失败不影响提示词
        pass
    return base


async def _get_system_prompt(cfg, session_id: int, conn):
    """获取系统提示词(测试可 monkeypatch)。

    - session 未 activate (locked_skill_name IS NULL) → 返回默认提示词(M1 行为)
    - session 已 activate → 返回锁定 skill 的 system_prompt(模板替换 + 少样本,
      与 activate_skill 生成完全一致,保证 Frozen Zone hash 稳定,AC-3)
    - V2 P2: 追加 MCP 工具速查指南(按 server 分类的工具名清单, 帮助模型
      选对工具; 完整 schema 已在 tools 字段)。注意: 该文本变化会改变
      frozen_hash → 旧会话走 replace_frozen_zone 自动重建(已有机制)。
    """
    from private_agent.skills.example_loader import ExampleLoader
    from private_agent.skills.loader import SkillLoader
    from private_agent.skills.manager import SkillManager
    from private_agent.tools.builtins import register_all_builtins
    from private_agent.tools.mcp_tools import build_tools_guide
    from private_agent.tools.registry import ToolRegistry

    locked_skill = await conn.fetchval(
        "SELECT locked_skill_name FROM sessions WHERE id = $1",
        session_id,
    )
    # 0.5.0 P3: monitor 会话(主智能体) → 专属监控提示词
    session_kind = await conn.fetchval(
        "SELECT kind FROM sessions WHERE id = $1", session_id
    )
    if session_kind == "monitor":
        base_prompt = _monitor_system_prompt(cfg, session_id, conn)
    elif not locked_skill:
        base_prompt = "You are a helpful assistant."
    else:
        loader = SkillLoader.from_cfg(cfg)
        skill = await loader.load(locked_skill, conn)
        registry = ToolRegistry()
        register_all_builtins(registry)
        mgr = SkillManager(
            loader=loader,
            example_loader=ExampleLoader.from_cfg(cfg),
            tool_registry=registry,
        )
        base_prompt = await mgr.build_system_prompt(
            skill, locked_skill, session_id, conn
        )

    # V2 P2: MCP 工具速查指南(读已装配的 tools cache, 不重复连接)
    # 项目优化(Hermes SOUL.md 借鉴): 身份段置于 system prompt 最前
    identity = _identity_prompt(cfg)
    if identity:
        base_prompt = f"{identity}\n\n{base_prompt}"
    # 0.5.1 B+C(蒋先生反馈 2026-08-09): 文件落地约定 + 决策输出约束。
    # 所有会话统一注入: ①LLM 明确"工作区/uploads/沙箱目录"约定, 不再猜路径;
    # ②需要用户决策时选项必须写入可见回复(禁止只写推理中 → 用户看不到)。
    # 注意: 文本变化改变 frozen_hash → 旧会话 replace_frozen_zone 自动重建。
    try:
        ws_root = os.path.expandvars(
            str(cfg.get("system", {}).get("workspace_root", ""))
        )
        # 多模态能力声明(蒋先生反馈 2026-08-09): 链上存在 multimodal 模型时
        # 告知 AI 具备图片识别, 避免主动否认"没有图片识别功能"。
        vision_note = ""
        try:
            provs = (cfg.get("models") or {}).get("providers", {})
            if any(
                isinstance(p, dict) and p.get("multimodal")
                for p in provs.values()
            ):
                vision_note = (
                    "\n- 你具备图片识别能力(已配置多模态模型)。用户粘贴/上传图片时"
                    "你会收到图片内容, 请直接识别并回答, 不要说'无法处理图片'。"
                )
        except Exception:  # noqa: BLE001
            pass
        runtime_guidelines = (
            f"\n\n[运行时约定]\n"
            f"- 工作区根目录: {ws_root or '(未配置)'}(所有会话共享)。"
            f"沙箱代码执行目录: {ws_root}/.sandbox/{{session_id}};"
            f"用户上传的文件统一存放: {ws_root}/uploads/。"
            f"当用户说\"文件在工作区/已解压/上传了\"时, 先检查上述两个目录, "
            f"找不到再请用户把文件放入 uploads/ 或 .sandbox/{{session_id}}, "
            f"不要猜测其他任意路径。\n"
            f"- 用户上传/粘贴了文件(图片/文档/压缩包)时, 必须读取其真实内容: "
            f"图片直接识别(你会收到图像), 文档/代码用 file_read 读取(大文件分块), "
            f"压缩包先解压再读; 禁止仅凭文件名/路径推测内容, 也禁止声称"
            f"\"只能看到路径\"/\"无法读取文件\"。\n"
            f"- 记忆与知识调用规则(2026-08-10 蒋先生确认):\n"
            f"  写入: 用户说\"记住/记录/保存\"某事 → 用 memory_save 写入原生"
            f"记忆(PG, scope 按当前场景, 全局偏好用 global), 用户明确要求时"
            f"同步写入记忆宫殿; 用户说\"整理/归档/建立知识\" → 以记忆宫殿为主。\n"
            f"  调取: ① 回忆习惯/偏好/概况(如\"我说过/我要求过/我习惯\") → "
            f"原生记忆(自动注入 + memory_search); ② 精确历史分析/报告/复盘"
            f"(含具体数字/日期/事件, 如投资复盘、持仓回顾) → 必须先主动检索"
            f"记忆宫殿(mempalace), 写报告/复盘前必查, 防止遗漏关键历史; "
            f"③ 领域知识/规则/框架(如\"什么是/规则/政策/理论\") → "
            f"search_knowledge 场景知识库。\n"
            f"  降级: memory_search 无结果时, 自动升级检索记忆宫殿; "
            f"分析/报告类任务优先记忆宫殿(宁可多调, 不可漏关键历史)。\n"
            f"- 需要用户决策(方案选择/确认/提问)时, 必须把选项与问题完整写入"
            f"你的可见回复, 禁止只写在推理过程(reasoning)中; "
            f"明确等待用户回答后再继续。{vision_note}"
        )
        base_prompt = f"{base_prompt}{runtime_guidelines}"
    except Exception:  # noqa: BLE001
        _logger.warning("runtime guidelines injection failed", exc_info=True)
    try:
        servers = cfg.get("tools", {}).get("mcp", {}).get("servers", [])
        guide = build_tools_guide(_get_mcp_manager(), servers)
        if guide:
            return f"{base_prompt}\n\n{guide}"
    except Exception:  # noqa: BLE001
        _logger.exception("mcp tools guide build failed")
    return base_prompt


@app.get("/")
async def root() -> dict[str, str]:
    """根路由(健康检查占位)。"""
    return {"status": "sidecar_running"}


@app.get("/health")
async def health() -> dict[str, str]:
    """健康检查端点(蓝图 §9.4 M0 Done Criteria 1)。"""
    return {"status": "ok"}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """WS 数据面端点(蓝图 §2.3)。

    支持消息类型:
    - ping: 心跳,返回 pong
    - replay: ws_offset 补发(蓝图 §2.3 line 449),从 react_events 查 turn > last_turn 的事件
    """
    await ws.accept()
    session_id = None
    # 0.5.0 P1: 采集器 ws_conns 计数(WS 连接数)
    collector = getattr(app.state, "metrics_collector", None)
    if collector is not None:
        collector.runtime_stats["ws_conns"] = (
            collector.runtime_stats.get("ws_conns", 0) + 1
        )
    try:
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type")
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
            elif msg_type == "replay":
                # ws_offset 补发(蓝图 §2.3 line 449)
                try:
                    session_id = int(msg["session_id"])
                    last_turn = int(msg.get("last_turn", 0))
                    full = bool(msg.get("full", False))
                except (KeyError, ValueError, TypeError):
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid replay: session_id and last_turn must be int",
                    })
                    continue
                if session_id <= 0 or last_turn < 0:
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid replay: session_id>0 and last_turn>=0 required",
                    })
                    continue
                try:
                    conn = await db.connect()
                    try:
                        messages = await ws_offset.build_replay_messages(
                            conn, session_id=session_id, last_turn=last_turn,
                            full=full,
                        )
                    finally:
                        await conn.close()
                    for m in messages:
                        await ws.send_json(m)
                except Exception:
                    # DB 异常不冒泡到 WS 循环外,发 error 后继续处理下一条消息
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "replay_failed",
                    })
            elif msg_type == "ack":
                # AC-6: 客户端 ACK,回写 config_runtime ws_offset:{session_id}=turn
                try:
                    session_id = int(msg["session_id"])
                    turn = int(msg["turn"])
                except (KeyError, ValueError, TypeError):
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid ack: session_id and turn must be int",
                    })
                    continue
                if session_id <= 0 or turn < 0:
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid ack: session_id>0 and turn>=0 required",
                    })
                    continue
                try:
                    conn = await db.connect()
                    try:
                        await ws_offset.handle_ack(
                            conn, session_id=session_id, turn=turn,
                        )
                    finally:
                        await conn.close()
                    await ws.send_json({
                        "type": "ack_confirm",
                        "session_id": session_id,
                        "turn": turn,
                    })
                except Exception:
                    # DB 异常不冒泡,发 error 后继续处理下一条消息
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "ack_failed",
                    })
            elif msg_type == "user_message":
                # AC-1: 用户消息触发 ReAct 循环(蓝图 §2.4/§2.6)
                try:
                    session_id = int(msg["session_id"])
                    content = str(msg["content"])
                except (KeyError, ValueError, TypeError):
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid user_message: session_id (int) and content required",
                    })
                    continue
                if session_id <= 0:
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid user_message: session_id>0 required",
                    })
                    continue
                # V2 P1(B2 修复): create_task 异步执行 —— run_turn 阻塞期间
                # 主循环仍可接收 tool_confirmation 等消息(权限确认链路前置条件)
                # V1.3-7.2: 透传可选 auto_execute/max_rounds(会话级覆盖)
                _auto = msg.get("auto_execute")
                _rounds = msg.get("max_rounds")
                _spawn_user_message_task(
                    ws,
                    session_id,
                    content,
                    bool(_auto) if _auto is not None else None,
                    int(_rounds) if _rounds is not None else None,
                )
            elif msg_type == "regenerate":
                # V1.1-3.3 消息重生成: 按 turn 重放该轮 user 消息
                # (前端事件按 turn 组织, 无 msg_id; 重放产生新 turn)
                try:
                    session_id = int(msg["session_id"])
                    turn = int(msg["turn"])
                except (KeyError, ValueError, TypeError):
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid regenerate: session_id (int) and turn (int) required",
                    })
                    continue
                try:
                    conn = await db.connect()
                    try:
                        content = await conn.fetchval(
                            """
                            SELECT content FROM messages
                            WHERE session_id = $1 AND turn = $2
                              AND role = 'user' AND compressed = FALSE
                            ORDER BY id ASC LIMIT 1
                            """,
                            session_id,
                            turn,
                        )
                    finally:
                        await conn.close()
                except Exception:
                    content = None
                if not content or not str(content).strip():
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "regenerate_failed: 未找到该轮可重放的 user 消息(可能已被删除/压缩)",
                    })
                    continue
                _spawn_user_message_task(ws, session_id, str(content))
            elif msg_type == "cancel":
                # 打断/停止: 取消当前会话所有运行中的 turn(生成中用户点"停止")
                try:
                    session_id = int(msg["session_id"])
                except (KeyError, ValueError, TypeError):
                    continue
                tasks = _session_tasks.get(session_id) or set()
                cancelled_any = False
                for task in list(tasks):
                    if not task.done():
                        task.cancel()
                        cancelled_any = True
                if cancelled_any:
                    await ws.send_json({
                        "type": "turn_cancelled",
                        "session_id": session_id,
                        "message": "已停止生成",
                    })
            elif msg_type == "resume":
                # V1.5 项-5/项-4: resume 双语义, 按会话状态区分 ——
                # 1) 会话运行中 paused=True → 流程级"继续"(解除挂起)
                # 2) 会话 interrupted → 断点恢复(从最新 checkpoint 原地续跑)
                try:
                    session_id = int(msg["session_id"])
                except (KeyError, ValueError, TypeError):
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid resume: session_id (int) required",
                    })
                    continue
                if session_id <= 0:
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid resume: session_id>0 required",
                    })
                    continue
                ctrl = _pause_controls.get(session_id)
                if ctrl is not None and ctrl.is_paused():
                    # 流程级继续: 解除挂起, ReactLoop 挂起点 wait() 返回
                    ctrl.resume()
                    try:
                        conn2 = await db.connect()
                        try:
                            await conn2.execute(
                                "UPDATE sessions SET paused=FALSE, "
                                "updated_at=now() WHERE id=$1",
                                session_id,
                            )
                        finally:
                            await conn2.close()
                    except Exception:
                        pass  # DB 更新失败不阻塞继续
                    await ws.send_json({
                        "type": "turn_resumed",
                        "session_id": session_id,
                        "message": "已继续生成",
                    })
                    continue
                _spawn_user_message_task(ws, session_id, "", resume=True)
            elif msg_type == "pause":
                # V1.5 项-5: 流程级暂停(生成中挂起, 区别于 cancel 终止)。
                # 生效时机: 当前迭代完成后、下一次迭代开始前(ReactLoop
                # 迭代开始检查 is_paused)。
                try:
                    session_id = int(msg["session_id"])
                except (KeyError, ValueError, TypeError):
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid pause: session_id (int) required",
                    })
                    continue
                if session_id <= 0:
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid pause: session_id>0 required",
                    })
                    continue
                ctrl = _get_pause_controller(session_id)
                ctrl.pause()
                try:
                    conn2 = await db.connect()
                    try:
                        await conn2.execute(
                            "UPDATE sessions SET paused=TRUE, "
                            "updated_at=now() WHERE id=$1",
                            session_id,
                        )
                    finally:
                        await conn2.close()
                except Exception:
                    pass
                await ws.send_json({
                    "type": "turn_paused",
                    "session_id": session_id,
                    "message": "已暂停(当前迭代完成后生效), 点击继续恢复",
                })
            elif msg_type == "tool_confirmation":
                # V2 P1: 权限确认响应(蓝图 §5.12, 用户点击同意/拒绝)
                try:
                    session_id = int(msg["session_id"])
                    confirmation_id = str(msg["confirmation_id"])
                    approved = bool(msg.get("approved", False))
                except (KeyError, ValueError, TypeError):
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid tool_confirmation: session_id/confirmation_id required",
                    })
                    continue
                pm = _permission_managers.get(session_id)
                if pm is None:
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "no pending confirmation for session",
                    })
                    continue
                if not pm.resolve(confirmation_id, approved):
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "unknown confirmation_id",
                    })
            elif msg_type == "approval_defer":
                # 阶段三批次4(B-14): 用户"稍后决定"挂起确认(60s 超时后
                # 继续等待 defer_timeout, 期间仍可 resolve)
                try:
                    session_id = int(msg["session_id"])
                    confirmation_id = str(msg["confirmation_id"])
                except (KeyError, ValueError, TypeError):
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "invalid approval_defer: session_id/confirmation_id required",
                    })
                    continue
                pm = _permission_managers.get(session_id)
                if pm is None or not pm.defer(confirmation_id):
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "unknown confirmation_id for defer",
                    })
                    continue
                await ws.send_json({
                    "type": "approval_deferred",
                    "session_id": session_id,
                    "confirmation_id": confirmation_id,
                    "message": "已挂起, 可稍后决定(期间仍可同意/拒绝)",
                })
    except WebSocketDisconnect:
        # 0.5.0 P1: 释放 ws_conns 计数
        if collector is not None:
            collector.runtime_stats["ws_conns"] = max(
                0, collector.runtime_stats.get("ws_conns", 1) - 1
            )
        if session_id is not None:
            try:
                conn = await db.connect()
                try:
                    await CheckpointManager.mark_session_interrupted(
                        conn, session_id
                    )
                finally:
                    await conn.close()
            except Exception:
                _logger.exception(
                    "Failed to mark session interrupted on disconnect"
                )


def _spawn_user_message_task(
    ws: WebSocket,
    session_id: int,
    content: str,
    auto_execute: bool | None = None,
    max_rounds: int | None = None,
    resume: bool = False,
) -> asyncio.Task:
    """V1.1-3.3: 用户消息/重生成共用的任务注册(create_task + task 集合管理)。

    run_turn 阻塞期间 WS 主循环仍可接收 tool_confirmation 等消息(V2 P1 B2 修复);
    同会话并发任务并存, 各自可被 cancel 命中; done 后从集合移除(T-3)。
    V1.3-7.2: 透传 auto_execute/max_rounds(会话级自动连续执行覆盖)。
    V1.5 项-4: resume=True 时以断点恢复模式执行(查 checkpoint → 回滚残留 →
    原地续跑中断轮)。
    """
    task = asyncio.create_task(
        _handle_user_message(
            ws, session_id, content, auto_execute, max_rounds, resume
        )
    )
    _session_tasks.setdefault(session_id, set()).add(task)

    def _on_task_done(t: asyncio.Task, sid: int = session_id) -> None:
        tasks = _session_tasks.get(sid)
        if tasks is not None:
            tasks.discard(t)
            if not tasks:
                _session_tasks.pop(sid, None)

    task.add_done_callback(_on_task_done)
    return task


async def _handle_user_message(
    ws: WebSocket,
    session_id: int,
    content: str,
    auto_execute: bool | None = None,
    max_rounds: int | None = None,
    resume: bool = False,
) -> None:
    """AC-1: 用户消息触发 ReAct 循环(蓝图 §2.4/§2.6)。

    V2 P1(B2 修复): 由 ws_endpoint create_task 异步调用 —— run_turn 期间
    WS 主循环可继续接收 tool_confirmation 等消息。

    V1.5 项-4: resume=True 时执行断点恢复 —— 读最新 checkpoint → 清理中断
    轮残留(assistant/tool 消息 + react_events) → 会话置 active → 构造
    ReactLoop(resume_from_turn=checkpoint.turn+1) → run_turn(resume=True)
    原地续跑该轮。中断轮 user 消息保留(续跑沿用同一 turn 号)。

    - per-session 运行锁: 同会话并发 user_message 串行(防 turn 冲突)
    - set_sandbox_config 注入(B1 修复): code_execution 工具依赖模块级全局,
      此前从未在生产路径调用 → 现网必报 "Sandbox not configured"
    - permission_manager 传入 ReactLoop: 权限确认运行时链路(蓝图 §5.12)
    - V1.3-7.2: auto_execute/max_rounds 自动连续执行(WS 显式传参覆盖会话配置)
    """
    lock = _session_locks.setdefault(session_id, asyncio.Lock())
    conn = None
    async with lock:
        try:
            cfg = await _load_cfg_with_runtime()
            # B1 修复: 注入沙箱配置(code_execution handler 依赖模块级全局)
            from private_agent.tools.builtins.code_execution import set_sandbox_config

            set_sandbox_config(cfg.get("sandbox"))
            # 阶段二批次 2: 注入安全配置(http_request SSRF 校验依赖模块级全局)
            from private_agent.tools.builtins.http_request import set_security_config

            set_security_config(cfg)
            conn = await db.connect()
            # 会话懒创建(蓝图 §2.10):WS 收到首条 user_message 时,
            # sessions 无该行则插入,保证 ensure_initial 外键不失败
            # 懒创建: title 留空(NULL), list_sessions 用首条用户消息兜底生成可读标题
            exists = await conn.fetchval(
                "SELECT 1 FROM sessions WHERE id=$1", session_id
            )
            if exists is None:
                await conn.execute(
                    "INSERT INTO sessions (id) VALUES ($1)",
                    session_id,
                )
            # V1.5 项-4: 断点恢复前置处理(必须在 reload_from_db 之前清理
            # 中断轮残留, 否则内存上下文会加载半轮消息)
            # 流程: 读最新 checkpoint → 回滚中断轮(清 assistant/tool 残留 +
            # react_events 半轮事件, 保留 user 消息) → 会话置 active →
            # ReactLoop 以 resume_from_turn=checkpoint.turn+1 原地续跑。
            resume_from_turn = None
            if resume:
                ckpt = await CheckpointManager.load_latest_checkpoint(
                    conn, session_id
                )
                if ckpt is None:
                    await ws.send_json({
                        "type": "error",
                        "session_id": session_id,
                        "message": "resume_failed: 该会话无 checkpoint, 无法断点恢复",
                    })
                    return
                ckpt_turn = int(ckpt["turn"])
                resume_from_turn = ckpt_turn + 1
                # 1) 回滚中断轮残留消息(保留 user, 清 assistant/tool ——
                #    工具执行中断可能留下无 assistant 配对的 tool 消息,
                #    不清理会导致下次模型调用 400 pairing 错误)
                await conn.execute(
                    """DELETE FROM messages
                       WHERE session_id = $1 AND turn > $2
                         AND role IN ('assistant', 'tool')""",
                    session_id, ckpt_turn,
                )
                # 2) 回滚中断轮 react_events(前端 replay 不拉到半轮事件)
                await conn.execute(
                    "DELETE FROM react_events WHERE session_id = $1 AND turn > $2",
                    session_id, ckpt_turn,
                )
                # 3) 中断轮 user 消息缺失(任务创建后立即被取消)时补恢复提示
                has_user = await conn.fetchval(
                    """SELECT 1 FROM messages
                       WHERE session_id=$1 AND turn=$2 AND role='user'
                       LIMIT 1""",
                    session_id, resume_from_turn,
                )
                if not has_user:
                    await conn.execute(
                        """INSERT INTO messages (session_id, turn, role, content)
                           VALUES ($1, $2, 'user', $3)""",
                        session_id, resume_from_turn,
                        "[resume] 请继续完成之前中断的任务(若已完成请简要说明)。",
                    )
                # 4) 会话状态回 active(解除 interrupted 标记)
                await conn.execute(
                    "UPDATE sessions SET status='active', paused=FALSE, "
                    "updated_at=now() WHERE id=$1",
                    session_id,
                )
                _logger.info(
                    "resume session=%s from checkpoint turn=%s (resume turn=%s)",
                    session_id, ckpt_turn, resume_from_turn,
                )
            # frozen_tools: 内置白名单(与 activate 同源, hash 锁定)
            # tools: 全量(内置 + MCP, 供 ReactLoop 调用)
            frozen_tools = await _get_frozen_tools(cfg, session_id, conn)
            tools = frozen_tools + await _get_mcp_manager().get_tools(cfg)
            # 0.5.0 P3: monitor 会话(主智能体) → 追加监控工具白名单
            # (system_metrics_query/system_status/optim_plan/apply_optim)。
            # 仅 monitor 会话装配, 场景会话(子瞻/白圭/清和)不暴露系统级工具。
            session_kind = await conn.fetchval(
                "SELECT kind FROM sessions WHERE id = $1", session_id
            )
            if session_kind == "monitor":
                from private_agent.tools.builtins import register_monitor_tools
                from private_agent.tools.registry import ToolRegistry

                _mreg = ToolRegistry()
                register_monitor_tools(_mreg)
                tools = tools + _mreg.list_tools()
            # V1.5 项-1(ADR-012 §3.5): 附加 delegate_subtask 工具。
            # - 闭包注入当轮上下文(conn/cfg/session_id/event_sink/tools),
            #   多会话并发无模块级全局串扰;
            # - 不进 frozen_tools → 不参与 frozen hash(工具演进不触发重建);
            # - 嵌套深度: 传入闭包的 tools=tools 在求值时仍是"附加前"的旧
            #   列表(不含 delegate) → 子代理继承的工具列表天然不含本工具,
            #   嵌套深度恒 1(< max_nesting_depth=2)。
            from private_agent.tools.builtins.delegate_subtask import (
                build_delegate_subtask_tool,
            )

            tools = [
                *tools,
                build_delegate_subtask_tool(
                    conn=conn,
                    cfg=cfg,
                    session_id=session_id,
                    event_sink=lambda ev: ws.send_json(ev),
                    tools=tools,
                    system_prompt_factory=(
                        lambda c, sid: _get_system_prompt(cfg, sid, c)
                    ),
                    adapter_factory=(
                        lambda m: _build_session_adapter(cfg, m)
                    ),
                    compress_adapter=_build_compress_adapter(cfg),
                ),
            ]
            # V1.1-3.5: 会话级记忆开关(关闭 → 不注入/不提取记忆, 传 None)
            memory_enabled = await conn.fetchval(
                "SELECT memory_enabled FROM sessions WHERE id = $1", session_id
            )
            # 0.5.0 M1: 会话场景(locked_skill_name → 记忆 scope)
            session_scene = await conn.fetchval(
                "SELECT locked_skill_name FROM sessions WHERE id = $1",
                session_id,
            )
            memory_mgr = None
            if memory_enabled is not False:
                # 构造 MemoryManager(蓝图 §4.2-§4.5)
                # V2 补齐(§4.4 [MVP]): 注入 react_events_insert, 使记忆提取/
                # 淘汰事件在生产路径真正入库(memory_extracted/memory_evicted)
                from private_agent.storage.react_events import insert_react_event

                memories_repo = MemoriesRepo(conn)
                memory_mgr = MemoryManager(
                    memories_repo=memories_repo,
                    compress_adapter=_build_compress_adapter(cfg),
                    react_events_insert=insert_react_event,
                    extract_interval_turns=cfg.get("memory", {}).get(
                        "extract_interval_turns", 8
                    ),
                    inject_limit=cfg.get("memory", {}).get("inject_limit", 10),
                    inject_global_n=cfg.get("memory", {}).get(
                        "inject_ratio", {}
                    ).get("global", 2),
                    eviction_max_active=cfg.get("memory", {}).get(
                        "eviction", {}
                    ).get("max_active_count", 200),
                    eviction_min_importance=cfg.get("memory", {}).get(
                        "eviction", {}
                    ).get("min_importance_threshold", 0.3),
                    eviction_expire_days=cfg.get("memory", {}).get(
                        "eviction", {}
                    ).get("expire_days", 30),
                    archive_before_evict=bool(
                        cfg.get("memory", {}).get("archive_before_evict", True)
                    ),
                )
            # 0.5.0 M2: 场景 KB 自动检索配置(从锁定 skill 的 knowledge_base 段读取)
            kb_auto_retrieve = False
            kb_scenario = None
            if session_scene:
                try:
                    from private_agent.skills.loader import SkillLoader

                    _sloader = SkillLoader.from_cfg(cfg)
                    _skill = await _sloader.load(session_scene, conn)
                    _kb = _skill.manifest.knowledge_base
                    if _kb.enabled and _kb.auto_retrieve:
                        kb_auto_retrieve = True
                        kb_scenario = _kb.scenario or session_scene
                except Exception:  # noqa: BLE001 - KB 配置读取失败不影响会话
                    pass
            # Phase 1-2(2026-08-11): 经验进化闭环 —— 经验注入 Stable Zone
            evolution_repo = EvolutionRepo(conn)
            cm = ContextManager(
                session_id=session_id,
                system_prompt=await _get_system_prompt(
                    cfg, session_id, conn
                ),
                tools=frozen_tools,
                memory_manager=memory_mgr,
                cfg=cfg,  # 方向三: KB/记忆注入精简配置
                # 0.5.0 M1: 会话场景(记忆注入按场景过滤/配额)
                scene=session_scene,
                # 0.5.0 M2: 场景 KB 自动检索(会话启动注入该场景知识库片段)
                kb_auto_retrieve=kb_auto_retrieve,
                kb_scenario=kb_scenario,
                # Phase 1-2: 经验注入(ensure_initial → _inject_lessons)
                evolution_repo=evolution_repo,
            )
            try:
                await cm.ensure_initial(conn)
            except FrozenHashMismatchError:
                # 工具/提示词演进导致 frozen_hash 变化(旧会话续聊):
                # 自动用当前 system_prompt + tools 重建 frozen zone 并
                # 更新 sessions.frozen_hash, 无需人工干预
                _logger.warning(
                    "frozen_hash mismatch → rebuild frozen zone "
                    "(工具/提示词演进, session=%s)", session_id,
                )
                await cm.replace_frozen_zone(
                    conn,
                    system_prompt=cm._system_prompt,
                    tools=cm._tools,
                )
            # 续聊: 完整重放历史消息到内存(ensure_initial 只加载
            # Frozen Zone, active 历史对话需 reload_from_db 重建,
            # 否则模型看不到历史上下文)
            await cm.reload_from_db(conn)
            # 会话级模型选择: auto(fallback 链) / 具体 provider(手动锁定)
            model_id = await conn.fetchval(
                "SELECT model_id FROM sessions WHERE id = $1", session_id
            )
            adapter, vision_adapter = _build_contextual_adapter(cfg, model_id)
            # per-provider 对话参数上限: 取 session.model_id 或 fallback 首选
            from private_agent.config.loader import resolve_provider_limits

            chain = cfg.get("models", {}).get("router", {}).get(
                "fallback_chain", []
            )
            provider_name = model_id or (chain[0] if chain else None)
            provider_limits = resolve_provider_limits(cfg, provider_name)
            # V1.1-3.6: skill model_params.max_tokens 覆盖(智能体级参数,
            # 参数跟随模型: 仅注入 provider 已支持的 max_tokens 维度)
            try:
                locked_skill = await conn.fetchval(
                    "SELECT locked_skill_name FROM sessions WHERE id = $1", session_id
                )
                if locked_skill:
                    from private_agent.skills.loader import SkillLoader

                    skill_loader = SkillLoader.from_cfg(cfg)
                    skill = await skill_loader.load(locked_skill, conn)
                    mp_max = (skill.manifest.model_params or {}).get("max_tokens")
                    if isinstance(mp_max, (int, float)) and int(mp_max) > 0:
                        provider_limits = {
                            **provider_limits,
                            "max_output_tokens": int(mp_max),
                        }
            except Exception:  # noqa: BLE001
                pass  # skill 参数注入失败不影响主流程
            # 会话工作区(画地为牢): 用户选定目录 > 默认 workspace_root
            session_workspace = await conn.fetchval(
                "SELECT workspace FROM sessions WHERE id = $1", session_id
            )
            if session_workspace:
                cfg = {**cfg, "system": {**cfg.get("system", {}),
                                          "workspace_root": session_workspace}}
            # Phase 1/3(2026-08-11): 反思引擎 + 失败案例采集器装配
            # 反思引擎: config evolution.reflection.enabled 门控(默认开)
            # 失败采集器: ReviewQueueRepo(JSON 文件) + OnlineFailureCollector
            reflection_engine = None
            if cfg.get("evolution", {}).get("reflection", {}).get("enabled", True):
                reflection_engine = ReflectionEngine(adapter)
            _queue_file = os.path.join(
                cfg.get("system", {}).get("workspace_root", "."),
                ".eval_review_queue.json",
            )
            failure_collector = OnlineFailureCollector(
                ReviewQueueRepo(queue_file=_queue_file)
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                vision_adapter=vision_adapter,
                tools=tools,
                conn=conn,
                cfg=cfg,
                provider_limits=provider_limits,
                # 实时推送: 事件边产生边发给 WS(流式逐块, 而非结束后批量)
                event_sink=lambda ev: ws.send_json(ev),
                # V2 P1: 权限确认管理器(仅 elevated 工具生效, 如 code_execution)
                permission_manager=_get_permission_manager(session_id),
                # V2 上下文工程: 压缩适配器(按 compress_model 构建,
                # 未配置时 None → 压缩降级为纯滑动窗口)
                compress_adapter=_build_compress_adapter(cfg),
                # 阶段三批次2(B-1): Hooks 调度器(config hooks 为空 → None 零回归)
                hook_runner=_build_hook_runner(cfg),
                # V1.5 项-4: 断点恢复起始轮(checkpoint.turn+1, resume 模式用)
                resume_from_turn=resume_from_turn,
                # V1.5 项-5: 流程级暂停控制器(迭代开始检查, 挂起等待)
                pause_controller=_get_pause_controller(session_id),
                # Phase 1/3: 反思 + 经验进化 + 失败采集(None 时零回归)
                reflection_engine=reflection_engine,
                evolution_repo=evolution_repo,
                failure_collector=failure_collector,
            )
            # 阶段三批次1(T1.2/T3.1): 同步会话级权限模式 + Skill 权限规则
            await _sync_permission_manager(
                _get_permission_manager(session_id), conn, session_id, cfg
            )
            # V1.3-7.2 工作流自动化: 自动连续执行。
            # 优先级: WS user_message 显式传参 > 会话级配置(auto_execute/max_rounds)。
            # 每轮独立 run_turn + turn_end(前端按 turn 分组), 后续轮用
            # "[auto-execute]" 前缀输入提示模型继续完成剩余任务。
            rounds = 0
            run_content = content
            if auto_execute is None:
                auto_execute = await conn.fetchval(
                    "SELECT auto_execute FROM sessions WHERE id = $1",
                    session_id,
                )
            if max_rounds is None:
                max_rounds = await conn.fetchval(
                    "SELECT max_rounds FROM sessions WHERE id = $1",
                    session_id,
                )
            max_rounds = int(max_rounds or 0)
            while True:
                rounds += 1
                if resume:
                    # V1.5 项-4: 断点恢复模式 —— 不追加 user 消息,
                    # 从 resume_from_turn 原地续跑中断轮; 仅跑一轮
                    await loop.run_turn(resume=True)
                else:
                    await loop.run_turn(run_content)
                # 事件已通过 event_sink 实时推送, 无需再排空 event_queue
                await ws.send_json({
                    "type": "turn_end",
                    "session_id": session_id,
                    "turn": loop._turn,
                })
                # 每轮结束后触发记忆提取(蓝图 §4.2; V1.1-3.5: 记忆关闭时跳过)
                if memory_mgr is not None:
                    await memory_mgr.maybe_extract(
                        session_id=session_id, current_turn=loop._turn,
                        scope=session_scene,
                    )
                # 断点恢复只续跑中断轮, 不触发 auto_execute 多轮
                if resume:
                    break
                # 自动执行: 已到上限或未开启则停止
                if not auto_execute or rounds >= max_rounds:
                    break
                run_content = (
                    "[auto-execute] 前一轮已完成, 请继续完成该任务"
                    "的剩余部分(若已全部完成, 简要说明即可)。"
                )
        except asyncio.CancelledError:
            # 打断/停止: 用户点"停止" → cancel → 标记会话中断, 不报错
            try:
                if conn is not None:
                    await CheckpointManager.mark_session_interrupted(
                        conn, session_id
                    )
            except Exception:  # noqa: BLE001
                pass
            try:
                await ws.send_json({
                    "type": "turn_cancelled",
                    "session_id": session_id,
                    "message": "已停止生成",
                })
            except Exception:  # noqa: BLE001
                pass
            raise  # 保持 CancelledError 语义(task 被正确取消)
        except Exception:
            _logger.exception("user_message handling failed")
            try:
                if conn is not None:
                    await CheckpointManager.mark_session_interrupted(
                        conn, session_id
                    )
            except Exception:
                pass
            try:
                await ws.send_json({
                    "type": "error",
                    "session_id": session_id,
                    "message": "user_message_failed",
                })
            except Exception:
                pass
        finally:
            if conn is not None:
                await conn.close()


def _migrate_user_data_from_workspace() -> None:
    """2026-08-08: 打包版用户数据重定向(userData)后的首次启动迁移。

    历史打包版 WORKSPACE=安装目录 resources/backend, 技能/壁纸(outputs)/
    上传(uploads) 落在安装目录, 更新覆盖会丢。用户数据根改为 PA_USER_DATA
    (%APPDATA%/Private Agent)后, 首次启动把旧位置已有目录复制到 userData;
    dev 模式或同目录时跳过(零回归)。
    """
    import shutil as _shutil
    from pathlib import Path as _P

    ud = os.environ.get("PA_USER_DATA", "").strip()
    ws = os.environ.get("WORKSPACE", "").strip()
    if not ud or not ws or os.path.normpath(ud) == os.path.normpath(ws):
        return
    for sub in ("outputs", "skills", "uploads"):
        src, dst = _P(ws) / sub, _P(ud) / sub
        if src.is_dir() and not dst.exists():
            try:
                _shutil.copytree(src, dst)
                _logger.info("user data migrated: %s -> %s", src, dst)
            except Exception as e:  # noqa: BLE001
                _logger.warning("user data migrate failed for %s: %s", sub, e)


@app.on_event("startup")
async def _on_startup() -> None:
    """启动钩子(蓝图 §2.10 + §9.4 AC-5 + §9.13)。

    - db.create_pool 创建连接池(失败时 log warning,不阻止启动)
    - 注册 APScheduler TTL 清理任务(cron `0 3 * * *`)
    - scheduler.start()
    - B1 P1-2: 读 cfg.observability.logging.file_path 配置 FileHandler
    """
    global _scheduler, _logger
    cfg = loader.load_config()
    # B1 P1-2: 配置 file_path(展开环境变量)
    _configure_logger(cfg)
    # 2026-08-08: 打包版用户数据重定向后, 首次启动把旧位置(安装目录)用户数据
    # 迁移到 userData, 避免升级后技能/壁纸丢失(dev/同目录自动跳过)
    _migrate_user_data_from_workspace()
    try:
        db._pool = await db.create_pool(cfg)
    except Exception as e:
        _logger.warning(f"DB pool creation failed at startup: {e}")
    # 启动自动迁移(migrate_all 幂等:全新库建表,已有库跑增量补丁)
    if db._pool is not None:
        from private_agent.storage import migrations

        try:
            async with db._pool.acquire() as conn:
                await migrations.migrate_all(conn)
            _logger.info("DB schema migrated (idempotent)")
            # 0.5.1: KB embedding 启动自检(防混存脏库/模型切换未重灌)。
            # 默认 KB 模块 fail-fast(应用其余功能正常, KB 端点/检索返回明确错误);
            # PA_KB_STRICT=1 时阻断进程启动。
            try:
                from private_agent.knowledge.factory import (
                    KBEmbeddingInconsistencyError,
                    set_kb_failfast,
                    verify_embedding_consistency,
                )

                async with db._pool.acquire() as conn:
                    await verify_embedding_consistency(conn, cfg)
                _logger.info("KB embedding consistency verified")
            except KBEmbeddingInconsistencyError as e:
                set_kb_failfast(str(e))
                if os.environ.get("PA_KB_STRICT") == "1":
                    _logger.error("PA_KB_STRICT=1, aborting startup: %s", e)
                    raise
            except Exception:
                _logger.exception("KB embedding consistency check failed")
            # 从 config_runtime 恢复 AES 加密的 API key → 环境变量(设置页录入后重启仍生效)
            await _restore_keys_from_runtime()
            # 2026-08-06: 启动即确保 AES 主密钥持久化到用户配置
            # (%APPDATA%/Private Agent/backend.env, 打包版与 dev 统一)——
            # 不依赖任何用户操作; 升级/重装(Electron userData 不随安装覆盖)
            # 后密钥不漂移 → provider API key 始终可解密(不再"每版重配")。
            try:
                from private_agent.api import admin as _admin

                _admin._ensure_master_key()
            except Exception:  # noqa: BLE001
                _logger.warning("master key ensure failed at startup")
            # V1.5 项-1(ADR-012 §3.3e): 进程重启后清理 running 且心跳过期的
            # 僵尸子代理(统一置 failed(heartbeat_timeout_after_restart), 幂等)
            try:
                from private_agent.core.subagent import cleanup_zombies_on_startup

                async with db._pool.acquire() as conn:
                    n = await cleanup_zombies_on_startup(conn, cfg)
                if n:
                    _logger.warning(
                        "startup: cleaned %d zombie subagent(s) "
                        "(heartbeat_timeout_after_restart)", n,
                    )
            except Exception:
                _logger.exception("subagent zombie cleanup failed at startup")
        except Exception as e:
            _logger.warning(f"DB schema migration failed at startup: {e}")
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from private_agent.storage.ttl_cleanup import schedule_ttl_cleanup
    _scheduler = AsyncIOScheduler()
    schedule_ttl_cleanup(_scheduler, cfg)
    # 0.5.0 P1: 主智能体监控 —— 周期指标采集(60s 默认, config 可覆盖)
    try:
        from private_agent.core.metrics_collector import MetricsCollector

        interval = float(
            (cfg.get("system") or {}).get("metrics", {}).get(
                "interval_sec", 60.0
            )
        )
        collector = MetricsCollector(db=db, interval_sec=interval)
        app.state.metrics_collector = collector

        async def _collect_metrics_job() -> None:
            if db._pool is None:
                return
            try:
                async with db._pool.acquire() as conn:
                    await collector.collect_once(conn)
            except Exception:  # noqa: BLE001 - 采集失败不阻塞主流程
                _logger.warning("metrics collect job failed", exc_info=True)

        # 启动后 15s 首采(等 DB 池就绪), 之后按间隔
        _scheduler.add_job(
            _collect_metrics_job, "interval",
            seconds=interval,
            next_run_time=datetime.now() + timedelta(seconds=15),
            id="system_metrics_collector",
            max_instances=1,
            coalesce=True,
        )
    except Exception:  # noqa: BLE001
        _logger.warning("metrics collector init failed", exc_info=True)
    _scheduler.start()


async def _load_cfg_with_runtime() -> dict:
    """加载 config.yaml 并合并 config_runtime 运行时覆盖(蓝图 §2.12, runtime > yaml)。

    设置页对 provider/MCP 的修改写入 config_runtime 后, 后续对话即用合并后的配置。
    """
    conn = await db.connect()
    try:
        return await loader.load_config_with_overrides(conn)
    finally:
        await conn.close()


async def _restore_keys_from_runtime() -> None:
    """从 config_runtime 读取 models.providers.*.api_key_encrypted, 解密后设置环境变量。

    设置页录入的 API key 重启后依赖此恢复(否则 env 丢失 → 模型 401)。
    """
    import os

    from private_agent.config import secrets

    master_hex = os.environ.get("PA_MASTER_KEY", "")
    if not master_hex:
        return  # 无 master key 则跳过(从未在设置页录入过 key)
    try:
        master = bytes.fromhex(master_hex)
    except ValueError:
        return
    async with db._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value FROM config_runtime WHERE key LIKE 'models.providers.%.api_key_encrypted'"
        )
    for row in rows:
        key_path = row["key"]  # models.providers.deepseek.api_key_encrypted
        # 0.5.1 修复(2026-08-10 蒋先生反馈"kimi key 重打包后丢失"): 原实现
        # parts[2] 只取第 3 段 —— 含点的 provider 名(kimi-k2.6)被截成
        # kimi-k2 → 环境变量名错位 → key 读不到。改为取"providers." 后
        # 最后一个点分割(与 loader._get_runtime_overrides 语义一致)。
        prefix = "models.providers."
        if not key_path.startswith(prefix):
            continue
        rest = key_path[len(prefix):]
        prov_name, field = rest.rsplit(".", 1)
        if field != "api_key_encrypted":
            continue
        try:
            # asyncpg JSONB 返回 JSON 字符串, 需先解析为 dict(decrypt 期望 dict)
            import json as _json

            encrypted = (
                _json.loads(row["value"])
                if isinstance(row["value"], str)
                else row["value"]
            )
            plain = secrets.decrypt_api_key(encrypted, master)
        except Exception:  # noqa: BLE001
            continue
        os.environ[f"PA_{prov_name.upper()}_API_KEY"] = plain


def _configure_logger(cfg: dict) -> None:
    """B1 P1-2: 从 cfg 读 file_path 并配置 logger(FileHandler 失败时降级仅 stdout)。"""
    global _logger
    obs_cfg = cfg.get("observability", {}).get("logging", {})
    file_path = obs_cfg.get("file_path")
    level = obs_cfg.get("level", "INFO").upper()
    level_int = getattr(logging, level, logging.INFO)
    expanded_path = os.path.expandvars(file_path) if file_path else None
    try:
        _logger = setup_logger(
            "private_agent.main", level=level_int, file_path=expanded_path
        )
    except (OSError, PermissionError) as e:
        # FileHandler 创建失败(路径不可写/权限不足)时降级仅 StreamHandler
        _logger = setup_logger("private_agent.main", level=level_int)
        _logger.warning(f"FileHandler setup failed, fallback to stdout only: {e}")


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    """关闭钩子:停止 scheduler + 关闭 MCP 客户端 + 关闭连接池。"""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    # 断开 MCP 客户端(进程级单例, 装配用)
    try:
        await _mcp_manager.close_all()
    except Exception:  # noqa: BLE001
        pass
    await db.close_pool()


def run_sidecar() -> None:
    """启动 Sidecar:从 config.yaml 读取端口,uvicorn 监听(蓝图 §2.2 + §9.13)."""
    import uvicorn

    global _logger
    cfg = loader.load_config()
    # B1 P1-2: 配置 file_path(展开环境变量)
    _configure_logger(cfg)
    # 阶段二批次 1: 确保 admin token 可用(缺失时生成并持久化到 backend/.env)
    try:
        ensure_admin_token()
    except Exception:  # noqa: BLE001
        _logger.warning("admin token ensure failed (fallback to env)", exc_info=True)
    host = cfg["server"]["http"]["host"]
    http_port = cfg["server"]["http"]["port"]
    _logger.info(f"Sidecar started: host={host} http_port={http_port}")
    uvicorn.run(app, host=host, port=http_port)


if __name__ == "__main__":
    run_sidecar()