"""工具执行器：把模型给出的 tool_call 分发到 Java 内部接口，并产出结构化结果。

执行器只负责“调用 + 翻译成人话”，不负责 SSE 事件推送（事件由 agent 节点统一推），
这样工具逻辑可以被单独测试，也方便未来替换成 HTTP 之外的实现。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.obs.logging import get_logger
from app.tools.java_client import (
    CODE_UPSTREAM_UNAUTHENTICATED,
    JavaClient,
    JavaClientError,
    UpstreamUnauthenticated,
)
from app.tools.schemas import is_known_tool, is_write_tool

logger = get_logger("tools.executor")

CODE_TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
CODE_TOOL_INVALID_ARGS = "TOOL_INVALID_ARGS"
CODE_TOOL_UPSTREAM_ERROR = "TOOL_UPSTREAM_ERROR"


@dataclass
class ToolResult:
    """一次工具调用的结果；会被翻译成 SSE tool_result 事件与 ToolMessage 回灌模型。"""

    call_id: str
    name: str
    ok: bool
    summary: str
    data: Any = None
    error_code: str | None = None
    duration_ms: int = 0
    is_write: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def fatal(self) -> bool:
        """是否需要中断整个对话（用户 token 失效）。"""
        return self.error_code == CODE_UPSTREAM_UNAUTHENTICATED

    def to_tool_message_payload(self) -> dict[str, Any]:
        """回灌给模型的 JSON 内容（模型据此决定下一步或直接回答）。"""
        payload: dict[str, Any] = {"ok": self.ok, "summary": self.summary}
        if self.data is not None:
            payload["data"] = self.data
        if self.error_code:
            payload["errorCode"] = self.error_code
        return payload


def _as_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数 {field_name} 必须是整数，收到：{value!r}") from exc


class ToolExecutor:
    """按工具名分发调用；所有失败都转换成 ToolResult，不向上抛异常。"""

    def __init__(self, java_client: JavaClient, *, trace_id: str, session_id: str) -> None:
        self._java = java_client
        self._trace_id = trace_id
        self._session_id = session_id

    async def execute(self, tool_call: dict[str, Any]) -> ToolResult:
        call_id = str(tool_call.get("id") or "")
        name = str(tool_call.get("name") or "")
        raw_args = tool_call.get("args")
        args: dict[str, Any] = dict(raw_args) if isinstance(raw_args, dict) else {}
        is_write = is_write_tool(name)

        if not is_known_tool(name):
            return ToolResult(
                call_id=call_id,
                name=name,
                ok=False,
                summary=f"不存在的工具「{name}」，可用工具：search_books / get_book_detail / get_my_borrows / borrow_book / return_book / reserve_book。",
                error_code=CODE_TOOL_NOT_FOUND,
                is_write=is_write,
            )

        started = time.perf_counter()
        try:
            summary, data = await self._dispatch(name, args)
            result = ToolResult(
                call_id=call_id,
                name=name,
                ok=True,
                summary=summary,
                data=data,
                is_write=is_write,
            )
        except UpstreamUnauthenticated as exc:
            result = ToolResult(
                call_id=call_id,
                name=name,
                ok=False,
                summary=exc.message,
                error_code=exc.code,
                is_write=is_write,
            )
        except JavaClientError as exc:
            result = ToolResult(
                call_id=call_id,
                name=name,
                ok=False,
                summary=exc.message,
                error_code=CODE_TOOL_UPSTREAM_ERROR,
                is_write=is_write,
            )
        except ValueError as exc:
            result = ToolResult(
                call_id=call_id,
                name=name,
                ok=False,
                summary=f"工具参数不合法：{exc}",
                error_code=CODE_TOOL_INVALID_ARGS,
                is_write=is_write,
            )
        except Exception as exc:  # pragma: no cover - 防御式兜底
            logger.exception(
                "工具执行出现未预期异常",
                extra={"traceId": self._trace_id, "sessionId": self._session_id, "tool": name},
            )
            result = ToolResult(
                call_id=call_id,
                name=name,
                ok=False,
                summary=f"工具执行失败：{type(exc).__name__}",
                error_code=CODE_TOOL_UPSTREAM_ERROR,
                is_write=is_write,
            )

        result.duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "工具调用结束",
            extra={
                "traceId": self._trace_id,
                "sessionId": self._session_id,
                "tool": name,
                "durationMs": result.duration_ms,
                "code": "OK" if result.ok else (result.error_code or "ERROR"),
            },
        )
        return result

    # ------------------------------------------------------------------
    async def _dispatch(self, name: str, args: dict[str, Any]) -> tuple[str, Any]:
        """返回 (中文摘要, 结构化数据)。"""
        if name == "search_books":
            keyword = args.get("keyword") or None
            category = args.get("category") or None
            size = _as_int(args.get("size", 5), "size")
            payload = await self._java.search_books(
                keyword=str(keyword) if keyword else None,
                category=str(category) if category else None,
                size=size,
            )
            records = list((payload or {}).get("records") or [])
            total = int((payload or {}).get("total") or len(records))
            available = sum(int(item.get("availableStock") or 0) for item in records)
            return f"找到 {total} 本图书，当前可借 {available} 本", records

        if name == "get_book_detail":
            book_id = _as_int(args.get("bookId"), "bookId")
            payload = await self._java.get_book_detail(book_id) or {}
            book = payload.get("book") or {}
            waiting = payload.get("waitingCount") or 0
            summary = (
                f"《{book.get('title', '未知图书')}》可借 {book.get('availableStock', 0)} 本，"
                f"共 {book.get('totalStock', 0)} 本，{waiting} 人排队"
            )
            return summary, payload

        if name == "get_my_borrows":
            records = await self._java.get_my_borrows() or []
            overdue = sum(1 for item in records if item.get("overdue"))
            return f"共 {len(records)} 条在借记录，其中逾期 {overdue} 条", records

        if name == "borrow_book":
            book_id = _as_int(args.get("bookId"), "bookId")
            record = await self._java.borrow_book(book_id) or {}
            due_at = record.get("dueAt")
            summary = "借阅成功" + (f"，应还日期 {due_at}" if due_at else "")
            return summary, record

        if name == "return_book":
            borrow_id = _as_int(args.get("borrowId"), "borrowId")
            record = await self._java.return_book(borrow_id) or {}
            fine = record.get("fineAmount")
            summary = "归还成功" + (f"，产生罚金 {fine} 元" if fine else "")
            return summary, record

        if name == "reserve_book":
            book_id = _as_int(args.get("bookId"), "bookId")
            reservation = await self._java.reserve_book(book_id) or {}
            position = reservation.get("queuePosition") or reservation.get("waitingCount")
            summary = "预约成功" + (f"，当前排队第 {position} 位" if position else "")
            return summary, reservation

        # is_known_tool 已过滤，正常不会到这里
        raise ValueError(f"未实现的工具：{name}")
