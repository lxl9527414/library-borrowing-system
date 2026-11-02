"""Agent 状态与运行期上下文。

- `AgentState`：会被 LangGraph 检查点持久化的对话状态（thread_id = sessionId）；
- `RunContext`：一次 HTTP 请求内的运行依赖（模型网关、工具执行器、事件出口等），
  不放进 state，避免把 token / 客户端对象写进检查点。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, Callable, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.config import get_stream_writer
from langgraph.graph.message import add_messages

from app.agent.events import AgentRunStats, EventEmitter

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，避免循环导入
    from app.llm.gateway import LLMGateway
    from app.tools.executor import ToolExecutor


class AgentState(TypedDict, total=False):
    """LangGraph 对话状态。"""

    messages: Annotated[list[BaseMessage], add_messages]
    # 已执行的 agent 循环步数（契约 §5：超过 AGENT_MAX_STEPS 强制结束）
    step: int
    # 写操作的用户决策：{"approved": bool, "confirmationId": str} | None
    approval: dict[str, Any] | None
    # 需要立即终止本次流的事件（如 UPSTREAM_UNAUTHENTICATED / AGENT_MAX_STEPS）
    terminal: dict[str, Any] | None
    # 模型连续调用不存在工具的计数（契约 §5：最多 3 轮自我纠正）
    tool_not_found_count: int


@dataclass
class RunContext:
    """单次请求的运行期依赖（不进检查点）。"""

    session_id: str
    trace_id: str
    emitter: EventEmitter
    gateway: "LLMGateway"
    executor: "ToolExecutor"
    system_prompt: str
    max_steps: int = 8
    # 送入模型的历史消息上限（超出时从最近的用户消息处截断）
    max_history_messages: int = 40
    # 暂停时由 API 层登记 confirmationId -> 待执行写操作
    on_confirm_required: Callable[[dict[str, Any]], None] | None = None
    started_at: float = field(default_factory=time.perf_counter)
    stats: AgentRunStats = field(default_factory=AgentRunStats)

    # ---- 便捷方法 ----
    def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        """节点内推送自定义事件：优先官方 stream_writer，保证与图执行顺序一致。

        writer 不可用时（例如在节点外调用）退回内存队列，避免丢事件。
        所有事件都经过这里，因此可观测性埋点也放在这里（业务/图代码零侵入）。
        """
        payload = data or {}
        try:
            writer = get_stream_writer()
        except RuntimeError:
            self.emitter.emit(event, payload)
        else:
            writer({"event": event, "data": payload})
        # 埋点失败绝不影响主链路（NoopTracer 在未启用时是空实现）
        try:
            from app.obs.langfuse_tracer import get_tracer

            get_tracer().on_agent_event(
                event,
                payload,
                trace_id=self.trace_id,
                session_id=self.session_id,
            )
        except Exception:  # noqa: BLE001
            pass

    def add_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.stats.add_usage(prompt_tokens, completion_tokens)

    @property
    def latency_ms(self) -> int:
        return int((time.perf_counter() - self.started_at) * 1000)
