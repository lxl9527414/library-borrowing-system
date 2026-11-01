"""Mock 模型：无需任何 API Key，用确定性规则驱动完整 Agent 链路。

为什么需要它：
- 本机/CI 没有模型 Key，但 W1 要验证的不是“模型有多聪明”，而是
  「SSE 协议 + 工具调用 + 写操作确认 + 错误处理」是否正确；
- mock 模型把用户意图映射为固定 tool_call / 固定文案，让 pytest 可以断言事件序列，
  不会因为模型随机性导致测试 flaky。

实现要点：继承 LangChain `BaseChatModel`，实现 `_stream`（流式，供 astream 使用）
与 `_generate`（非流式，供 ainvoke 使用）两个入口，行为完全一致。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from app.llm.base import BaseLLMProvider

# 演示用书名 -> bookId 映射，保证 mock 模式下的工具参数是确定值
TITLE_TO_BOOK_ID: dict[str, int] = {
    "三体": 1,
    "活着": 2,
    "百年孤独": 3,
    "小王子": 4,
    "人类简史": 5,
}

SEARCH_KEYS = ("找", "查", "搜", "检索", "馆藏", "有哪些", "有没有", "推荐")
MY_BORROWS_KEYS = ("我的借阅", "在借", "借了什么", "还没还", "逾期", "罚金", "该还", "到期")
RETURN_KEYS = ("还书", "归还", "返还")
RESERVE_KEYS = ("预约", "排队")
BORROW_KEYS = ("借书", "借阅", "我要借", "帮我借", "借一本", "借一下", "想借")
DETAIL_KEYS = ("详情", "库存", "还有几本", "有货吗", "可借")

# 抽取检索关键词时需要剔除的口语噪声
_NOISE_WORDS = (
    # 动词短语优先（长的在前，_extract_keyword 按长度倒序剔除），否则「帮我借红楼梦」
    # 会被抽成「借红楼梦」导致检索落空
    "帮我借", "我要借", "想借", "借一本", "借一下", "借书", "借阅", "还书", "归还", "返还",
    "帮我", "请问", "帮我查", "帮我找", "我想", "我要", "能不能", "可以", "一下",
    "这本书", "那本书", "有没有", "有哪些", "搜一下", "找一下", "查一下", "借阅",
    "预约", "排队", "借书", "还书", "归还", "详情", "库存", "可借", "吗", "呢",
    "的", "了", "请", "我", "能", "帮", "找", "查", "搜", "检索", "推荐", "看看",
    "如果", "没货", "就",
)

_MOCK_USAGE = {"input_tokens": 24, "output_tokens": 12, "total_tokens": 36}


def _message_text(message: BaseMessage) -> str:
    """取出消息的纯文本（兼容 str 和内容块两种格式）。"""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def _contains_any(text: str, keys: tuple[str, ...]) -> bool:
    return any(key in text for key in keys)


def _extract_keyword(text: str) -> str:
    """从口语里抽取书名/作者等检索词；抽不到就返回空串。"""
    for title in TITLE_TO_BOOK_ID:
        if title in text:
            return title
    cleaned = text
    for word in sorted(_NOISE_WORDS, key=len, reverse=True):
        cleaned = cleaned.replace(word, " ")
    cleaned = re.sub(r"[\s，。,.!！?？、：:；;\"'“”‘’《》]+", " ", cleaned).strip()
    return cleaned.split(" ")[0] if cleaned else ""


def _extract_book_id(text: str, fallback: int = 1) -> int:
    digits = re.search(r"\d+", text)
    if digits:
        return int(digits.group())
    for title, book_id in TITLE_TO_BOOK_ID.items():
        if title in text:
            return book_id
    return fallback


def _tool_chunk(name: str, args: dict[str, Any], call_id: str) -> ChatGenerationChunk:
    """构造“模型决定调用工具”的流式 chunk（args 需为 JSON 字符串）。"""
    return ChatGenerationChunk(
        message=AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": name,
                    "args": json.dumps(args, ensure_ascii=False),
                    "id": call_id,
                    "index": 0,
                }
            ],
            usage_metadata=_MOCK_USAGE,
        )
    )


def _text_chunks(text: str, size: int = 12) -> Iterator[ChatGenerationChunk]:
    """把最终文案切成固定大小的片段，模拟 token 增量输出（保证测试可预期）。"""
    chunks = [text[i : i + size] for i in range(0, len(text), size)] or [""]
    for index, piece in enumerate(chunks):
        is_last = index == len(chunks) - 1
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=piece,
                usage_metadata=_MOCK_USAGE if is_last else None,
            )
        )


def _json_payload(message: ToolMessage) -> dict[str, Any]:
    """工具结果统一以 JSON 字符串回灌，这里解析失败时降级为空 dict。"""
    try:
        payload = json.loads(_message_text(message))
        return payload if isinstance(payload, dict) else {}
    except (ValueError, TypeError):
        return {}


def _records_from(data: Any) -> list[dict[str, Any]]:
    """把工具返回的 data 统一成记录列表（search 返回 list，detail 返回 dict）。"""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return [item for item in data["records"] if isinstance(item, dict)]
    return []


class MockChatModel(BaseChatModel):
    """确定性 mock 对话模型（离线开发 / CI 用）。"""

    model_name: str = "mock-chat"

    @property
    def _llm_type(self) -> str:
        return "mock"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    # ------------------------------------------------------------------
    # 决策逻辑
    # ------------------------------------------------------------------
    def _plan(self, messages: list[BaseMessage]) -> list[ChatGenerationChunk]:
        human_index = -1
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], HumanMessage):
                human_index = index
                break
        user_text = _message_text(messages[human_index]) if human_index >= 0 else ""
        tool_messages = [m for m in messages[human_index + 1 :] if isinstance(m, ToolMessage)]

        if not tool_messages:
            return self._plan_first_turn(user_text)
        return self._plan_after_tools(user_text, tool_messages)

    def _plan_first_turn(self, user_text: str) -> list[ChatGenerationChunk]:
        """首轮：把意图映射成一次工具调用；无意图则直接闲聊。

        写操作意图会先调只读工具补齐参数（书名 -> bookId、借阅记录 -> borrowId），
        这样确认卡片能显示真实书名，也更接近真实 Agent 的推理路径。
        """
        has_explicit_id = bool(re.search(r"\d+", user_text))

        # 「帮我找 X，如果没货就预约」：先查库存，下一轮再决定是否预约
        if _contains_any(user_text, RESERVE_KEYS) and _contains_any(user_text, SEARCH_KEYS):
            keyword = _extract_keyword(user_text)
            return [_tool_chunk("search_books", {"keyword": keyword}, "call_mock_1")]
        if _contains_any(user_text, MY_BORROWS_KEYS):
            return [_tool_chunk("get_my_borrows", {}, "call_mock_1")]
        if _contains_any(user_text, RETURN_KEYS):
            if has_explicit_id:
                return [_tool_chunk("return_book", {"borrowId": _extract_book_id(user_text)}, "call_mock_1")]
            # 没有借阅记录 ID 时先查“我的在借”，再归还第一本
            return [_tool_chunk("get_my_borrows", {}, "call_mock_1")]
        if _contains_any(user_text, RESERVE_KEYS) and has_explicit_id:
            return [_tool_chunk("reserve_book", {"bookId": _extract_book_id(user_text)}, "call_mock_1")]
        if _contains_any(user_text, BORROW_KEYS) and has_explicit_id:
            return [_tool_chunk("borrow_book", {"bookId": _extract_book_id(user_text)}, "call_mock_1")]
        if _contains_any(user_text, RESERVE_KEYS) or _contains_any(user_text, BORROW_KEYS):
            # 先检索，拿到真实 bookId 与库存后再决定借/预约
            return [_tool_chunk("search_books", {"keyword": _extract_keyword(user_text)}, "call_mock_1")]
        if _contains_any(user_text, DETAIL_KEYS):
            return [_tool_chunk("get_book_detail", {"bookId": _extract_book_id(user_text)}, "call_mock_1")]
        if _contains_any(user_text, SEARCH_KEYS):
            keyword = _extract_keyword(user_text)
            return [_tool_chunk("search_books", {"keyword": keyword}, "call_mock_1")]

        text = (
            "你好，我是云阁图书馆智能助手。我可以帮你找书、查看我的借阅、"
            "借书、还书或预约图书，直接告诉我你想做什么就好。"
        )
        return list(_text_chunks(text))

    def _plan_after_tools(
        self, user_text: str, tool_messages: list[ToolMessage]
    ) -> list[ChatGenerationChunk]:
        """工具执行后的决策：可能继续下一个工具，也可能给出最终回答。"""
        last = tool_messages[-1]
        payload = _json_payload(last)
        called_writes = {m.name for m in tool_messages if m.name in {"borrow_book", "return_book", "reserve_book"}}
        summary = str(payload.get("summary") or "")

        # 用户拒绝了写操作
        if payload.get("cancelled") or "用户已取消" in summary:
            return list(_text_chunks("好的，已取消本次操作。还需要我帮你做什么吗？"))

        # 工具失败：如实告知，不再继续自动重试
        if not payload.get("ok", True):
            detail = summary or "业务系统暂不可用"
            return list(_text_chunks(f"抱歉，操作没有成功：{detail}"))

        if last.name in {"borrow_book", "return_book", "reserve_book"}:
            detail = summary or "已完成"
            return list(_text_chunks(f"操作成功：{self._join_sentence(detail)}还需要我帮你做什么吗？"))

        # 「还书」先查在借记录，再归还第一条
        records = _records_from(payload.get("data"))
        if last.name == "get_my_borrows":
            if _contains_any(user_text, RETURN_KEYS) and records and "return_book" not in called_writes:
                borrow_id = int(records[0].get("id", 1))
                return [_tool_chunk("return_book", {"borrowId": borrow_id}, "call_mock_1")]
            return list(_text_chunks(self._summarize_read(user_text, last.name or "", payload)))

        # 检索结果：按库存情况继续借阅 / 预约，或直接回答
        if records:
            first = records[0]
            title = str(first.get("title") or first.get("bookTitle") or "该图书")
            book_id = int(first.get("id", 1))
            available = int(first.get("availableStock", 0) or 0)
            if _contains_any(user_text, RESERVE_KEYS) and "reserve_book" not in called_writes:
                if available <= 0:
                    return [_tool_chunk("reserve_book", {"bookId": book_id}, "call_mock_1")]
                return list(_text_chunks(f"《{title}》当前有可借库存（可借 {available} 本），可以直接借阅，需要我帮你借吗？"))
            if _contains_any(user_text, BORROW_KEYS) and "borrow_book" not in called_writes:
                if available > 0:
                    return [_tool_chunk("borrow_book", {"bookId": book_id}, "call_mock_1")]
                return list(_text_chunks(f"《{title}》当前暂无可借库存，可以为你预约排队，需要吗？"))

        return list(_text_chunks(self._summarize_read(user_text, last.name or "", payload)))

    @staticmethod
    def _join_sentence(detail: str) -> str:
        """拼接句子时避免出现「。。」这类重复标点。"""
        return detail if detail.endswith(("。", "！", "？", "；")) else f"{detail}。"

    def _summarize_read(self, user_text: str, tool_name: str, payload: dict[str, Any]) -> str:
        """把只读工具结果转成固定的自然语言回答（确定性，便于断言）。"""
        data = payload.get("data")
        if tool_name == "search_books":
            records = _records_from(data)
            if not records:
                return "没有找到匹配的图书，换一个书名或作者再试试？"
            parts = []
            for record in records[:3]:
                parts.append(
                    f"《{record.get('title', '未知')}》{record.get('author', '')}"
                    f"（可借 {record.get('availableStock', 0)} 本）"
                )
            total = data.get("total", len(records)) if isinstance(data, dict) else len(records)
            return f"为你找到 {total} 本相关图书：" + "；".join(parts) + "。需要我帮你借阅或预约吗？"
        if tool_name == "get_book_detail":
            book = data.get("book", {}) if isinstance(data, dict) else {}
            waiting = data.get("waitingCount", 0) if isinstance(data, dict) else 0
            return (
                f"《{book.get('title', '未知')}》当前可借 {book.get('availableStock', 0)} 本，"
                f"共 {book.get('totalStock', 0)} 本，{waiting} 人正在排队。"
            )
        if tool_name == "get_my_borrows":
            records = data if isinstance(data, list) else []
            if not records:
                return "你当前没有在借图书，可以让我帮你找书。"
            parts = [
                f"《{item.get('bookTitle', '未知')}》应还 {item.get('dueAt', '-')}"
                for item in records[:3]
            ]
            return f"你当前有 {len(records)} 条在借记录：" + "；".join(parts) + "。"
        return str(payload.get("summary") or "已完成查询。")

    # ------------------------------------------------------------------
    # LangChain 接口
    # ------------------------------------------------------------------
    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        """mock 的意图识别是硬编码规则，不需要真正消费工具 schema。

        但仍实现 bind_tools，保证 gateway 对 mock / 真实模型使用同一条调用路径。
        """
        return self.bind(tools=list(tools), tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        aggregate: AIMessageChunk | None = None
        for chunk in self._plan(messages):
            aggregate = chunk.message if aggregate is None else aggregate + chunk.message
        content = aggregate.content if aggregate else ""
        tool_calls = [dict(call, type="tool_call") for call in (aggregate.tool_calls if aggregate else [])]
        message = AIMessage(content=content, tool_calls=tool_calls, usage_metadata=_MOCK_USAGE)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        yield from self._plan(messages)


class MockLLMProvider(BaseLLMProvider):
    """mock provider：永远可用，不需要 Key。"""

    name = "mock"
    model_name = "mock-chat"

    def build_chat_model(self) -> BaseChatModel:
        return MockChatModel()
