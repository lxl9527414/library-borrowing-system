"""测试替身：Java 内部接口的 MockTransport 后端 + 可编排的假模型网关。

原则：
- 不依赖真实网络（httpx.MockTransport）；
- 不依赖真实模型（mock provider 或 ScriptedGateway）；
- 断言尽量落在协议/事件序列上，避免与文案强耦合。
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain_core.messages import AIMessage

from app.llm.base import LLMError
from app.llm.gateway import LLMResult

DEFAULT_CONTEXT: dict[str, Any] = {
    "user": {"id": 2, "username": "reader", "realName": "张小明", "role": "READER"},
    "rule": {
        "maxBorrowCount": 5,
        "borrowDays": 30,
        "maxRenewCount": 1,
        "renewDays": 15,
        "finePerDay": 0.20,
        "fineMax": 20.00,
        "reserveLimit": 3,
        "holdDays": 3,
    },
    "borrowedCount": 2,
    "overdueCount": 1,
    "activeReservationCount": 1,
    "pendingPickupCount": 0,
}

DEFAULT_BOOKS: list[dict[str, Any]] = [
    {
        "id": 1,
        "isbn": "9787536692930",
        "title": "三体",
        "author": "刘慈欣",
        "publisher": "重庆出版社",
        "category": "科幻",
        "price": 23.0,
        "totalStock": 2,
        "availableStock": 0,
        "description": "地球往事三部曲第一部",
    },
    {
        "id": 2,
        "isbn": "9787506365437",
        "title": "活着",
        "author": "余华",
        "publisher": "作家出版社",
        "category": "文学",
        "price": 20.0,
        "totalStock": 3,
        "availableStock": 2,
        "description": "余华代表作",
    },
]

DEFAULT_BORROWS: list[dict[str, Any]] = [
    {
        "id": 10,
        "bookId": 1,
        "bookTitle": "三体",
        "bookIsbn": "9787536692930",
        "status": "BORROWED",
        "borrowedAt": "2026-10-01 10:00:00",
        "dueAt": "2026-10-31 10:00:00",
        # 现网实际行为：罚金恒定 0.0、fineStatus=NONE，只有 overdue 有意义
        "overdue": True,
        "overdueDays": 1,
        "renewCount": 0,
        "fineAmount": 0.0,
        "fineStatus": "NONE",
    }
]


def json_response(body: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=body)


def api_ok(data: Any) -> httpx.Response:
    return json_response({"code": 200, "message": "操作成功", "data": data})


def api_fail(code: int, message: str) -> httpx.Response:
    return json_response({"code": code, "message": message, "data": None})


class JavaBackendMock:
    """模拟 Java 侧 /api/ai/internal/** 的真实行为（含现网实测的 3 处差异）。"""

    def __init__(self) -> None:
        self.context: dict[str, Any] = copy.deepcopy(DEFAULT_CONTEXT)
        self.rules_text = "每人最多同时借阅 5 本，借期 30 天，最多续借 1 次（每次 15 天）。"
        self.books: list[dict[str, Any]] = copy.deepcopy(DEFAULT_BOOKS)
        self.borrows: list[dict[str, Any]] = copy.deepcopy(DEFAULT_BORROWS)
        # 现网行为：myReservation 为 null 时字段被 Jackson 直接省略
        self.my_reservation: dict[str, Any] | None = None
        self.waiting_count = 2

        self.requests: list[httpx.Request] = []
        self.write_calls: list[dict[str, Any]] = []
        self.unauthorized = False
        self.connection_error = False
        self.internal_server_error = False
        self.read_timeout = False

    # ------------------------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.connection_error:
            raise httpx.ConnectError("模拟：Java 服务不可用", request=request)
        if self.read_timeout:
            raise httpx.ReadTimeout("模拟：Java 接口响应超时", request=request)
        if self.unauthorized:
            return json_response({"code": 401, "message": "未登录或令牌已过期", "data": None}, 401)
        if self.internal_server_error:
            return json_response({"code": 500, "message": "系统异常", "data": None}, 500)

        path = request.url.path
        method = request.method
        params = dict(request.url.params)

        if path == "/api/ai/internal/context":
            return api_ok(self.context)
        if path == "/api/ai/internal/rules/text":
            return api_ok({"text": self.rules_text})
        if path == "/api/ai/internal/books" and method == "GET":
            return self._search(params)
        if path.startswith("/api/ai/internal/books/") and method == "GET":
            return self._detail(int(path.rsplit("/", 1)[-1]))
        if path == "/api/ai/internal/borrows/my" and method == "GET":
            return api_ok(self.borrows)
        if path == "/api/ai/internal/borrow" and method == "POST":
            return self._borrow(self._json_body(request))
        if path == "/api/ai/internal/return" and method == "POST":
            return self._return(self._json_body(request))
        if path == "/api/ai/internal/reserve" and method == "POST":
            return self._reserve(self._json_body(request))
        return json_response({"code": 404, "message": f"未知接口 {method} {path}", "data": None}, 404)

    # ------------------------------------------------------------------
    @staticmethod
    def _json_body(request: httpx.Request) -> dict[str, Any]:
        try:
            body = json.loads(request.content.decode("utf-8") or "{}")
            return body if isinstance(body, dict) else {}
        except ValueError:
            return {}

    def _find_book(self, book_id: int) -> dict[str, Any] | None:
        return next((book for book in self.books if int(book["id"]) == int(book_id)), None)

    def _search(self, params: dict[str, str]) -> httpx.Response:
        keyword = (params.get("keyword") or "").strip()
        size = min(int(params.get("size") or 5), 20)
        records = self.books
        if keyword:
            records = [
                book
                for book in self.books
                if keyword in str(book.get("title", ""))
                or keyword in str(book.get("author", ""))
                or keyword in str(book.get("isbn", ""))
            ]
        data = {
            "records": records[:size],
            "total": len(records),
            "current": int(params.get("current") or 1),
            "size": size,
        }
        return api_ok(data)

    def _detail(self, book_id: int) -> httpx.Response:
        book = self._find_book(book_id)
        if book is None:
            return api_fail(404, "图书不存在")
        data: dict[str, Any] = {"book": book, "waitingCount": self.waiting_count}
        # 关键差异 1：无预约时不返回 myReservation 字段（而不是 null）
        if self.my_reservation is not None:
            data["myReservation"] = self.my_reservation
        return api_ok(data)

    def _borrow(self, body: dict[str, Any]) -> httpx.Response:
        book = self._find_book(int(body.get("bookId") or 0))
        if book is None:
            return api_fail(404, "图书不存在")
        if int(book.get("availableStock") or 0) <= 0:
            return api_fail(400, "该图书暂无可借库存，可预约排队")
        book["availableStock"] = int(book["availableStock"]) - 1
        record = {
            "id": 100 + len(self.write_calls),
            "bookId": book["id"],
            "bookTitle": book["title"],
            "status": "BORROWED",
            "borrowedAt": "2026-11-01 10:00:00",
            "dueAt": "2026-12-01 10:00:00",
            "overdue": False,
            "overdueDays": 0,
            "renewCount": 0,
            "fineAmount": 0.0,
            "fineStatus": "NONE",
        }
        self.write_calls.append({"tool": "borrow_book", "body": body})
        return api_ok(record)

    def _return(self, body: dict[str, Any]) -> httpx.Response:
        borrow_id = int(body.get("borrowId") or 0)
        record = next((item for item in self.borrows if int(item["id"]) == borrow_id), None)
        if record is None:
            return api_fail(404, "借阅记录不存在")
        record["status"] = "RETURNED"
        self.write_calls.append({"tool": "return_book", "body": body})
        return api_ok(record)

    def _reserve(self, body: dict[str, Any]) -> httpx.Response:
        book = self._find_book(int(body.get("bookId") or 0))
        if book is None:
            return api_fail(404, "图书不存在")
        if int(book.get("availableStock") or 0) > 0:
            # 现网实测：有库存时预约返回业务 400
            return api_fail(400, "该图书当前有可借库存，可直接借阅")
        self.write_calls.append({"tool": "reserve_book", "body": body})
        return api_ok(
            {
                "id": 9,
                "bookId": book["id"],
                "status": "WAITING",
                "queuePosition": 3,
                "expireAt": "2026-11-03 12:00:00",
            }
        )


def _default_message() -> AIMessage:
    return AIMessage(content="好的")


@dataclass
class ScriptedGateway:
    """可编排的假网关：按顺序返回预设结果，用于测试错误码与多写工具合并确认。"""

    script: list[Any] = field(default_factory=list)
    calls: list[list[Any]] = field(default_factory=list)
    _index: int = 0

    async def astream_chat(
        self,
        messages: list[Any],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_token: Any = None,
    ) -> LLMResult:
        self.calls.append(messages)
        if not self.script:
            item: Any = _default_message()
        else:
            item = self.script[min(self._index, len(self.script) - 1)]
            self._index += 1

        if isinstance(item, BaseException):
            raise item
        if isinstance(item, LLMResult):
            result = item
        else:
            message = item if isinstance(item, AIMessage) else _default_message()
            # 每次返回新的消息 id：否则 add_messages 会把重复的 AIMessage 去重
            message = message.model_copy(update={"id": uuid.uuid4().hex})
            if on_token is not None and message.content:
                on_token(str(message.content))
            result = LLMResult(
                message=message,
                provider="scripted",
                model="scripted",
                prompt_tokens=1,
                completion_tokens=1,
            )
        return result


def tool_call_message(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def llm_timeout_error() -> LLMError:
    from app.llm.base import LLMErrorCode

    return LLMError(LLMErrorCode.LLM_TIMEOUT, "模型响应超时，已尝试切换备用模型。", provider="mock")
