"""AI 服务对外 HTTP 接口（契约 §4）。

- POST /api/ai/chat          对话（SSE）
- POST /api/ai/chat/confirm  写操作确认后恢复（同一套 SSE 事件）
- GET  /api/ai/health        健康检查
- GET  /api/ai/sessions/{id} 会话历史（调试用）
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.types import Command
from sse_starlette.sse import EventSourceResponse

from app.agent.events import AgentEvent, EventEmitter
from app.agent.graph import get_graph
from app.agent.prompts import DEFAULT_SYSTEM_PROMPT, build_system_prompt
from app.agent.runner import AgentStreamRunner
from app.agent.state import RunContext
from app.api.models import ChatRequest, ConfirmRequest, HealthResponse, SessionResponse
from app.config import Settings, get_settings
from app.llm.gateway import LLMGateway
from app.memory.store import get_confirmation_store, get_session_store
from app.obs.logging import Stopwatch, get_logger, new_trace_id
from app.tools.executor import ToolExecutor
from app.tools.java_client import JavaClient, UpstreamUnauthenticated

router = APIRouter()
logger = get_logger("api.chat")

# SSE 额外响应头：禁用 Nginx / 中间层缓冲，保证逐事件到达前端
SSE_HEADERS = {"X-Accel-Buffering": "no"}
PING_INTERVAL_SECONDS = 15


# ----------------------------------------------------------------------
# 事件与请求辅助
# ----------------------------------------------------------------------
def _sse(event: AgentEvent) -> dict[str, str]:
    return {
        "event": event.event,
        "data": json.dumps(event.data, ensure_ascii=False, default=str),
    }


def _extract_authorization(request: Request) -> str:
    """用户身份只从浏览器带来的 Authorization 取（契约 §1 核心原则 2）。"""
    authorization = (request.headers.get("authorization") or "").strip()
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="缺少 Authorization 请求头，请先登录后再使用 AI 助手。",
        )
    return authorization


def _build_java_client(
    request: Request,
    settings: Settings,
    authorization: str,
    trace_id: str,
) -> JavaClient:
    # 测试可通过 app.state.java_transport 注入 httpx.MockTransport，避免真实网络
    transport = getattr(request.app.state, "java_transport", None)
    return JavaClient(
        settings,
        authorization=authorization,
        trace_id=trace_id,
        transport=transport,
    )


async def _load_session_context(
    java_client: JavaClient,
    settings: Settings,
    trace_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """预取会话上下文与规则文案（契约 §2.1 / §2.2，服务端注入、非 LLM 工具）。

    业务后端不可用时降级为“无上下文模式”，让对话仍可进行（工具调用会在执行时报错）；
    只有 401 需要向上抛出让前端重新登录。
    """
    context_result, rules_result = await asyncio.gather(
        java_client.get_context(timeout=settings.java_context_timeout_seconds),
        java_client.get_rules_text(timeout=settings.java_context_timeout_seconds),
        return_exceptions=True,
    )

    context: dict[str, Any] | None = None
    rules_text: str | None = None
    for name, result in (("context", context_result), ("rules", rules_result)):
        if isinstance(result, UpstreamUnauthenticated):
            raise result
        if isinstance(result, BaseException):
            logger.warning(
                "预取会话上下文失败，降级继续",
                extra={"traceId": trace_id, "code": type(result).__name__},
            )
            continue
        if name == "context" and isinstance(result, dict):
            context = result
        elif name == "rules" and isinstance(result, str) and result:
            rules_text = result
    return context, rules_text


def _new_run_context(
    settings: Settings,
    *,
    session_id: str,
    trace_id: str,
    system_prompt: str,
    java_client: JavaClient,
) -> RunContext:
    confirmation_store = get_confirmation_store()

    def _register_confirmation(payload: dict[str, Any]) -> None:
        confirmation_store.create(
            session_id=session_id,
            confirmation_id=str(payload.get("confirmationId") or ""),
            payload=payload,
            ttl_seconds=settings.confirmation_ttl_seconds,
        )

    return RunContext(
        session_id=session_id,
        trace_id=trace_id,
        emitter=EventEmitter(),
        gateway=LLMGateway(settings),
        executor=ToolExecutor(java_client, trace_id=trace_id, session_id=session_id),
        system_prompt=system_prompt,
        max_steps=settings.agent_max_steps,
        max_history_messages=settings.ai_max_history_turns * 4,
        on_confirm_required=_register_confirmation,
    )


def _serialize_message(message: BaseMessage) -> dict[str, Any]:
    role = {"human": "user", "ai": "assistant", "tool": "tool"}.get(message.type, message.type)
    content = message.content if isinstance(message.content, str) else str(message.content)
    payload: dict[str, Any] = {"role": role, "content": content}
    tool_calls = getattr(message, "tool_calls", None)
    if role == "assistant" and tool_calls:
        payload["toolCalls"] = [
            {"name": call.get("name"), "args": call.get("args")} for call in tool_calls
        ]
    if role == "tool":
        payload["name"] = getattr(message, "name", None)
    return payload


async def _persist_transcript(settings: Settings, session_id: str, trace_id: str) -> None:
    """把图状态里的消息落进会话存储，供 /sessions/{id} 调试使用。"""
    try:
        snapshot = await get_graph().aget_state({"configurable": {"thread_id": session_id}})
        messages = list((snapshot.values or {}).get("messages") or [])
        transcript = [_serialize_message(message) for message in messages]
        get_session_store().save_transcript(session_id, transcript)
    except Exception:  # pragma: no cover - 调试端点数据，失败不影响主链路
        logger.warning(
            "保存会话历史失败",
            extra={"traceId": trace_id, "sessionId": session_id},
        )


def _single_event_stream(session_id: str, trace_id: str, event: AgentEvent) -> EventSourceResponse:
    """返回只有 meta + 指定终止事件的 SSE 流（用于确认信息失效等场景）。"""

    async def _generator() -> AsyncIterator[dict[str, str]]:
        yield _sse(AgentEvent("meta", {"sessionId": session_id, "traceId": trace_id}))
        yield _sse(event)

    return EventSourceResponse(_generator(), ping=PING_INTERVAL_SECONDS, headers=SSE_HEADERS)


# ----------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------
@router.post("/chat")
async def chat(payload: ChatRequest, request: Request) -> EventSourceResponse:
    settings = get_settings()
    authorization = _extract_authorization(request)
    trace_id = new_trace_id()
    session_id = payload.sessionId or f"s-{uuid.uuid4().hex[:12]}"
    stopwatch = Stopwatch()
    logger.info(
        "收到对话请求",
        extra={"traceId": trace_id, "sessionId": session_id, "event": "chat.start"},
    )

    java_client = _build_java_client(request, settings, authorization, trace_id)
    try:
        user_context, rules_text = await _load_session_context(java_client, settings, trace_id)
    except UpstreamUnauthenticated as exc:
        await java_client.aclose()
        raise HTTPException(status_code=401, detail=str(exc.message)) from exc

    system_prompt = (
        build_system_prompt(user_context, rules_text)
        if (user_context or rules_text)
        else DEFAULT_SYSTEM_PROMPT
    )

    graph = get_graph()
    # 若会话上一笔写操作还在等待确认，直接拒绝新输入：
    # LangGraph 在 pending interrupt 时收到新输入会被当成 resume 值，语义会错乱。
    try:
        snapshot = await graph.aget_state({"configurable": {"thread_id": session_id}})
    except Exception:  # pragma: no cover - 检查失败时不阻塞主流程
        snapshot = None
    if snapshot is not None and snapshot.next:
        await java_client.aclose()
        return _single_event_stream(
            session_id,
            trace_id,
            AgentEvent(
                "error",
                {
                    "code": "CONFIRMATION_PENDING",
                    "message": "上一笔写操作还在等待确认，请先确认或取消后再发送新消息。",
                },
            ),
        )

    async def _event_stream() -> AsyncIterator[dict[str, str]]:
        run_ctx = _new_run_context(
            settings,
            session_id=session_id,
            trace_id=trace_id,
            system_prompt=system_prompt,
            java_client=java_client,
        )
        runner = AgentStreamRunner(run_ctx, graph)
        try:
            yield _sse(AgentEvent("meta", {"sessionId": session_id, "traceId": trace_id}))
            async for event in runner.astream({"messages": [HumanMessage(content=payload.message)]}):
                yield _sse(event)
        finally:
            await java_client.aclose()
            await _persist_transcript(settings, session_id, trace_id)
            logger.info(
                "对话请求结束",
                extra={
                    "traceId": trace_id,
                    "sessionId": session_id,
                    "durationMs": stopwatch.elapsed_ms,
                    "event": "chat.end",
                },
            )

    return EventSourceResponse(_event_stream(), ping=PING_INTERVAL_SECONDS, headers=SSE_HEADERS)


@router.post("/chat/confirm")
async def chat_confirm(payload: ConfirmRequest, request: Request) -> EventSourceResponse:
    settings = get_settings()
    authorization = _extract_authorization(request)
    trace_id = new_trace_id()
    session_id = payload.sessionId
    logger.info(
        "收到写操作确认",
        extra={
            "traceId": trace_id,
            "sessionId": session_id,
            "confirmationId": payload.confirmationId,
            "code": "APPROVED" if payload.approved else "REJECTED",
        },
    )

    # 确认记录一次性消费：重复点击不会重复执行写操作
    record = get_confirmation_store().consume(payload.confirmationId, session_id)
    if record is None:
        logger.warning(
            "确认信息无效或已过期",
            extra={"traceId": trace_id, "sessionId": session_id, "confirmationId": payload.confirmationId},
        )
        return _single_event_stream(
            session_id,
            trace_id,
            AgentEvent(
                "error",
                {
                    "code": "CONFIRMATION_INVALID",
                    "message": "确认信息已失效（可能已超时或已处理），请重新发起操作。",
                },
            ),
        )

    graph = get_graph()
    try:
        snapshot = await graph.aget_state({"configurable": {"thread_id": session_id}})
    except Exception:  # pragma: no cover
        snapshot = None
    if snapshot is None or not snapshot.next:
        return _single_event_stream(
            session_id,
            trace_id,
            AgentEvent(
                "error",
                {
                    "code": "CONFIRMATION_INVALID",
                    "message": "该会话没有等待确认的写操作（可能已执行或已过期）。",
                },
            ),
        )

    java_client = _build_java_client(request, settings, authorization, trace_id)
    try:
        user_context, rules_text = await _load_session_context(java_client, settings, trace_id)
    except UpstreamUnauthenticated as exc:
        await java_client.aclose()
        raise HTTPException(status_code=401, detail=str(exc.message)) from exc

    system_prompt = (
        build_system_prompt(user_context, rules_text)
        if (user_context or rules_text)
        else DEFAULT_SYSTEM_PROMPT
    )

    async def _event_stream() -> AsyncIterator[dict[str, str]]:
        run_ctx = _new_run_context(
            settings,
            session_id=session_id,
            trace_id=trace_id,
            system_prompt=system_prompt,
            java_client=java_client,
        )
        runner = AgentStreamRunner(run_ctx, graph)
        resume = Command(resume={"approved": payload.approved, "confirmationId": payload.confirmationId})
        try:
            yield _sse(AgentEvent("meta", {"sessionId": session_id, "traceId": trace_id}))
            async for event in runner.astream(resume):
                yield _sse(event)
        finally:
            await java_client.aclose()
            await _persist_transcript(settings, session_id, trace_id)
            logger.info(
                "写操作确认处理结束",
                extra={
                    "traceId": trace_id,
                    "sessionId": session_id,
                    "confirmationId": payload.confirmationId,
                },
            )

    return EventSourceResponse(_event_stream(), ping=PING_INTERVAL_SECONDS, headers=SSE_HEADERS)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        llmProvider=settings.llm_provider,
        version=settings.service_version,
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse, response_model_exclude_none=True)
async def get_session(session_id: str) -> SessionResponse:
    """调试端点：返回会话最近 20 条消息（生产环境应加内网/鉴权限制）。"""
    messages = get_session_store().get_transcript(session_id)
    return SessionResponse(sessionId=session_id, messages=messages[-20:])
