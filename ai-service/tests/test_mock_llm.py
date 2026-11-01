"""Mock 模型：意图识别与输出必须确定（否则 SSE 测试会 flaky）。"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, ToolMessage

from app.llm.mock import MockChatModel
from app.tools.schemas import TOOL_SCHEMAS


async def _invoke(text: str):
    return await MockChatModel().ainvoke([HumanMessage(content=text)])


async def test_search_intent_maps_to_search_books() -> None:
    message = await _invoke("帮我找三体")
    assert len(message.tool_calls) == 1
    call = message.tool_calls[0]
    assert call["name"] == "search_books"
    assert call["args"] == {"keyword": "三体"}


async def test_reserve_intent_first_resolves_book_id_by_search() -> None:
    message = await _invoke("帮我预约三体")
    assert message.tool_calls[0]["name"] == "search_books"
    assert message.tool_calls[0]["args"]["keyword"] == "三体"


async def test_reserve_intent_with_explicit_id_skips_search() -> None:
    message = await _invoke("帮我预约 bookId 3")
    assert message.tool_calls[0]["name"] == "reserve_book"
    assert message.tool_calls[0]["args"] == {"bookId": 3}


async def test_return_intent_queries_my_borrows_first() -> None:
    message = await _invoke("我要还书")
    assert message.tool_calls[0]["name"] == "get_my_borrows"


async def test_return_intent_with_id_calls_return_book() -> None:
    message = await _invoke("归还借阅记录 10")
    assert message.tool_calls[0]["name"] == "return_book"
    assert message.tool_calls[0]["args"] == {"borrowId": 10}


async def test_my_borrows_intent() -> None:
    message = await _invoke("我的借阅有没有逾期")
    assert message.tool_calls[0]["name"] == "get_my_borrows"


async def test_detail_intent() -> None:
    message = await _invoke("三体还有库存吗")
    assert message.tool_calls[0]["name"] == "get_book_detail"
    assert message.tool_calls[0]["args"] == {"bookId": 1}


async def test_greeting_has_no_tool_calls_and_is_deterministic() -> None:
    first = await _invoke("你好")
    second = await _invoke("你好")
    assert first.tool_calls == []
    assert isinstance(first.content, str) and first.content
    assert first.content == second.content


async def test_after_search_without_stock_and_reserve_intent_continues_to_reserve() -> None:
    tool_message = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "summary": "找到 1 本图书，当前可借 0 本",
                "data": [{"id": 1, "title": "三体", "availableStock": 0}],
            },
            ensure_ascii=False,
        ),
        tool_call_id="call_mock_1",
        name="search_books",
    )
    messages = [
        HumanMessage(content="帮我找三体，如果没货就预约"),
        ToolMessage(
            content="{}",
            tool_call_id="call_mock_0",
            name="search_books",
        ),
        tool_message,
    ]
    message = await MockChatModel().ainvoke(messages)
    assert message.tool_calls[0]["name"] == "reserve_book"
    assert message.tool_calls[0]["args"] == {"bookId": 1}


async def test_after_cancelled_write_returns_cancel_text() -> None:
    tool_message = ToolMessage(
        content=json.dumps({"ok": False, "summary": "用户已取消该操作", "cancelled": True}, ensure_ascii=False),
        tool_call_id="call_mock_1",
        name="reserve_book",
    )
    message = await MockChatModel().ainvoke(
        [HumanMessage(content="帮我预约三体"), tool_message]
    )
    assert message.tool_calls == []
    assert "取消" in str(message.content)


async def test_after_failed_tool_reports_failure_without_retry() -> None:
    tool_message = ToolMessage(
        content=json.dumps(
            {"ok": False, "summary": "该图书暂无可借库存，可预约排队"}, ensure_ascii=False
        ),
        tool_call_id="call_mock_1",
        name="borrow_book",
    )
    message = await MockChatModel().ainvoke([HumanMessage(content="帮我借三体"), tool_message])
    assert message.tool_calls == []
    assert "抱歉" in str(message.content)
    assert "库存" in str(message.content)


async def test_streaming_chunks_concatenate_to_final_text() -> None:
    model = MockChatModel()
    texts: list[str] = []
    async for chunk in model.astream([HumanMessage(content="你好")]):
        if chunk.content:
            texts.append(str(chunk.content))
    assert len(texts) > 1  # 流式分片（用于验证 SSE token 增量事件）
    joined = "".join(texts)
    final = await model.ainvoke([HumanMessage(content="你好")])
    assert joined == final.content


async def test_bind_tools_keeps_model_usable() -> None:
    bound = MockChatModel().bind_tools(TOOL_SCHEMAS)
    message = await bound.ainvoke([HumanMessage(content="帮我找三体")])
    assert message.tool_calls[0]["name"] == "search_books"
