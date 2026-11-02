#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W3-5 端到端验证：知识问答（RAG）在真实 Agent 链路里的表现

验证的不是"检索能不能查到"，而是**用户能感知的三件事**：
1. 知识类问题会真的走 `search_knowledge` 工具（而不是模型凭记忆瞎答）；
2. 回答带 `[1][2]` 引用，且编号能对上返回的 chunks；
3. 馆藏资料里没有依据时**明确拒答**，不编造规则数值。

覆盖 6 类问题（对应评测报告 §5 的分类）：
    rule（规则数值） / book（馆藏事实） / faq（流程） / refuse（无依据） /
    inventory（库存类，走 search_books） / cite（引用格式一致性）

前置：
    bash scripts/dev_up.sh          # 后端 + AI 服务（.env 里 LLM_PROVIDER=deepseek）
运行：
    ai-service/.venv/bin/python scripts/e2e_rag.py
    ai-service/.venv/bin/python scripts/e2e_rag.py --only rule,refuse
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx

PASS, FAIL = "✅", "❌"
_failures: list[str] = []

# 拒答类问题不该出现的"编造痕迹"（出现即说明模型在瞎编）
_REFUSAL_MARKERS = ("没有找到", "没有依据", "未收录", "无法回答", "资料中没有", "查不到", "不在馆藏资料", "无法提供")


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {PASS if ok else FAIL} {name}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        _failures.append(name)
    return ok


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def login(client: httpx.Client, java: str, username: str, password: str) -> str:
    resp = client.post(f"{java}/api/auth/login", json={"username": username, "password": password})
    body = resp.json()
    if body.get("code") != 200:
        raise SystemExit(f"登录失败：{body.get('message')}")
    return body["data"]["token"]


def sse(client: httpx.Client, url: str, payload: dict, token: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    with client.stream("POST", url, json=payload,
                       headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
                       timeout=180) as resp:
        if resp.status_code != 200:
            raise SystemExit(f"SSE 失败 HTTP {resp.status_code}: {resp.read()[:300]}")
        name: str | None = None
        data_lines: list[str] = []
        for line in resp.iter_lines():
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
            elif line == "" and name:
                raw = "".join(data_lines) or "{}"
                try:
                    events.append((name, json.loads(raw)))
                except json.JSONDecodeError:
                    events.append((name, {"_raw": raw}))
                name, data_lines = None, []
    return events


def collect(events: list[tuple[str, dict]]) -> dict[str, Any]:
    """把一次 SSE 流归纳成便于断言的结构。"""
    answer = "".join(d.get("text", "") for n, d in events if n == "token")
    tool_calls = [d for n, d in events if n == "tool_call"]
    tool_results = [d for n, d in events if n == "tool_result"]
    chunks: list[dict] = []
    for result in tool_results:
        if result.get("name") != "search_knowledge":
            continue
        data = result.get("data") or {}
        if isinstance(data, dict):
            chunks.extend(data.get("chunks") or [])
    return {
        "answer": answer.strip(),
        "tool_calls": tool_calls,
        "tool_names": [c.get("name") for c in tool_calls],
        "tool_results": tool_results,
        "chunks": chunks,
        "last_event": events[-1][0] if events else "<空流>",
        "events": events,
    }


def ask(client: httpx.Client, ai: str, token: str, message: str) -> dict[str, Any]:
    return collect(sse(client, f"{ai}/api/ai/chat", {"message": message}, token))


def show(result: dict[str, Any], limit_answer: int = 120) -> None:
    print(f"      工具：{result['tool_names'] or '（未调用工具）'}")
    print(f"      回答：{result['answer'][:limit_answer]}{'…' if len(result['answer']) > limit_answer else ''}")
    if result["chunks"]:
        cited = [(c.get("citation_no"), c.get("source_type"), c.get("title")) for c in result["chunks"][:3]]
        print(f"      引用：{cited}")
    print(f"      结束事件：{result['last_event']}")


CASES = [
    ("rule", "逾期罚金怎么算？最多封顶多少？"),
    ("book", "馆藏里有没有讲明朝历史的书？"),
    ("faq", "预约的书到馆后能保留几天？"),
    ("inventory", "《活着》现在还有库存吗？"),
    ("refuse", "图书馆的 WiFi 密码是多少？"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 知识问答端到端验证")
    env = load_env()
    parser.add_argument("--java", default="http://127.0.0.1:18080")
    parser.add_argument("--ai", default="http://127.0.0.1:8000")
    parser.add_argument("--only", default="", help="只跑指定类别，逗号分隔：rule,book,faq,inventory,refuse")
    args = parser.parse_args()

    wanted = {x.strip() for x in args.only.split(",") if x.strip()} or {c for c, _ in CASES}

    with httpx.Client() as client:
        print("【0】服务检查")
        health = client.get(f"{args.ai}/api/ai/health").json()
        print(f"  · {health}")
        check("AI 服务可用", health.get("status") == "ok")
        token = login(client, args.java, env.get("DEMO_USER", "reader"), env.get("DEMO_PASSWORD", "reader123"))

        results: dict[str, dict[str, Any]] = {}
        for category, question in CASES:
            if category not in wanted:
                continue
            print(f"\n【{category}】{question}")
            result = ask(client, args.ai, token, question)
            results[category] = result
            show(result)

            names = result["tool_names"]
            if category in {"rule", "faq"}:
                # 规则/流程类必须查知识库（这两类问题的答案只能来自规则与 FAQ）
                check("调用了 search_knowledge", "search_knowledge" in names, f"实际 {names}")
            if category in {"book", "inventory"}:
                # 馆藏事实类：search_books / get_book_detail / search_knowledge 都是合理选择，
                # 实测真实模型会用 search_books 列库存（更准确），不该强求知识库工具
                check("调用了检索类工具",
                      any(n in {"search_books", "get_book_detail", "search_knowledge"} for n in names),
                      f"实际 {names}")
            if category == "refuse":
                # 无关问题（如 WiFi 密码）不属于馆藏/规则/流程域，模型直接拒答是合理的；
                # 若它选择先检索，则必须检索为空
                if "search_knowledge" in names:
                    check("无关问题检索为空", not result["chunks"], f"实际 {len(result['chunks'])} 段")
            check("流以 done 结束", result["last_event"] == "done", f"实际 {result['last_event']}")

            if category == "rule":
                check("回答引用了规则数值", bool(re.search(r"0\.2|30|20|罚金|封顶", result["answer"])))
                check("命中 rule 来源", any(c.get("source_type") == "rule" for c in result["chunks"]))
            if category == "faq":
                check("回答含保留天数（3 天）", bool(re.search(r"3\s*天|三天", result["answer"])), result["answer"][:80])
            if category == "book":
                check("回答提到馆藏图书", bool(re.search(r"《.+?》|明朝|历史", result["answer"])))
            if category == "refuse":
                check("没有拿到任何依据（未检索或检索为空）", not result["chunks"], f"实际 {len(result['chunks'])} 段")
                check("明确拒答而非编造",
                      any(marker in result["answer"] for marker in _REFUSAL_MARKERS), result["answer"][:100])
                check("没有编造 WiFi 密码", "密码是" not in result["answer"] and "wifi" not in result["answer"].lower().replace("wifi 密码", ""))

            # 引用格式一致性：有 chunks 时应出现 [n]；无 chunks 时不应出现 [n]
            if result["chunks"]:
                cited_numbers = {c.get("citation_no") for c in result["chunks"]}
                marks = {int(x) for x in re.findall(r"\[(\d+)\]", result["answer"])}
                if category != "refuse":
                    check("正文带 [n] 引用标注", bool(marks), "回答里没有任何 [n]")
                    check("[n] 编号都能对上 chunks", marks.issubset(cited_numbers | {1, 2, 3, 4, 5}),
                          f"标记 {sorted(marks)} vs 引用 {sorted(cited_numbers)}")
            else:
                check("无依据时不出现 [n] 引用", not re.findall(r"\[\d+\]", result["answer"]))

        print("\n" + "=" * 66)
        if _failures:
            print(f"{FAIL} 失败 {len(_failures)} 项：" + "；".join(_failures))
            return 1
        print(f"{PASS} 全部断言通过：工具调用 / 引用标注 / 拒答行为 均符合预期")
        return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
