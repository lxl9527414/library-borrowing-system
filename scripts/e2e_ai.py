#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W1 端到端验证：浏览器 → AI 服务 → Java 内部工具 → MySQL

验证的目标不是"接口能通"，而是**三个安全/工程命题真的成立**：

1. 身份透传：AI 服务不持有用户身份，只转发浏览器带来的 Authorization；
   Agent 的工具调用必须作用在该用户身上（换成 reader2 就查不到 reader 的借阅）。
2. 人在回路：borrow/return/reserve 这类写操作在用户确认前**绝不落库**。
3. 全链路可用：原始数据要能穿透 Agent、工具层、业务层真正写进 MySQL。

运行前置：
    # 1) 业务后端（需要 MySQL + Redis）
    cd backend && JAVA_HOME=<jdk17> DB_URL=... mvn spring-boot:run
    # 2) AI 服务（mock 模型，无需任何 Key）
    cd ai-service && LLM_PROVIDER=mock JAVA_API_BASE=http://127.0.0.1:18080 \\
        AI_INTERNAL_TOKEN=dev-internal-token .venv/bin/uvicorn app.main:app --port 8000

运行：
    ai-service/.venv/bin/python scripts/e2e_ai.py
    ai-service/.venv/bin/python scripts/e2e_ai.py --skip-return   # 只验证借，不还
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

PASS, FAIL = "✅", "❌"
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {PASS} {name}")
    else:
        _failures.append(name)
        print(f"  {FAIL} {name} {detail}")


def _load_env() -> dict[str, str]:
    """读取仓库根目录 .env（gitignored）里的本地演示凭据；没有则用种子默认值。"""
    env: dict[str, str] = {}
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def login(client: httpx.Client, java: str, username: str, password: str) -> str:
    resp = client.post(f"{java}/api/auth/login",
                       json={"username": username, "password": password})
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 200 or not body.get("data"):
        raise SystemExit(f"登录失败：{body}")
    return body["data"]["token"]


def tool_call(client: httpx.Client, java: str, token: str, internal: str,
              name: str, **params: Any) -> dict:
    """直接调用 Java 内部工具接口（用于断言真实业务状态）。"""
    headers = {"Authorization": f"Bearer {token}", "X-Internal-Token": internal}
    mapping = {
        "context": ("GET", "/api/ai/internal/context", None),
        "my_borrows": ("GET", "/api/ai/internal/borrows/my", None),
        "books": ("GET", "/api/ai/internal/books", None),
    }
    method, path, _ = mapping[name]
    resp = client.request(method, f"{java}{path}", headers=headers, params=params or None)
    return resp.json()


def sse(client: httpx.Client, url: str, payload: dict, token: str | None) -> list[tuple[str, dict]]:
    """消费一次 SSE 流，返回 [(event, data), ...]。"""
    headers = {"Accept": "text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    events: list[tuple[str, dict]] = []
    with client.stream("POST", url, json=payload, headers=headers, timeout=120) as resp:
        if resp.status_code != 200:
            raise SystemExit(f"SSE 请求失败 HTTP {resp.status_code}: {resp.read()[:300]}")
        event_name: str | None = None
        data_lines: list[str] = []
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
            elif line == "" and event_name:
                raw = "".join(data_lines) or "{}"
                try:
                    events.append((event_name, json.loads(raw)))
                except json.JSONDecodeError:
                    events.append((event_name, {"_raw": raw}))
                event_name, data_lines = None, []
    return events


def show(events: list[tuple[str, dict]], limit: int = 14) -> None:
    for name, data in events[:limit]:
        brief = json.dumps(data, ensure_ascii=False)
        print(f"      · {name:<16} {brief[:110]}")
    if len(events) > limit:
        print(f"      · … 省略 {len(events) - limit} 个事件")


def names(events: list[tuple[str, dict]]) -> list[str]:
    return [n for n, _ in events]


def last_event(events: list[tuple[str, dict]]) -> str:
    return events[-1][0] if events else "<空流>"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--java", default="http://127.0.0.1:18080")
    parser.add_argument("--ai", default="http://127.0.0.1:8000")
    env = _load_env()
    parser.add_argument("--user", default=env.get("DEMO_USER", "reader"), help="普通读者账号")
    parser.add_argument("--password", default=env.get("DEMO_PASSWORD", "reader123"))
    parser.add_argument("--internal-token", default=_load_env().get("AI_INTERNAL_TOKEN", "dev-internal-token"))
    parser.add_argument("--book", default="红楼梦", help="用于借还验证的书名（需有库存）")
    parser.add_argument("--skip-return", action="store_true")
    args = parser.parse_args()

    with httpx.Client() as client:
        print("【0】健康检查")
        health = client.get(f"{args.ai}/api/ai/health").json()
        print(f"  · AI 服务 {health}")
        check("AI 服务健康", health.get("status") == "ok")
        provider = str(health.get("llmProvider") or "未知")
        check(f"模型提供方：{provider}", provider in ("mock", "deepseek", "qwen", "ollama"),
              f"实际 {provider}（mock=离线关键词模型，其余为真实大模型）")

        print("\n【1】登录并确认初始借阅状态")
        token = login(client, args.java, args.user, args.password)
        print(f"  · token {token[:10]}…")
        ctx = tool_call(client, args.java, token, args.internal_token, "context")["data"]
        # 幂等保障：先清空该演示账号的所有在借记录。
        # 原因：真实模型遇到"有多条在借"时会合理地反问"要还哪一本"，而 mock 直接还第一条；
        # 清空后语义唯一，脚本才能在 mock 与真实模型下都确定性通过。
        stale = tool_call(client, args.java, token, args.internal_token, "my_borrows")["data"]
        for record in stale:
            client.post(f"{args.java}/api/ai/internal/return", json={"borrowId": record["id"]},
                        headers={"Authorization": f"Bearer {token}",
                                 "X-Internal-Token": args.internal_token})
            print(f"  · 清理残留借阅《{record.get('bookTitle')}》(borrowId={record['id']})")
        if stale:
            ctx = tool_call(client, args.java, token, args.internal_token, "context")["data"]
        before_count = ctx["borrowedCount"]
        print(f"  · 当前在借 {before_count} 本，读者 {ctx['user']['realName']}，规则上限 "
              f"{ctx['rule']['maxBorrowCount']} 本")

        print(f"\n【2】自然语言问书：「帮我找{args.book}」")
        events = sse(client, f"{args.ai}/api/ai/chat",
                     {"message": f"帮我找{args.book}"}, token)
        show(events)
        check("收到 meta 事件", "meta" in names(events))
        check("调用了只读工具 search_books",
              any(n == "tool_call" and d.get("name") == "search_books" for n, d in events))
        check("流以 done 结束", last_event(events) == "done", f"实际 {last_event(events)}")

        print(f"\n【3】发起借书（写操作，必须等确认）：「帮我借{args.book}」")
        events = sse(client, f"{args.ai}/api/ai/chat",
                     {"message": f"帮我借{args.book}"}, token)
        show(events)
        confirm = next((d for n, d in events if n == "confirm_required"), None)
        check("出现 confirm_required（人在回路）", confirm is not None,
              f"事件序列 {names(events)}")

        # 未确认前，数据库里不允许有新增借阅
        mid_ctx = tool_call(client, args.java, token, args.internal_token, "context")["data"]
        check("用户确认前借阅数量未变（未落库）", mid_ctx["borrowedCount"] == before_count,
              f"{before_count} → {mid_ctx['borrowedCount']}")

        if confirm is None:
            print("\n无法继续确认流程，终止")
            return 1

        session_id = confirm.get("sessionId") or next(
            d.get("sessionId") for n, d in events if n == "meta")
        confirmation_id = confirm.get("confirmationId")
        print(f"\n【4】用户点击确认（sessionId={session_id}, confirmationId={confirmation_id}）")
        events = sse(client, f"{args.ai}/api/ai/chat/confirm",
                     {"sessionId": session_id, "confirmationId": confirmation_id,
                      "approved": True}, token)
        show(events)
        check("调用了写工具 borrow_book",
              any(n == "tool_call" and d.get("name") == "borrow_book" for n, d in events))
        ok_results = [d for n, d in events if n == "tool_result" and d.get("ok")]
        check("写操作工具返回成功", bool(ok_results), f"事件序列 {names(events)}")
        check("确认后流以 done 结束", last_event(events) == "done", f"实际 {last_event(events)}")

        after_ctx = tool_call(client, args.java, token, args.internal_token, "context")["data"]
        check("借阅数量 +1（真实落库）", after_ctx["borrowedCount"] == before_count + 1,
              f"{before_count} → {after_ctx['borrowedCount']}")

        print("\n【5】验证工具层读到的是当前用户的数据：「我的借阅」")
        events = sse(client, f"{args.ai}/api/ai/chat", {"message": "我的借阅"}, token)
        show(events)
        borrows = tool_call(client, args.java, token, args.internal_token,
                            "my_borrows")["data"]
        titles = [b.get("bookTitle") for b in borrows]
        print(f"  · 工具层返回在借：{titles}")
        check(f"在借列表包含《{args.book}》", any(args.book in (t or "") for t in titles))
        check("调用了只读工具 get_my_borrows",
              any(n == "tool_call" and d.get("name") == "get_my_borrows" for n, d in events))

        if not args.skip_return:
            print(f"\n【6】还书（写操作，同样要确认）：「我要还书」")
            events = sse(client, f"{args.ai}/api/ai/chat",
                         {"message": f"我要归还《{args.book}》"}, token)
            show(events)
            confirm2 = next((d for n, d in events if n == "confirm_required"), None)
            check("还书同样出现 confirm_required", confirm2 is not None,
                  f"事件序列 {names(events)}")
            if confirm2 is not None:
                sid2 = confirm2.get("sessionId") or next(
                    d.get("sessionId") for n, d in events if n == "meta")
                events = sse(client, f"{args.ai}/api/ai/chat/confirm",
                             {"sessionId": sid2,
                              "confirmationId": confirm2.get("confirmationId"),
                              "approved": True}, token)
                show(events)
                final_ctx = tool_call(client, args.java, token, args.internal_token,
                                      "context")["data"]
                check("归还后借阅数量回到初始值",
                      final_ctx["borrowedCount"] == before_count,
                      f"{after_ctx['borrowedCount']} → {final_ctx['borrowedCount']}")

    print("\n" + "=" * 66)
    if _failures:
        print(f"{FAIL} 失败 {len(_failures)} 项：" + "；".join(_failures))
        return 1
    print(f"{PASS} 全部断言通过：身份透传 / 人在回路 / 真实落库 三条命题均成立")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
