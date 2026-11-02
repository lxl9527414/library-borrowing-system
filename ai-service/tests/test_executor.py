"""工具执行器：结果摘要、错误码、致命错误标记。"""

from __future__ import annotations

from app.tools.executor import CODE_TOOL_INVALID_ARGS, CODE_TOOL_NOT_FOUND
from tests.conftest import build_executor
from tests.fakes import JavaBackendMock


def _call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "name": name, "args": args}


async def test_search_books_summary_and_data(settings, backend: JavaBackendMock) -> None:
    executor = build_executor(settings, backend)
    result = await executor.execute(_call("search_books", {"keyword": "三体"}))
    assert result.ok is True
    assert "找到 1 本图书" in result.summary
    assert result.data[0]["title"] == "三体"
    assert result.is_write is False
    assert result.duration_ms >= 0


async def test_get_book_detail_tolerates_missing_my_reservation(settings, backend: JavaBackendMock) -> None:
    backend.my_reservation = None
    executor = build_executor(settings, backend)
    result = await executor.execute(_call("get_book_detail", {"bookId": 1}))
    assert result.ok is True
    assert "三体" in result.summary
    assert result.data["book"]["availableStock"] == 0


async def test_get_my_borrows_only_reports_overdue_days(
    settings, backend: JavaBackendMock
) -> None:
    executor = build_executor(settings, backend)
    result = await executor.execute(_call("get_my_borrows", {}))
    assert result.ok is True
    assert "逾期 1 条" in result.summary
    # 现网罚金恒为 0，摘要里不应出现罚金金额
    assert "罚金" not in result.summary


async def test_borrow_success(settings, backend: JavaBackendMock) -> None:
    executor = build_executor(settings, backend)
    # 活着可借 2 本
    result = await executor.execute(_call("borrow_book", {"bookId": 2}))
    assert result.ok is True
    assert result.is_write is True
    assert "借阅成功" in result.summary
    assert "应还日期" in result.summary


async def test_borrow_business_failure_is_fed_back(settings, backend: JavaBackendMock) -> None:
    executor = build_executor(settings, backend)
    result = await executor.execute(_call("borrow_book", {"bookId": 1}))
    assert result.ok is False
    assert result.error_code == "TOOL_UPSTREAM_ERROR"
    assert "库存" in result.summary


async def test_reserve_success(settings, backend: JavaBackendMock) -> None:
    executor = build_executor(settings, backend)
    result = await executor.execute(_call("reserve_book", {"bookId": 1}))
    assert result.ok is True
    assert "预约成功" in result.summary


async def test_reserve_rejected_by_business_rule(settings, backend: JavaBackendMock) -> None:
    executor = build_executor(settings, backend)
    result = await executor.execute(_call("reserve_book", {"bookId": 2}))
    assert result.ok is False
    assert "可借库存" in result.summary


async def test_return_success(settings, backend: JavaBackendMock) -> None:
    executor = build_executor(settings, backend)
    result = await executor.execute(_call("return_book", {"borrowId": 10}))
    assert result.ok is True
    assert "归还成功" in result.summary


async def test_unknown_tool_returns_tool_not_found(settings, backend: JavaBackendMock) -> None:
    executor = build_executor(settings, backend)
    result = await executor.execute(_call("delete_all_books", {}))
    assert result.ok is False
    assert result.error_code == CODE_TOOL_NOT_FOUND
    assert "不存在的工具" in result.summary


async def test_invalid_args_returns_invalid_args(settings, backend: JavaBackendMock) -> None:
    executor = build_executor(settings, backend)
    result = await executor.execute(_call("borrow_book", {"bookId": "不是数字"}))
    assert result.ok is False
    assert result.error_code == CODE_TOOL_INVALID_ARGS


async def test_unauthorized_is_fatal(settings, backend: JavaBackendMock) -> None:
    backend.unauthorized = True
    executor = build_executor(settings, backend)
    result = await executor.execute(_call("search_books", {"keyword": "三体"}))
    assert result.ok is False
    assert result.error_code == "UPSTREAM_UNAUTHENTICATED"
    assert result.fatal is True


async def test_backend_down_is_not_fatal(settings, backend: JavaBackendMock) -> None:
    backend.connection_error = True
    executor = build_executor(settings, backend)
    result = await executor.execute(_call("search_books", {"keyword": "三体"}))
    assert result.ok is False
    assert result.error_code == "TOOL_UPSTREAM_ERROR"
    assert result.fatal is False
    assert "无法连接" in result.summary


async def test_tool_message_payload_is_json_serializable(settings, backend: JavaBackendMock) -> None:
    import json

    executor = build_executor(settings, backend)
    result = await executor.execute(_call("search_books", {"keyword": "三体"}))
    payload = json.loads(json.dumps(result.to_tool_message_payload(), ensure_ascii=False))
    assert payload["ok"] is True
    assert payload["data"][0]["id"] == 1
