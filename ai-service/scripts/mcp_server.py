#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP Server（W4-4）：把项目的 7 个能力暴露成标准 MCP 工具

为什么做这个：
- MCP（Model Context Protocol）让"工具层"可以被任意 Agent 客户端复用（Claude Desktop / IDE / 自研 Agent）；
- 对简历的意义：证明工具层不是和某个框架绑死，而是**可被标准协议消费的能力**。

工具与 Agent 内部工具一一对应（语义保持一致）：
  search_knowledge / search_books / get_book_detail / get_my_borrows
  borrow_book / return_book / reserve_book   ← 写操作，必须显式 confirm=True

鉴权模型（重要）：
- MCP 客户端不在浏览器里，没有用户 token。本服务用环境变量 `MCP_USER_TOKEN` 作为"调用者身份"，
  原样转发给 Java 内部接口（服务端仍按 token 解析用户，LLM/客户端无法指定 userId）。
- 未设置 token 时：只读工具会给出清晰错误；`search_knowledge` 不依赖用户身份，可直接用。

运行：
    # stdio（给 Claude Desktop / IDE 用）
    MCP_USER_TOKEN=<token> ai-service/.venv/bin/python ai-service/scripts/mcp_server.py

    # 自检（不需要客户端）：列出工具 + 调一次知识检索
    ai-service/.venv/bin/python ai-service/scripts/mcp_server.py --selftest

Claude Desktop 配置示例（~/Library/Application Support/Claude/claude_desktop_config.json）：
    {
      "mcpServers": {
        "library-ai": {
          "command": "/绝对路径/ai-service/.venv/bin/python",
          "args": ["/绝对路径/ai-service/scripts/mcp_server.py"],
          "env": { "MCP_USER_TOKEN": "你的读者 token", "JAVA_API_BASE": "http://127.0.0.1:18080" }
        }
      }
    }
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

AI_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AI_ROOT))

JAVA_API_BASE = os.environ.get("JAVA_API_BASE", "http://127.0.0.1:18080").rstrip("/")
INTERNAL_TOKEN = os.environ.get("AI_INTERNAL_TOKEN", "dev-internal-token")


def _java_headers() -> dict[str, str]:
    token = os.environ.get("MCP_USER_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "缺少 MCP_USER_TOKEN：MCP 客户端没有浏览器登录态，需要用环境变量提供用户 token"
            "（从 /api/auth/login 获取后填入 MCP 客户端配置的 env）"
        )
    return {"Authorization": f"Bearer {token}", "X-Internal-Token": INTERNAL_TOKEN}


async def _java_get(path: str, params: dict[str, Any] | None = None) -> Any:
    import httpx

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{JAVA_API_BASE}{path}", params=params, headers=_java_headers())
        return _unwrap(resp)


async def _java_post(path: str, payload: dict[str, Any]) -> Any:
    import httpx

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(f"{JAVA_API_BASE}{path}", json=payload, headers=_java_headers())
        return _unwrap(resp)


def _unwrap(resp) -> Any:
    body = resp.json()
    if resp.status_code >= 400:
        raise RuntimeError(f"业务接口鉴权失败 HTTP {resp.status_code}: {body.get('message')}")
    if body.get("code") != 200:
        raise RuntimeError(f"业务接口返回错误 {body.get('code')}: {body.get('message')}")
    return body.get("data")


def _knowledge_search(query: str, top_k: int = 5, source_types: list[str] | None = None) -> list[dict]:
    from app.rag.service import get_rag_service

    results = get_rag_service().search(query, top_k=top_k, source_types=source_types)
    return [
        {
            "citation_no": index + 1,
            "chunk_id": item.get("chunk_id"),
            "source_type": item.get("source_type"),
            "title": item.get("title"),
            "text": str(item.get("text") or "")[:500],
            "score": item.get("score"),
        }
        for index, item in enumerate(results or [])
    ]


def build_server():
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="library-ai",
        version="1.0.0",
        instructions=(
            "云阁图书馆 AI 能力。查规则/罚金/预约等知识用 search_knowledge（回答需带 [n] 引用）；"
            "查馆藏/库存用 search_books；写操作（借/还/预约）必须先用 confirm=False 预览，"
            "用户确认后再以 confirm=True 调用。"
        ),
    )

    @server.tool(description="检索馆藏知识库（借阅规则、罚金、预约流程、图书简介）。返回带 citation_no 的片段，回答时应引用 [n]。")
    async def search_knowledge(query: str, top_k: int = 5, source_types: list[str] | None = None) -> dict:
        chunks = await asyncio.to_thread(_knowledge_search, query, top_k, source_types)
        return {"ok": True, "count": len(chunks), "chunks": chunks}

    @server.tool(description="按书名/作者/分类检索馆藏，返回库存情况")
    async def search_books(keyword: str | None = None, category: str | None = None, size: int = 5) -> dict:
        data = await _java_get("/api/ai/internal/books", {"keyword": keyword, "category": category, "size": min(max(size, 1), 20)})
        records = data.get("records") or []
        return {"ok": True, "count": len(records), "records": records}

    @server.tool(description="查询单本图书详情（含库存与预约队列）")
    async def get_book_detail(book_id: int) -> dict:
        return {"ok": True, "data": await _java_get(f"/api/ai/internal/books/{book_id}")}

    @server.tool(description="查询当前用户（MCP_USER_TOKEN 对应读者）的在借记录、到期与逾期")
    async def get_my_borrows() -> dict:
        records = await _java_get("/api/ai/internal/borrows/my")
        return {"ok": True, "count": len(records or []), "records": records}

    @server.tool(description="借阅一本书。写操作：先 confirm=false 预览，用户同意后再 confirm=true 执行")
    async def borrow_book(book_id: int, confirm: bool = False) -> dict:
        if not confirm:
            return {"ok": False, "requires_confirmation": True,
                    "preview": f"即将借阅 bookId={book_id}，请确认后以 confirm=true 重新调用"}
        return {"ok": True, "data": await _java_post("/api/ai/internal/borrow", {"bookId": book_id})}

    @server.tool(description="归还一本在借图书。写操作：先 confirm=false 预览，用户同意后再 confirm=true 执行")
    async def return_book(borrow_id: int, confirm: bool = False) -> dict:
        if not confirm:
            return {"ok": False, "requires_confirmation": True,
                    "preview": f"即将归还 borrowId={borrow_id}，请确认后以 confirm=true 重新调用"}
        return {"ok": True, "data": await _java_post("/api/ai/internal/return", {"borrowId": borrow_id})}

    @server.tool(description="预约一本无库存的图书。写操作：先 confirm=false 预览，用户同意后再 confirm=true 执行")
    async def reserve_book(book_id: int, confirm: bool = False) -> dict:
        if not confirm:
            return {"ok": False, "requires_confirmation": True,
                    "preview": f"即将预约 bookId={book_id}，请确认后以 confirm=true 重新调用"}
        return {"ok": True, "data": await _java_post("/api/ai/internal/reserve", {"bookId": book_id})}

    return server


async def _selftest() -> int:
    server = build_server()
    tools = await server.list_tools()
    print(f"✅ 已注册 {len(tools)} 个 MCP 工具：")
    for tool in tools:
        print(f"   - {tool.name}: {(tool.description or '')[:40]}")
    print("\n调用 search_knowledge('逾期罚金怎么算')…")
    result = await server.call_tool("search_knowledge", {"query": "逾期罚金怎么算", "top_k": 3})
    # mcp v2 的 call_tool 直接返回 CallToolResult：优先 structuredContent，其次 content[0].text
    payload: Any
    structured = getattr(result, "structuredContent", None)
    content = getattr(result, "content", None)
    if structured:
        payload = structured
    elif isinstance(content, list) and content and getattr(content[0], "text", None):
        payload = json.loads(content[0].text)
    else:
        payload = {"raw": str(result)[:300]}
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:600])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="云阁图书馆 MCP Server")
    parser.add_argument("--selftest", action="store_true", help="列出工具并调用一次知识检索（不需要客户端）")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse", "streamable-http"])
    args = parser.parse_args()
    if args.selftest:
        return asyncio.run(_selftest()) or 0
    build_server().run(transport=args.transport)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
