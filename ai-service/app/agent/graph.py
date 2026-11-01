"""LangGraph 状态机：agent -> authorize(写操作 interrupt) -> tools -> agent。

流程（契约 §6）：
    用户输入 → agent（LLM 决策）
                 ├─ 无 tool_calls → END（输出文本）
                 ├─ 只有只读工具 → tools → agent
                 └─ 含写工具 → authorize（interrupt 暂停）→ tools → agent

实现约束：
1. `interrupt()` 之前的 authorize 节点逻辑必须无副作用（LangGraph 恢复时会重放该节点），
   因此 confirmationId 由 sessionId + step + 工具调用内容哈希得到，保证重放结果一致；
2. 同一次 LLM 回复里的多个写工具合并成一次确认（一个 interrupt，一个确认卡片）；
3. 确认状态（confirmationId -> 工具调用）由 API 层在收到 interrupt 时登记到 ConfirmationStore。
"""

from __future__ import annotations

import functools
import hashlib
import json
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import var_child_runnable_config
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.state import AgentState, RunContext
from app.obs.logging import get_logger
from app.tools.executor import CODE_TOOL_NOT_FOUND
from app.tools.schemas import CONFIRM_SUMMARY_TEMPLATES, TOOL_SCHEMAS, is_write_tool

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，运行时延迟导入避免循环依赖
    from app.agent.checkpointer import CheckpointerBackend

logger = get_logger("agent.graph")

CODE_AGENT_MAX_STEPS = "AGENT_MAX_STEPS"
CODE_UPSTREAM_UNAUTHENTICATED = "UPSTREAM_UNAUTHENTICATED"
# 契约 §5：模型调用了不存在的工具时，最多回灌错误 3 轮让它自我纠正
MAX_TOOL_NOT_FOUND_ROUNDS = 3


# ----------------------------------------------------------------------
# Python 3.10 兼容垫片
# ----------------------------------------------------------------------
def _with_config_context(
    fn: Callable[..., Awaitable[dict[str, Any]]],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """给节点补上 `var_child_runnable_config` contextvar。

    LangGraph 1.x 在 Python >= 3.11 会用 `asyncio.create_task(..., context=...)`
    把配置上下文传进节点；3.10 的 create_task 没有 context 参数，导致节点内
    调用 `interrupt()` / `get_stream_writer()` 时报
    `Called get_config outside of a runnable context`。
    这里在节点入口显式 set/reset 该 contextvar，使官方 API 在 3.10 上可用。
    """
    @functools.wraps(fn)
    async def _wrapper(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        token = var_child_runnable_config.set(config)
        try:
            return await fn(state, config)
        finally:
            var_child_runnable_config.reset(token)

    return _wrapper


# ----------------------------------------------------------------------
# 内部工具函数
# ----------------------------------------------------------------------
def _get_ctx(config: RunnableConfig) -> RunContext:
    return config["configurable"]["run"]


def _message_text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return ""


def _recent_tool_payloads(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """回溯当前用户回合内的工具结果（用于给确认卡片补上书名等上下文）。"""
    payloads: list[dict[str, Any]] = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, ToolMessage):
            try:
                parsed = json.loads(_message_text(message))
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict):
                payloads.append(parsed)
    return payloads


def _candidate_items(data: Any) -> list[dict[str, Any]]:
    """把工具 data 摊平成候选实体列表（search 返回 list，detail 返回 dict）。"""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("records"), list):
            return [item for item in data["records"] if isinstance(item, dict)]
        if isinstance(data.get("book"), dict):
            return [data["book"]]
        return [data]
    return []


def _find_title(messages: list[BaseMessage], args: dict[str, Any]) -> str | None:
    book_id = args.get("bookId")
    borrow_id = args.get("borrowId")
    for payload in _recent_tool_payloads(messages):
        for item in _candidate_items(payload.get("data")):
            title = item.get("title") or item.get("bookTitle")
            if not title:
                continue
            item_id = item.get("id") or item.get("bookId")
            if book_id is not None and str(item_id) == str(book_id):
                return str(title)
            if borrow_id is not None and str(item.get("id")) == str(borrow_id):
                return str(item.get("bookTitle") or title)
    return None


def _single_call_summary(name: str, args: dict[str, Any], title: str | None) -> str:
    template = CONFIRM_SUMMARY_TEMPLATES.get(name, "即将执行 {tool}，是否确认？")
    return template.format(
        title=title or "该图书",
        bookId=args.get("bookId", "-"),
        borrowId=args.get("borrowId", "-"),
        tool=name,
    )


def build_confirmation_payload(
    session_id: str,
    step: int,
    write_calls: list[dict[str, Any]],
    messages: list[BaseMessage],
) -> dict[str, Any]:
    """构造 confirm_required 事件的 data（确定性：同一状态重放得到同一 confirmationId）。"""
    signature = json.dumps(
        [{"name": call.get("name"), "args": call.get("args")} for call in write_calls],
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha1(f"{session_id}:{step}:{signature}".encode("utf-8")).hexdigest()
    confirmation_id = f"cf-{digest[:12]}"

    tools: list[dict[str, Any]] = []
    for call in write_calls:
        name = str(call.get("name") or "")
        raw_args = call.get("args")
        args = dict(raw_args) if isinstance(raw_args, dict) else {}
        title = _find_title(messages, args)
        tools.append(
            {
                "callId": str(call.get("id") or ""),
                "tool": name,
                "args": args,
                "summary": _single_call_summary(name, args, title),
            }
        )

    if len(tools) == 1:
        summary = tools[0]["summary"]
    else:
        joined = "；".join(f"{index + 1}) {item['summary']}" for index, item in enumerate(tools))
        summary = f"本次共 {len(tools)} 个写操作需要确认：{joined} 是否全部确认？"

    payload: dict[str, Any] = {
        "confirmationId": confirmation_id,
        "summary": summary,
        "tools": tools,
    }
    # 单个写操作时额外透出扁平的 tool/args 字段，兼容契约 §4.1 的示例结构
    if len(tools) == 1:
        payload["tool"] = tools[0]["tool"]
        payload["args"] = tools[0]["args"]
    else:
        payload["tool"] = "multi_tool"
        payload["args"] = {
            "toolCalls": [{"callId": item["callId"], "name": item["tool"], "args": item["args"]} for item in tools]
        }
    return payload


def _build_model_messages(state: AgentState, ctx: RunContext) -> list[BaseMessage]:
    """SystemPrompt + 历史消息（过长时按“从最近一条用户消息开始”裁剪）。"""
    history: list[BaseMessage] = list(state.get("messages") or [])
    limit = ctx.max_history_messages
    if len(history) > limit:
        cut = max(len(history) - limit, 0)
        for index in range(cut, len(history)):
            if isinstance(history[index], HumanMessage):
                cut = index
                break
        history = history[cut:]
    return [SystemMessage(content=ctx.system_prompt), *history]


# ----------------------------------------------------------------------
# 节点
# ----------------------------------------------------------------------
@_with_config_context
async def agent_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """调用模型：流式 token 直接推给 SSE，返回 AIMessage（可能带 tool_calls）。"""
    ctx = _get_ctx(config)
    step = int(state.get("step") or 0) + 1
    if step > ctx.max_steps:
        logger.warning(
            "超过最大推理步数，强制结束",
            extra={"traceId": ctx.trace_id, "sessionId": ctx.session_id, "step": step},
        )
        return {
            "step": step,
            "terminal": {
                "event": "error",
                "data": {
                    "code": CODE_AGENT_MAX_STEPS,
                    "message": f"已达到最大推理步数（{ctx.max_steps}），本次对话已强制结束。",
                },
            },
        }

    result = await ctx.gateway.astream_chat(
        _build_model_messages(state, ctx),
        tools=TOOL_SCHEMAS,
        on_token=lambda text: ctx.emit("token", {"text": text}),
    )
    ctx.add_usage(result.prompt_tokens, result.completion_tokens)
    return {"step": step, "messages": [result.message], "approval": None}


@_with_config_context
async def authorize_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """写操作授权节点：interrupt 暂停，等待 /chat/confirm 恢复。

    注意：interrupt 之前的代码会在恢复时重放，因此这里只做纯计算，不写存储、不发事件。
    """
    ctx = _get_ctx(config)
    last_message = state["messages"][-1]
    tool_calls = list(getattr(last_message, "tool_calls", []) or [])
    write_calls = [call for call in tool_calls if is_write_tool(str(call.get("name") or ""))]

    payload = build_confirmation_payload(
        session_id=ctx.session_id,
        step=int(state.get("step") or 0),
        write_calls=write_calls,
        messages=list(state.get("messages") or []),
    )
    decision = interrupt(payload)
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else False
    logger.info(
        "写操作确认结果",
        extra={
            "traceId": ctx.trace_id,
            "sessionId": ctx.session_id,
            "confirmationId": payload["confirmationId"],
            "code": "APPROVED" if approved else "REJECTED",
        },
    )
    return {"approval": {"approved": approved, "confirmationId": payload["confirmationId"]}}


@_with_config_context
async def tools_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """执行工具调用：只读直接跑；写操作若被拒绝则回灌“用户已取消”。"""
    ctx = _get_ctx(config)
    last_message = state["messages"][-1]
    tool_calls = list(getattr(last_message, "tool_calls", []) or [])
    approval = state.get("approval") or None
    denied = approval is not None and not bool(approval.get("approved"))

    new_messages: list[ToolMessage] = []
    terminal: dict[str, Any] | None = None
    # 连续调用不存在工具的轮数（成功调用一次即清零）
    not_found_count = int(state.get("tool_not_found_count") or 0)

    for index, call in enumerate(tool_calls):
        name = str(call.get("name") or "")
        call_id = str(call.get("id") or f"call-{index}")
        raw_args = call.get("args")
        args = dict(raw_args) if isinstance(raw_args, dict) else {}

        if is_write_tool(name) and denied:
            summary = "用户已取消该操作"
            ctx.emit("tool_result", {"callId": call_id, "name": name, "ok": False, "summary": summary})
            new_messages.append(
                ToolMessage(
                    content=json.dumps({"ok": False, "summary": summary, "cancelled": True}, ensure_ascii=False),
                    tool_call_id=call_id,
                    name=name,
                )
            )
            continue

        ctx.emit("tool_call", {"callId": call_id, "name": name, "args": args})
        result = await ctx.executor.execute({"id": call_id, "name": name, "args": args})
        ctx.emit(
            "tool_result",
            {
                "callId": call_id,
                "name": name,
                "ok": result.ok,
                "summary": result.summary,
                "data": result.data,
            },
        )
        new_messages.append(
            ToolMessage(
                content=json.dumps(result.to_tool_message_payload(), ensure_ascii=False, default=str),
                tool_call_id=call_id,
                name=name,
            )
        )
        if result.error_code == CODE_TOOL_NOT_FOUND:
            not_found_count += 1
        else:
            not_found_count = 0
        if result.fatal:
            terminal = {
                "event": "error",
                "data": {"code": CODE_UPSTREAM_UNAUTHENTICATED, "message": result.summary},
            }
            break
        if not_found_count >= MAX_TOOL_NOT_FOUND_ROUNDS:
            terminal = {
                "event": "error",
                "data": {
                    "code": CODE_TOOL_NOT_FOUND,
                    "message": "模型连续调用了不存在的工具，已结束本次对话，请换个说法再试。",
                },
            }
            break

    return {
        "messages": new_messages,
        "approval": None,
        "terminal": terminal,
        "tool_not_found_count": not_found_count,
    }


# ----------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------
def route_after_agent(state: AgentState) -> str:
    if state.get("terminal"):
        return END
    last_message = state["messages"][-1]
    tool_calls = list(getattr(last_message, "tool_calls", []) or [])
    if not tool_calls:
        return END
    if any(is_write_tool(str(call.get("name") or "")) for call in tool_calls):
        return "authorize"
    return "tools"


def route_after_tools(state: AgentState) -> str:
    if state.get("terminal"):
        return END
    return "agent"


def build_graph(checkpointer: Any = None) -> Any:
    """编译状态机；checkpointer 由工厂提供（默认 InMemorySaver，测试/降级路径）。"""
    graph: StateGraph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("authorize", authorize_node)
    graph.add_node("tools", tools_node)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        route_after_agent,
        {"authorize": "authorize", "tools": "tools", END: END},
    )
    graph.add_edge("authorize", "tools")
    graph.add_conditional_edges("tools", route_after_tools, {"agent": "agent", END: END})
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())


_graph: Any = None
# 与图绑定的 checkpointer 实例：进程级单例，应用退出时由 shutdown_graph 关闭连接
_checkpointer: Any = None
_checkpointer_backend: "CheckpointerBackend" = "memory"


async def init_graph(settings: Any, *, saver_cls: Any = None) -> "CheckpointerBackend":
    """应用启动时调用：通过工厂拿到 checkpointer 并编译进程级单例图。

    优先 Redis（AsyncRedisSaver，跨重启/多实例共享 interrupt 状态）；
    Redis 不可用时工厂会降级 InMemorySaver 并 log.warn，绝不阻断启动。
    """
    global _graph, _checkpointer, _checkpointer_backend

    from app.agent.checkpointer import create_checkpointer  # 延迟导入，避免测试路径加载 redis 依赖

    if _checkpointer is not None:
        from app.agent.checkpointer import close_checkpointer

        await close_checkpointer(_checkpointer)
    saver, backend = await create_checkpointer(settings, saver_cls=saver_cls)
    _checkpointer = saver
    _checkpointer_backend = backend
    _graph = build_graph(saver)
    logger.info(
        f"Agent 状态机已编译（checkpointer={backend}）",
        extra={"code": "GRAPH_READY"},
    )
    return backend


def get_graph() -> Any:
    """进程级单例：checkpointer 必须在请求之间共享，否则无法恢复 interrupt。

    未经过 lifespan 初始化时（单元测试直接驱动图）懒加载 InMemorySaver 版本。
    """
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def get_checkpointer_backend() -> "CheckpointerBackend":
    """当前生效的 checkpointer 后端（health 端点展示用）。"""
    return _checkpointer_backend


async def shutdown_graph() -> None:
    """应用退出时关闭 checkpointer 连接（幂等）。"""
    global _graph, _checkpointer, _checkpointer_backend
    from app.agent.checkpointer import close_checkpointer

    await close_checkpointer(_checkpointer)
    _checkpointer = None
    _checkpointer_backend = "memory"
    _graph = None


def reset_graph() -> None:
    """测试辅助：丢弃检查点，重新编译状态机。"""
    global _graph, _checkpointer, _checkpointer_backend
    _graph = None
    _checkpointer = None
    _checkpointer_backend = "memory"

