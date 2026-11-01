"""LLM 工具 schema（契约 §3 的 6 个工具）。

统一用 OpenAI function calling 的 JSON Schema 格式，便于 mock 与真实模型共用。
工具名 / 参数以本文件为唯一来源（Java 侧只负责实现 §2 的 HTTP 端点）。
"""

from __future__ import annotations

from typing import Any

# 写操作工具：必须走 interrupt 确认流程（契约 §3 / §6）
WRITE_TOOLS: frozenset[str] = frozenset({"borrow_book", "return_book", "reserve_book"})

# 只读工具
READ_TOOLS: frozenset[str] = frozenset({"search_books", "get_book_detail", "get_my_borrows"})

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_books",
            "description": "按书名/作者/ISBN/分类检索馆藏图书，返回库存状态。用户想找书、问有没有某本书时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "书名 / 作者 / ISBN 关键词，可为空"},
                    "category": {"type": "string", "description": "图书分类，如 科幻、文学，可为空"},
                    "size": {"type": "integer", "description": "返回条数，默认 5，最大 20", "minimum": 1, "maximum": 20},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_book_detail",
            "description": "查询单本图书的详细信息：库存、预约队列长度、我的预约状态。用户问某本书详情/库存时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "bookId": {"type": "integer", "description": "图书 ID"},
                },
                "required": ["bookId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_borrows",
            "description": "查询当前登录用户的在借记录：到期时间、逾期天数、续借次数、罚金。用户问“我借了什么/什么时候还/有没有逾期”时使用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "borrow_book",
            "description": "借阅一本图书（写操作，需要用户确认）。仅在用户明确表达借阅意图后调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "bookId": {"type": "integer", "description": "要借阅的图书 ID"},
                },
                "required": ["bookId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "return_book",
            "description": "归还一本已借图书（写操作，需要用户确认）。borrowId 取自 get_my_borrows 返回的记录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "borrowId": {"type": "integer", "description": "借阅记录 ID"},
                },
                "required": ["borrowId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reserve_book",
            "description": "预约/排队一本暂无库存的图书（写操作，需要用户确认）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "bookId": {"type": "integer", "description": "要预约的图书 ID"},
                },
                "required": ["bookId"],
            },
        },
    },
]

TOOL_NAMES: frozenset[str] = frozenset(schema["function"]["name"] for schema in TOOL_SCHEMAS)


def is_write_tool(name: str) -> bool:
    return name in WRITE_TOOLS


def is_known_tool(name: str) -> bool:
    return name in TOOL_NAMES


# 确认卡片文案模板：把工具参数翻译成人话（契约 §4.1 confirm_required.summary）
CONFIRM_SUMMARY_TEMPLATES: dict[str, str] = {
    "borrow_book": "即将为你借阅《{title}》（bookId={bookId}），确认借阅吗？",
    "return_book": "即将为你归还《{title}》（borrowId={borrowId}），确认归还吗？",
    "reserve_book": "《{title}》当前暂无可借库存，是否为你预约排队？",
}
