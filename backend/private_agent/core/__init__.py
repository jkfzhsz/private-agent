"""蓝图 §2.15 core 子包 - 编排核心公开 API。

M1 Phase 3 导出:
- ContextManager / Zone:三区上下文管理(蓝图 §3.1-3.3)
- ReactLoop / ReactLoopState:ReAct 状态机 + 流式事件产出(蓝图 §2.4/§2.6)
"""
from private_agent.core.context_manager import ContextManager, Zone
from private_agent.core.react_loop import ReactLoop, ReactLoopState

__all__ = ["ContextManager", "Zone", "ReactLoop", "ReactLoopState"]
