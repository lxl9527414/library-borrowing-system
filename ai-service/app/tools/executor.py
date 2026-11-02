"""工具执行器：把模型给出的 tool_call 分发到 Java 内部接口 / 知识检索，并产出结构化结果。

执行器只负责“调用 + 翻译成人话”，不负责 SSE 事件推送（事件由 agent 节点统一推），
这样工具逻辑可以被单独测试，也方便未来替换成 HTTP 之外的实现。

``search_knowledge`` 是第 7 个工具（只读）：走本地 RAG 检索层（不经过 Java），
把 ``RagService.search()`` 的结果映射成前端契约：

    {"ok": true, "summary": "...", "data": {"chunks": [
        {"citation_no": 1, "chunk_id": "...", "source_type": "rule", "title": "...",
         "text": "...", "book_id": null, "url": null, "score": 0.83}
    ]}}

约束（契约）：citation_no 从 1 递增、text 截断 500 字、按 score 降序；
**RAG 不可用 / 抛异常 / 空结果都不向调用方抛异常**，而是 ``ok:true + chunks: []`` +
说明性 summary，把“要不要拒答”交给模型（提示词里已约束）。

关于「弱相关阈值」：向量检索（cosine）永远会返回 top-k 个最近邻，无关问题也会拿到
0.4~0.66 的相似度（见 eval 报告：10 条拒答负样本 top1 ∈ [0.41, 0.67]）。为了让
"书库里根本没有依据"的问题能返回空 chunks（拒答），这里按实际检索模式做一次弱相关过滤：

- ``vector`` 模式：默认阈值 0.67（cosine）；负样本最高 0.667，有效问题多数 ≥ 0.7；
- ``bm25``（原始分 0~100）/ ``hybrid``（RRF 分 ~0.03）量纲完全不同，默认**不设阈值**，
  以免把有效结果全部过滤掉（这两种模式建议配合重排/模型判断拒答）；
- 可用 ``RAG_TOOL_MIN_SCORE`` 显式覆盖（对所有模式生效，设为 0 即关闭过滤）。
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.obs.logging import get_logger
from app.tools.java_client import (
    CODE_UPSTREAM_UNAUTHENTICATED,
    JavaClient,
    JavaClientError,
    UpstreamUnauthenticated,
)
from app.tools.schemas import KNOWLEDGE_SOURCE_TYPES, is_known_tool, is_write_tool

logger = get_logger("tools.executor")

CODE_TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
CODE_TOOL_INVALID_ARGS = "TOOL_INVALID_ARGS"
CODE_TOOL_UPSTREAM_ERROR = "TOOL_UPSTREAM_ERROR"

# search_knowledge 的输出契约
DEFAULT_KNOWLEDGE_TOP_K = 5
MAX_KNOWLEDGE_TOP_K = 10
MAX_CHUNK_CHARS = 500
EMPTY_KNOWLEDGE_SUMMARY = "没有检索到相关依据"
RAG_UNAVAILABLE_SUMMARY = "知识库检索暂不可用，暂时无法提供馆藏依据，请稍后再试。"

# 各检索模式的默认弱相关阈值（见模块 docstring；“None 模式”= 不做过滤）
RAG_TOOL_MIN_SCORE_ENV = "RAG_TOOL_MIN_SCORE"
DEFAULT_MIN_SCORE_BY_MODE: dict[str, float] = {
    "vector": 0.67,
    "hybrid": 0.0,
    "bm25": 0.0,
}


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


def _as_score(value: Any) -> float:
    """把检索分数转成可排序的有限 float（脏数据降级为 0.0，避免 NaN 破坏排序）。"""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return score if math.isfinite(score) else 0.0


def _normalize_source_types(value: Any) -> list[str] | None:
    """校验 ``source_types`` 参数：None/空数组 → 不过滤；未知来源 → 参数非法。"""
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError('参数 source_types 必须是数组，例如 ["rule"] 或 ["book","faq"]')
    normalized: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        if text not in KNOWLEDGE_SOURCE_TYPES:
            raise ValueError(
                f"参数 source_types 含未知来源 {text!r}，可选：{'/'.join(KNOWLEDGE_SOURCE_TYPES)}"
            )
        if text not in normalized:
            normalized.append(text)
    return normalized or None


def _knowledge_min_score(mode: str | None) -> float:
    """解析 search_knowledge 的弱相关阈值。

    优先级：``RAG_TOOL_MIN_SCORE`` 显式配置 > 检索模式默认值 > 0（不过滤）。
    配置读取失败时退回默认值，绝不因为配置问题阻断检索。
    """
    default = DEFAULT_MIN_SCORE_BY_MODE.get(mode or "", 0.0)
    try:
        from app.rag.rag_config import rag_env

        raw = rag_env(RAG_TOOL_MIN_SCORE_ENV)
    except Exception:  # noqa: BLE001 - 配置模块不可用时不影响检索
        raw = None
    if raw is None:
        return default
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return default


class ToolExecutor:
    """按工具名分发调用；所有失败都转换成 ToolResult，不向上抛异常。"""

    def __init__(
        self,
        java_client: JavaClient,
        *,
        trace_id: str,
        session_id: str,
        rag_service: Any | None = None,
    ) -> None:
        self._java = java_client
        self._trace_id = trace_id
        self._session_id = session_id
        # 测试注入 / 未来替换检索实现用；None 表示首次调用 search_knowledge 时懒加载单例
        self._rag_service = rag_service

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
                summary=f"不存在的工具「{name}」，可用工具：search_books / get_book_detail / get_my_borrows / search_knowledge / borrow_book / return_book / reserve_book。",
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

        if name == "search_knowledge":
            return await self._search_knowledge(args)

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

    # ------------------------------------------------------------------
    # search_knowledge：本地 RAG 检索（第 7 个工具，只读）
    # ------------------------------------------------------------------
    async def _search_knowledge(self, args: dict[str, Any]) -> tuple[str, Any]:
        """检索馆藏知识库并映射为前端契约格式。

        参数非法（query 缺失 / top_k 越界 / source_types 未知）→ ValueError，
        由 ``execute()`` 统一转成 ``TOOL_INVALID_ARGS``（模型可自我纠正）；
        RAG 不可用 / 抛异常 / 空结果 → **不抛异常**，返回空 chunks + 说明性 summary。
        """
        query = args.get("query")
        query_text = str(query).strip() if query is not None else ""
        if not query_text:
            raise ValueError("参数 query 不能为空：请传入要检索的问题或关键词")

        top_k = _as_int(args.get("top_k", DEFAULT_KNOWLEDGE_TOP_K), "top_k")
        if not 1 <= top_k <= MAX_KNOWLEDGE_TOP_K:
            raise ValueError(f"参数 top_k 必须在 1~{MAX_KNOWLEDGE_TOP_K} 之间，收到：{top_k}")
        source_types = _normalize_source_types(args.get("source_types"))

        try:
            service = self._knowledge_service()
            # 检索里有一次 embedding 网络调用（vector/hybrid 模式），放到线程池避免阻塞事件循环
            hits = await asyncio.to_thread(
                service.search,
                query_text,
                top_k=top_k,
                source_types=source_types,
            )
            hits = list(hits or [])
            # 弱相关过滤：向量检索总会返回 top-k 最近邻，低于阈值的片段视为“无依据”
            min_score = _knowledge_min_score(self._retrieval_mode(service))
            if min_score > 0:
                hits = [
                    hit
                    for hit in hits
                    if isinstance(hit, dict) and _as_score(hit.get("score")) >= min_score
                ]
        except Exception as exc:  # noqa: BLE001 - 检索失败不能打断对话
            logger.warning(
                f"知识库检索失败，已降级为空结果：{type(exc).__name__}: {exc}",
                extra={
                    "traceId": self._trace_id,
                    "sessionId": self._session_id,
                    "tool": "search_knowledge",
                    "code": "RAG_SEARCH_TOOL_FAILED",
                },
            )
            return RAG_UNAVAILABLE_SUMMARY, {"chunks": []}

        chunks = self._format_knowledge_chunks(hits or [], top_k=top_k)
        if not chunks:
            return EMPTY_KNOWLEDGE_SUMMARY, {"chunks": []}

        counts = Counter(str(chunk.get("source_type") or "") for chunk in chunks)
        summary = (
            f"检索到 {len(chunks)} 段馆藏依据"
            f"（规则 {counts.get('rule', 0)} / FAQ {counts.get('faq', 0)} / 馆藏 {counts.get('book', 0)}）"
        )
        return summary, {"chunks": chunks}

    @staticmethod
    def _retrieval_mode(service: Any) -> str | None:
        """读取检索层实际生效的模式（用于选择阈值量纲）；读取失败返回 None。"""
        try:
            mode = getattr(service, "retrieval_mode", None)
        except Exception:  # noqa: BLE001 - 模式读取失败按“未知模式”处理
            return None
        return str(mode).strip().lower() if mode else None

    def _knowledge_service(self) -> Any:
        """懒加载检索服务单例；注入优先（测试 / 替换实现）。"""
        if self._rag_service is not None:
            return self._rag_service
        # 延迟导入：RAG 依赖（numpy/httpx）不应拖慢或阻断整个 AI 服务启动
        from app.rag.service import get_rag_service

        return get_rag_service()

    @staticmethod
    def _format_knowledge_chunks(hits: list[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
        """把检索结果映射为契约字段：按 score 降序、citation_no 从 1 递增、text 截断 500 字。"""
        ordered = sorted(
            (hit for hit in hits if isinstance(hit, dict)),
            key=lambda hit: -_as_score(hit.get("score")),
        )
        chunks: list[dict[str, Any]] = []
        seen: set[str] = set()
        for hit in ordered:
            if len(chunks) >= top_k:
                break
            if not isinstance(hit, dict):  # 防御脏数据：非 dict 项直接跳过
                continue
            chunk_id = str(hit.get("chunk_id") or "").strip()
            if not chunk_id or chunk_id in seen:  # 防御式去重（正常检索层已去重）
                continue
            seen.add(chunk_id)
            chunks.append(
                {
                    "citation_no": len(chunks) + 1,
                    "chunk_id": chunk_id,
                    "source_type": str(hit.get("source_type") or ""),
                    "title": str(hit.get("title") or ""),
                    "text": str(hit.get("text") or "")[:MAX_CHUNK_CHARS],
                    "book_id": hit.get("book_id"),
                    "url": hit.get("url"),
                    "score": round(_as_score(hit.get("score")), 6),
                }
            )
        return chunks
