"""Agent 事件流编排：把 LangGraph 的执行过程翻译成契约 §4.1 的 SSE 事件序列。

结构：图执行放在后台 task 里，节点通过 EventEmitter 写事件；本模块消费同一条队列，
因此 token / tool_call / tool_result 的顺序与真实执行顺序一致，且流一定会以
done / error / confirm_required 三者之一结束。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from langgraph.errors import GraphRecursionError

from app.agent.events import END_OF_STREAM, AgentEvent
from app.agent.graph import CODE_AGENT_MAX_STEPS, get_graph
from app.agent.state import RunContext
from app.llm.base import LLMError
from app.obs.logging import get_logger

logger = get_logger("agent.runner")



class AgentStreamRunner:
    """执行一张图，并把执行过程转成 AgentEvent 流（一次请求一个实例）。"""

    def __init__(self, run_ctx: RunContext, graph: Any = None) -> None:
        self._ctx = run_ctx
        self._graph = graph if graph is not None else get_graph()

    # ------------------------------------------------------------------
    def _graph_config(self) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": self._ctx.session_id,  # 契约：thread_id = sessionId
                "run": self._ctx,
            },
            "recursion_limit": max(25, self._ctx.max_steps * 3),
        }

    def _done_event(self) -> AgentEvent:
        return AgentEvent(
            "done",
            {
                "sessionId": self._ctx.session_id,
                "usage": self._ctx.stats.as_usage(),
                "latencyMs": self._ctx.latency_ms,
            },
        )

    # ------------------------------------------------------------------
    async def astream(self, graph_input: Any) -> AsyncIterator[AgentEvent]:
        """消费图执行事件；调用方直接把这些事件写成 SSE。"""
        ctx = self._ctx
        emitter = ctx.emitter

        async def _produce() -> None:
            try:
                async for mode, chunk in self._graph.astream(
                    graph_input,
                    self._graph_config(),
                    stream_mode=["custom", "updates"],
                ):
                    # 1) 节点内通过 stream_writer 推送的自定义事件（token/tool_call/tool_result）
                    if mode == "custom":
                        if isinstance(chunk, dict) and chunk.get("event"):
                            emitter.emit(str(chunk["event"]), dict(chunk.get("data") or {}))
                        continue

                    update = chunk
                    if not isinstance(update, dict):
                        continue
                    # 2) 图被 interrupt 暂停：登记确认状态并结束本条流
                    if "__interrupt__" in update:
                        interrupts = update.get("__interrupt__") or ()
                        payload = dict(interrupts[0].value or {}) if interrupts else {}
                        if ctx.on_confirm_required is not None:
                            ctx.on_confirm_required(payload)
                        emitter.emit("confirm_required", payload)
                        logger.info(
                            "流程暂停，等待用户确认",
                            extra={
                                "traceId": ctx.trace_id,
                                "sessionId": ctx.session_id,
                                "confirmationId": payload.get("confirmationId"),
                                "code": "CONFIRM_REQUIRED",
                                "durationMs": ctx.latency_ms,
                            },
                        )
                        return
                    # 3) 节点声明了终止事件（AGENT_MAX_STEPS / UPSTREAM_UNAUTHENTICATED）
                    for delta in update.values():
                        if isinstance(delta, dict) and delta.get("terminal"):
                            terminal = delta["terminal"]
                            emitter.emit(terminal["event"], terminal["data"])
                            return
                # 4) 正常结束
                emitter.emit("done", self._done_event().data)
            except asyncio.CancelledError:
                raise
            except GraphRecursionError:
                logger.error(
                    "LangGraph 递归超限，强制结束",
                    extra={"traceId": ctx.trace_id, "sessionId": ctx.session_id},
                )
                emitter.emit(
                    "error",
                    {
                        "code": CODE_AGENT_MAX_STEPS,
                        "message": f"已达到最大推理步数（{ctx.max_steps}），本次对话已强制结束。",
                    },
                )
            except LLMError as exc:
                logger.error(
                    "模型调用失败，流终止",
                    extra={
                        "traceId": ctx.trace_id,
                        "sessionId": ctx.session_id,
                        "provider": exc.provider,
                        "code": exc.code,
                        "durationMs": ctx.latency_ms,
                    },
                )
                emitter.emit("error", {"code": exc.code, "message": exc.message})
            except Exception as exc:  # pragma: no cover - 防御式兜底
                logger.exception(
                    "Agent 执行出现未预期异常",
                    extra={"traceId": ctx.trace_id, "sessionId": ctx.session_id},
                )
                emitter.emit(
                    "error",
                    {
                        "code": "INTERNAL_ERROR",
                        "message": f"AI 服务内部错误（{type(exc).__name__}），请稍后重试。",
                    },
                )
            finally:
                emitter.close()

        task = asyncio.create_task(_produce(), name=f"agent-{ctx.session_id}")
        try:
            while True:
                event = await emitter.queue.get()
                if event is END_OF_STREAM:
                    break
                yield event
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            logger.info(
                "SSE 流结束",
                extra={
                    "traceId": ctx.trace_id,
                    "sessionId": ctx.session_id,
                    "durationMs": ctx.latency_ms,
                    "code": "DONE",
                },
            )
