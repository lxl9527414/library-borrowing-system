"""LLM 工具 schema（契约 §3 的 7 个工具：6 个业务工具 + 1 个知识检索）。

统一用 OpenAI function calling 的 JSON Schema 格式，便于 mock 与真实模型共用。
工具名 / 参数以本文件为唯一来源（Java 侧只负责实现 §2 的 HTTP 端点；
``search_knowledge`` 走本地 RAG 检索，不经过 Java 后端）。
"""

from __future__ import annotations

from typing import Any

# 写操作工具：必须走 interrupt 确认流程（契约 §3 / §6）
WRITE_TOOLS: frozenset[str] = frozenset({"borrow_book", "return_book", "reserve_book"})

# 只读工具
READ_TOOLS: frozenset[str] = frozenset(
    {"search_books", "get_book_detail", "get_my_borrows", "search_knowledge"}
)

# search_knowledge 的来源过滤取值（与 data/corpus 的 source_type 对齐）
KNOWLEDGE_SOURCE_TYPES: tuple[str, ...] = ("book", "rule", "faq")

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
            "name": "search_knowledge",
            "description": (
                "检索馆藏知识库（借阅规则、罚金标准、预约/取书规则、馆藏图书事实、系统使用流程），"
                "返回带 citation_no 编号的依据片段。当问题涉及借阅规则、罚金、预约、馆藏事实、"
                "使用流程时调用；回答时必须用 [编号] 标注依据，不得凭记忆编造规则数值。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要检索的问题或关键词（中文，尽量保留原问题的关键实体）",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，默认 5，范围 1~10",
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "source_types": {
                        "type": "array",
                        "description": "可选来源过滤：book=馆藏图书、rule=借阅规则、faq=常见问题；不传则检索全部",
                        "items": {"type": "string", "enum": list(KNOWLEDGE_SOURCE_TYPES)},
                    },
                },
                "required": ["query"],
            },
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
