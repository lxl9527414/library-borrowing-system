"""LangGraph 主链路：只读流程、写操作确认/取消、多写合并、错误码。

这些用例直接驱动 graph（不经过 HTTP），断言事件序列与状态变化，
覆盖契约 §4.1 事件协议与 §5 错误码的核心分支。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command

from app.agent.graph import build_confirmation_payload
from tests.conftest import run_agent
from tests.fakes import (
    JavaBackendMock,
    ScriptedGateway,
    llm_timeout_error,
    tool_call_message,
)


async def test_read_only_flow_emits_tool_events_then_done(settings) -> None:
    backend = JavaBackendMock()
    run = await run_agent(settings, message="帮我找三体", backend=backend)

    assert run.names[0] == "tool_call"
    tool_call = run.first("tool_call")
    assert tool_call["name"] == "search_books"
    assert tool_call["args"] == {"keyword": "三体"}

    tool_result = run.first("tool_result")
    assert tool_result["ok"] is True
    assert tool_result["data"][0]["title"] == "三体"

    assert "token" in run.names
    assert run.names[-1] == "done"
    assert run.first("done")["usage"]["promptTokens"] > 0
    # 只读流程不应出现确认事件
    assert "confirm_required" not in run.names


async def test_write_flow_pauses_with_confirm_required_then_resumes(settings) -> None:
    backend = JavaBackendMock()
    session_id = "s-confirm-approve"
    confirmations: list[dict] = []

    paused = await run_agent(
        settings,
        message="帮我预约三体",
        session_id=session_id,
        backend=backend,
        on_confirm_required=confirmations.append,
    )
    # 先检索（补 bookId），再进入确认暂停；流以 confirm_required 结束，不出现 done
    assert [name for name, _ in paused.events if name == "tool_call"][0] == "tool_call"
    assert paused.first("confirm_required") is not None
    assert paused.names[-1] == "confirm_required"
    assert "done" not in paused.names

    payload = paused.first("confirm_required")
    assert payload["confirmationId"].startswith("cf-")
    assert payload["tool"] == "reserve_book"
    assert payload["args"] == {"bookId": 1}
    assert "三体" in payload["summary"]
    assert confirmations[-1]["confirmationId"] == payload["confirmationId"]

    resumed = await run_agent(
        settings,
        session_id=session_id,
        backend=backend,
        graph_input=Command(resume={"approved": True}),
    )
    assert resumed.first("confirm_required") is None
    write_call = resumed.first("tool_call")
    assert write_call["name"] == "reserve_book"
    assert write_call["args"] == {"bookId": 1}
    assert resumed.names[-1] == "done"
    assert backend.write_calls == [{"tool": "reserve_book", "body": {"bookId": 1}}]


async def test_rejected_write_skips_execution_and_reports_cancellation(settings) -> None:
    backend = JavaBackendMock()
    session_id = "s-confirm-reject"

    paused = await run_agent(
        settings, message="帮我预约三体", session_id=session_id, backend=backend
    )
    assert paused.names[-1] == "confirm_required"

    resumed = await run_agent(
        settings,
        session_id=session_id,
        backend=backend,
        graph_input=Command(resume={"approved": False}),
    )
    # 写操作被跳过：只有 tool_result(ok=false)，没有 tool_call
    assert all(name != "tool_call" for name, _ in resumed.events)
    result = resumed.first("tool_result")
    assert result["ok"] is False
    assert "取消" in result["summary"]
    assert resumed.names[-1] == "done"
    assert backend.write_calls == []
    assert any("取消" in str(data) for name, data in resumed.events if name == "token")


async def test_multiple_write_tools_are_merged_into_one_confirmation(settings) -> None:
    backend = JavaBackendMock()
    session_id = "s-merged-confirm"
    two_writes = AIMessage(
        content="",
        tool_calls=[
            {"name": "borrow_book", "args": {"bookId": 2}, "id": "c1", "type": "tool_call"},
            {"name": "reserve_book", "args": {"bookId": 1}, "id": "c2", "type": "tool_call"},
        ],
    )
    gateway = ScriptedGateway(script=[two_writes])
    paused = await run_agent(
        settings, message="帮我借活着并预约三体", session_id=session_id, backend=backend, gateway=gateway
    )
    payload = paused.first("confirm_required")
    assert payload is not None
    assert len(payload["tools"]) == 2
    assert payload["tool"] == "multi_tool"
    assert "2 个写操作" in payload["summary"]
    assert paused.names.count("confirm_required") == 1  # 合并成一次确认

    resumed = await run_agent(
        settings,
        session_id=session_id,
        backend=backend,
        gateway=ScriptedGateway(script=[AIMessage(content="已为你办理完成")]),
        graph_input=Command(resume={"approved": True}),
    )
    assert [call["tool"] for call in backend.write_calls] == ["borrow_book", "reserve_book"]
    assert resumed.names[-1] == "done"


async def test_confirmation_id_is_deterministic_for_replay(settings) -> None:
    """interrupt 恢复会重放 authorize 节点，confirmationId 必须稳定。"""
    messages = [HumanMessage(content="帮我预约三体")]
    write_calls = [{"name": "reserve_book", "args": {"bookId": 1}, "id": "c1"}]
    first = build_confirmation_payload("s-1", 2, write_calls, messages)
    second = build_confirmation_payload("s-1", 2, write_calls, messages)
    other = build_confirmation_payload("s-2", 2, write_calls, messages)
    assert first["confirmationId"] == second["confirmationId"]
    assert first["confirmationId"] != other["confirmationId"]


async def test_max_steps_terminates_with_agent_max_steps(settings) -> None:
    gateway = ScriptedGateway(script=[tool_call_message("search_books", {"keyword": "三体"})])
    backend = JavaBackendMock()
    run = await run_agent(
        settings, message="一直查书", backend=backend, gateway=gateway, max_steps=1
    )
    assert run.names[-1] == "error"
    error = run.first("error")
    assert error["code"] == "AGENT_MAX_STEPS"
    assert "最大推理步数" in error["message"]
    assert "done" not in run.names


async def test_llm_timeout_emits_error_event(settings) -> None:
    gateway = ScriptedGateway(script=[llm_timeout_error()])
    run = await run_agent(settings, message="你好", gateway=gateway)
    assert run.names[-1] == "error"
    error = run.first("error")
    assert error["code"] == "LLM_TIMEOUT"
    assert "超时" in error["message"]


async def test_upstream_unauthenticated_terminates_stream(settings) -> None:
    backend = JavaBackendMock()
    backend.unauthorized = True
    run = await run_agent(settings, message="帮我找三体", backend=backend)
    result = run.first("tool_result")
    assert result["ok"] is False
    assert run.names[-1] == "error"
    assert run.first("error")["code"] == "UPSTREAM_UNAUTHENTICATED"
    assert "done" not in run.names


async def test_tool_not_found_is_fed_back_then_model_recovers(settings) -> None:
    gateway = ScriptedGateway(
        script=[
            tool_call_message("delete_everything", {}),
            AIMessage(content="抱歉，我换个方式帮你查。"),
        ]
    )
    run = await run_agent(settings, message="帮我查书", gateway=gateway)
    result = run.first("tool_result")
    assert result["ok"] is False
    assert "不存在" in result["summary"]
    assert run.names[-1] == "done"
    assert len(gateway.calls) == 2  # 错误回灌后模型继续回答


async def test_tool_not_found_gives_up_after_three_rounds(settings) -> None:
    gateway = ScriptedGateway(script=[tool_call_message("delete_everything", {})])
    run = await run_agent(settings, message="帮我查书", gateway=gateway, max_steps=8)
    assert run.names[-1] == "error"
    assert run.first("error")["code"] == "TOOL_NOT_FOUND"
    assert len(gateway.calls) == 3


async def test_tool_upstream_error_is_fed_back_and_conversation_finishes(settings) -> None:
    """Java 接口 5xx：工具失败回灌模型，对话不中断（契约 §5）。"""
    backend = JavaBackendMock()
    backend.internal_server_error = True
    run = await run_agent(settings, message="帮我找三体", backend=backend)
    result = run.first("tool_result")
    assert result["ok"] is False
    assert "业务后端异常" in result["summary"]
    assert run.names[-1] == "done"
    assert run.names.count("error") == 0


async def test_java_backend_unavailable_still_finishes_gracefully(settings) -> None:
    """Java 完全不可用（连接失败）：SSE 仍以 done 结束，工具失败被优雅处理。"""
    backend = JavaBackendMock()
    backend.connection_error = True
    run = await run_agent(settings, message="帮我找三体", backend=backend)
    result = run.first("tool_result")
    assert result["ok"] is False
    assert "无法连接" in result["summary"]
    assert run.names[-1] == "done"
