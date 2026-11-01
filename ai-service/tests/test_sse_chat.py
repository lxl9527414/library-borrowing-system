"""HTTP + SSE 端到端：事件协议、确认流程、错误优雅处理（走 ASGI，不启真实端口）。"""

from __future__ import annotations

import json

from tests.conftest import AUTH_HEADERS
from tests.fakes import JavaBackendMock

TERMINAL_EVENTS = {"done", "error", "confirm_required"}


async def test_chat_streams_meta_then_tool_events_then_done(api) -> None:
    result = await api.chat("帮我找三体")
    assert result.status == 200
    assert result.content_type.startswith("text/event-stream")

    assert result.names[0] == "meta"
    meta = result.first("meta")
    assert meta["sessionId"].startswith("s-")
    assert meta["traceId"].startswith("t-")

    tool_call = result.first("tool_call")
    assert tool_call["name"] == "search_books"
    assert tool_call["args"] == {"keyword": "三体"}

    tool_result = result.first("tool_result")
    assert tool_result["ok"] is True
    assert tool_result["data"][0]["title"] == "三体"

    tokens = [data["text"] for name, data in result.events if name == "token"]
    assert "".join(tokens)
    assert "三体" in "".join(tokens)

    assert result.names[-1] == "done"
    done = result.first("done")
    assert done["sessionId"] == meta["sessionId"]
    assert set(done["usage"]) == {"promptTokens", "completionTokens"}
    assert done["latencyMs"] >= 0


async def test_every_stream_ends_with_terminal_event(api) -> None:
    find = await api.chat("帮我找三体")
    assert find.names[-1] in TERMINAL_EVENTS
    write = await api.chat("帮我预约三体")
    assert write.names[-1] in TERMINAL_EVENTS


async def test_write_flow_confirm_approved_executes_reservation(api, backend: JavaBackendMock) -> None:
    paused = await api.chat("帮我预约三体")
    session_id = paused.first("meta")["sessionId"]
    assert paused.names[-1] == "confirm_required"

    payload = paused.first("confirm_required")
    assert payload["tool"] == "reserve_book"
    assert payload["args"] == {"bookId": 1}
    assert "三体" in payload["summary"]
    assert backend.write_calls == []  # 未确认前绝不能执行写操作

    resumed = await api.confirm(session_id, payload["confirmationId"], approved=True)
    assert resumed.status == 200
    assert resumed.names[0] == "meta"
    assert resumed.first("confirm_required") is None
    tool_call = resumed.first("tool_call")
    assert tool_call["name"] == "reserve_book"
    assert resumed.names[-1] == "done"
    assert backend.write_calls == [{"tool": "reserve_book", "body": {"bookId": 1}}]

    # 确认是一次性的：重复确认返回失效错误事件
    duplicated = await api.confirm(session_id, payload["confirmationId"], approved=True)
    assert duplicated.names == ["meta", "error"]
    assert duplicated.first("error")["code"] == "CONFIRMATION_INVALID"
    assert len(backend.write_calls) == 1


async def test_write_flow_confirm_rejected_does_not_execute(api, backend: JavaBackendMock) -> None:
    paused = await api.chat("帮我预约三体")
    session_id = paused.first("meta")["sessionId"]
    confirmation_id = paused.first("confirm_required")["confirmationId"]

    resumed = await api.confirm(session_id, confirmation_id, approved=False)
    assert resumed.names[-1] == "done"
    assert resumed.first("tool_result")["ok"] is False
    assert all(name != "tool_call" for name, _ in resumed.events)
    assert any("取消" in str(data) for name, data in resumed.events if name == "token")
    assert backend.write_calls == []


async def test_new_message_while_confirmation_pending_is_rejected(api) -> None:
    paused = await api.chat("帮我预约三体")
    session_id = paused.first("meta")["sessionId"]
    assert paused.names[-1] == "confirm_required"

    blocked = await api.chat("帮我找活着", session_id=session_id)
    assert blocked.names == ["meta", "error"]
    assert blocked.first("error")["code"] == "CONFIRMATION_PENDING"


async def test_confirm_with_unknown_id_returns_error_stream(api) -> None:
    result = await api.confirm("s-unknown", "cf-not-exist", approved=True)
    assert result.status == 200
    assert result.names == ["meta", "error"]
    assert result.first("error")["code"] == "CONFIRMATION_INVALID"


async def test_confirm_with_wrong_session_is_rejected(api) -> None:
    paused = await api.chat("帮我预约三体")
    session_id = paused.first("meta")["sessionId"]
    confirmation_id = paused.first("confirm_required")["confirmationId"]

    result = await api.confirm("s-other-session", confirmation_id, approved=True)
    assert result.names == ["meta", "error"]
    assert result.first("error")["code"] == "CONFIRMATION_INVALID"


async def test_unauthorized_token_returns_http_401(api, backend: JavaBackendMock) -> None:
    backend.unauthorized = True
    result = await api.chat("帮我找三体")
    assert result.status == 401
    # 预取上下文就发现 401，直接按 HTTP 401 透传，不进入 SSE
    assert result.events == []


async def test_java_backend_unavailable_stream_still_finishes(api, backend: JavaBackendMock) -> None:
    """验收场景：Java 层不可用，工具调用失败必须被优雅处理，SSE 协议仍正确。"""
    backend.connection_error = True
    result = await api.chat("帮我找三体")
    assert result.names[0] == "meta"
    tool_result = result.first("tool_result")
    assert tool_result["ok"] is False
    assert "无法连接" in tool_result["summary"]
    assert result.names[-1] == "done"


async def test_java_backend_500_stream_still_finishes(api, backend: JavaBackendMock) -> None:
    backend.internal_server_error = True
    result = await api.chat("帮我找三体")
    tool_result = result.first("tool_result")
    assert tool_result["ok"] is False
    assert result.names[-1] == "done"
    assert result.first("error") is None


async def test_confirm_write_business_failure_is_reported_to_user(api, backend: JavaBackendMock) -> None:
    """确认后被业务规则拒绝（如库存被抢）也要回灌模型、正常 done。"""
    backend.books[0]["availableStock"] = 1  # 三体先有 1 本可借，模型才会发起借阅
    paused = await api.chat("帮我借三体")
    session_id = paused.first("meta")["sessionId"]
    payload = paused.first("confirm_required")
    assert payload["tool"] == "borrow_book"

    backend.books[0]["availableStock"] = 0  # 用户确认前库存被别人借走
    resumed = await api.confirm(session_id, payload["confirmationId"], approved=True)
    tool_result = resumed.first("tool_result")
    assert tool_result["ok"] is False
    assert "库存" in tool_result["summary"]
    assert resumed.names[-1] == "done"
    assert backend.write_calls == []  # 后端拒绝，没有真正写入


async def test_multi_turn_session_reuses_context(api) -> None:
    first = await api.chat("帮我找红楼梦")
    session_id = first.first("meta")["sessionId"]
    assert first.names[-1] == "done"

    second = await api.chat("我的借阅", session_id=session_id)
    assert second.first("meta")["sessionId"] == session_id
    assert second.names[-1] == "done"
    assert second.first("tool_call")["name"] == "get_my_borrows"

    history = (await api.get(f"/api/ai/sessions/{session_id}")).json()["messages"]
    assert len(history) >= 4
    assert history[0]["role"] == "user"


async def test_session_history_is_persisted(api) -> None:
    result = await api.chat("帮我找三体")
    session_id = result.first("meta")["sessionId"]

    response = await api.get(f"/api/ai/sessions/{session_id}")
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert messages[0] == {"role": "user", "content": "帮我找三体"}
    assert any(message["role"] == "assistant" for message in messages)
    assert any(message["role"] == "tool" for message in messages)


async def test_sse_data_lines_are_single_line_json(api) -> None:
    """契约要求 data 为单行 JSON（data 内不能出现裸换行）。"""
    from app.main import app  # noqa: PLC0415 - 确保 app 已初始化

    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
        async with client.stream(
            "POST", "/api/ai/chat", json={"message": "帮我找三体"}, headers=AUTH_HEADERS
        ) as response:
            raw = await response.aread()
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("data:"):
            payload = line.split(":", 1)[1].strip()
            assert json.loads(payload) is not None
