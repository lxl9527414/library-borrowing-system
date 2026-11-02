#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG 答案层评测（W3-6 后半）：faithfulness / answer_relevancy / context_precision / 拒答正确性

为什么自己实现而不是直接 `pip install ragas`：
- ragas 依赖 datasets/tiktoken 等重型包，且 tiktoken 首次运行需要联网下载 BPE 词表（本机网络受限，实测被墙）；
- 本项目只需要 4 个核心指标，指标定义参照 RAGAS 论文，用 DeepSeek 当 judge 即可复现；
- 自己写能控制判官提示词、成本与可观测性（每条用例 1 次 judge 调用，返回结构化 JSON）。

指标定义（与 RAGAS 对齐）：
- faithfulness      答案中的陈述有多少能被检索到的 chunks 支持（0~1）
- answer_relevancy  答案是否切题、是否直接回答了问题（0~1）
- context_precision 召回的 chunks 中真正相关的比例（相关块数 / 总块数）
- 引用准确率         正文标注的 [n] 里，有多少指向"被判定为相关"的块
- 拒答正确性         should_refuse 用例：是否明确拒答且没有编造（单独统计，不计入上面四项）

流程（每条用例）：
    AI 服务 /api/ai/chat（真实模型 + 真实检索）→ 答案 + chunks + 延迟/ tokens
        → DeepSeek judge 打分（1 次调用，JSON 输出）

运行（需要 AI 服务在 8000、DeepSeek Key 在 .env）：
    ai-service/.venv/bin/python scripts/eval_answers.py
    ai-service/.venv/bin/python scripts/eval_answers.py --limit 20     # 快速冒烟
    ai-service/.venv/bin/python scripts/eval_answers.py --only rule,faq
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

AI_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLDEN = AI_ROOT / "eval" / "golden_qa.jsonl"
DEFAULT_OUT = AI_ROOT / "eval" / "eval_report_answers.json"

JUDGE_MODEL = "deepseek-chat"


# ---------------------------------------------------------------------------
# 环境与客户端
# ---------------------------------------------------------------------------
def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = AI_ROOT.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in os.environ.items():
        env.setdefault(key, value)
    return env


# ---------------------------------------------------------------------------
# 1) 调 AI 服务拿答案（真实链路：模型 + 工具 + 检索）
# ---------------------------------------------------------------------------
def ask_agent(client: httpx.Client, ai_base: str, token: str, question: str, timeout: float = 180) -> dict[str, Any]:
    events: list[tuple[str, dict]] = []
    with client.stream(
        "POST",
        f"{ai_base}/api/ai/chat",
        json={"message": question},
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        timeout=timeout,
    ) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"AI 服务返回 HTTP {resp.status_code}: {resp.read()[:200]}")
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
                    events.append((name, {}))
                name, data_lines = None, []

    answer = "".join(d.get("text", "") for n, d in events if n == "token").strip()
    chunks: list[dict] = []
    evidence: list[dict] = []      # 模型实际拿到的全部证据（知识库 + 馆藏接口）
    for n, d in events:
        if n != "tool_result":
            continue
        payload = d.get("data") or {}
        if d.get("name") == "search_knowledge" and isinstance(payload, dict):
            chunks.extend(payload.get("chunks") or [])
        if isinstance(payload, dict):
            compact = {}
            for key in ("chunks", "records", "book", "waitingCount", "myReservation"):
                if key in payload:
                    compact[key] = payload[key]
            if compact:
                evidence.append({"tool": d.get("name"), "summary": d.get("summary"), "data": compact})
        elif isinstance(payload, list):
            evidence.append({"tool": d.get("name"), "summary": d.get("summary"), "data": payload})
    done = next((d for n, d in events if n == "done"), {})
    errors = [d for n, d in events if n == "error"]
    return {
        "answer": answer,
        "chunks": chunks,
        "evidence": evidence,
        "latency_ms": done.get("latencyMs"),
        "usage": done.get("usage") or {},
        "last_event": events[-1][0] if events else None,
        "error": errors[0].get("message") if errors else None,
    }


# ---------------------------------------------------------------------------
# 2) DeepSeek judge
# ---------------------------------------------------------------------------
JUDGE_SYSTEM = """你是严格的 RAG 评测裁判。只依据给出的【检索片段】判断【回答】，不要用你自己的知识补充。
必须只输出一个 JSON 对象，不要输出 markdown 代码块或任何解释文字。"""

JUDGE_RUBRIC = """评分规则：
- faithfulness：回答中的事实性陈述有多少能被检索片段支持。0 = 大量编造，1 = 全部有依据。只统计事实陈述，不罚"礼貌用语"。
- answer_relevancy：回答是否切题、是否直接解决用户问题。0 = 答非所问，1 = 完全切题。
- relevant_chunk_ids：检索片段中真正与问题相关、能支撑答案的片段 id 列表（片段不一定被回答引用，只要相关就算）。
- unsupported_claims：回答中找不到片段依据的陈述（原文摘录，最多 3 条）。
"""


def judge(client: httpx.Client, env: dict[str, str], question: str, answer: str,
          chunks: list[dict], evidence: list[dict] | None = None) -> dict[str, Any]:
    api_key = env.get("DEEPSEEK_API_KEY", "")
    base_url = env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    if not api_key:
        raise SystemExit("缺少 DEEPSEEK_API_KEY，无法进行 LLM 评判")

    lines = [
        f"[{c.get('chunk_id')}] ({c.get('source_type')}) {str(c.get('text') or '')[:400]}" for c in chunks
    ]
    # 馆藏检索（search_books / get_book_detail）的结果同样是"模型可用的证据"，不能只看知识库
    for item in evidence or []:
        if item.get("tool") == "search_knowledge":
            continue
        snippet = json.dumps(item.get("data"), ensure_ascii=False)[:1200]
        lines.append(f"[tool:{item.get('tool')}] 摘要：{item.get('summary')} 数据：{snippet}")
    chunk_text = "\n".join(lines) or "（本轮没有检索到任何片段）"
    user_prompt = f"""【问题】
{question}

【检索片段】
{chunk_text}

【回答】
{answer or "（空）"}

{JUDGE_RUBRIC}

输出 JSON 结构：
{{"faithfulness": 0.0, "answer_relevancy": 0.0, "relevant_chunk_ids": [], "unsupported_claims": []}}"""

    payload = {
        "model": env.get("JUDGE_MODEL", JUDGE_MODEL),
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    last_error = ""
    for attempt in range(3):
        try:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=90,
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                return json.loads(content)
            last_error = f"HTTP {resp.status_code}: {resp.text[:160]}"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"judge 调用失败：{last_error}")


REFUSE_SYSTEM = """你是 RAG 评测裁判，判断助手面对"馆藏资料无法回答的问题"时的表现。
只输出 JSON：{"refused": true/false, "hallucinated": true/false, "reason": "简短理由"}
refused=true 表示明确说明查不到/没有依据；hallucinated=true 表示编造了具体信息（如具体的 WiFi 密码）。"""


def judge_refusal(client: httpx.Client, env: dict[str, str], question: str, answer: str) -> dict[str, Any]:
    api_key = env.get("DEEPSEEK_API_KEY", "")
    base_url = env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    payload = {
        "model": env.get("JUDGE_MODEL", JUDGE_MODEL),
        "messages": [
            {"role": "system", "content": REFUSE_SYSTEM},
            {"role": "user", "content": f"【问题】{question}\n\n【助手回答】{answer or '（空）'}"},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    resp = client.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])


# ---------------------------------------------------------------------------
# 3) 评测主流程
# ---------------------------------------------------------------------------
def load_golden(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"评测集不存在：{path}（先跑 scripts/gen_golden_qa.py）")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def citation_stats(answer: str, chunks: list[dict], relevant_ids: set[str]) -> dict[str, Any]:
    """引用覆盖与准确率（不需要 LLM）：正文 [n] → chunk，检查该 chunk 是否被判为相关。"""
    by_no = {int(c.get("citation_no") or i + 1): c for i, c in enumerate(chunks)}
    cited = [int(x) for x in re.findall(r"\[(\d+)\]", answer)]
    sentences = [s for s in re.split(r"[。！？\n]", answer) if s.strip()]
    with_citation = [s for s in sentences if re.search(r"\[\d+\]", s)]
    cited_ids = {str(by_no[n].get("chunk_id")) for n in cited if n in by_no}
    valid = cited_ids & relevant_ids if relevant_ids else set()
    return {
        "citations": len(cited),
        "coverage": round(len(with_citation) / len(sentences), 3) if sentences else 0.0,
        "accuracy": round(len(valid) / len(cited_ids), 3) if cited_ids else None,
    }


def summarize(rows: list[dict]) -> dict[str, Any]:
    def agg(subset: list[dict], key: str) -> float | None:
        values = [r[key] for r in subset if isinstance(r.get(key), (int, float))]
        return round(statistics.fmean(values), 4) if values else None

    answerable = [r for r in rows if not r["should_refuse"]]
    refused = [r for r in rows if r["should_refuse"]]
    result: dict[str, Any] = {
        "n_answerable": len(answerable),
        "n_refuse": len(refused),
        "overall": {
            "faithfulness": agg(answerable, "faithfulness"),
            "answer_relevancy": agg(answerable, "answer_relevancy"),
            "context_precision": agg(answerable, "context_precision"),
            "citation_accuracy": agg(answerable, "citation_accuracy"),
            "citation_coverage": agg(answerable, "citation_coverage"),
            "avg_latency_ms": round(statistics.fmean([r["latency_ms"] for r in answerable if r.get("latency_ms")]), 1)
            if any(r.get("latency_ms") for r in answerable) else None,
        },
        "by_category": {},
        "refusal": {
            "correct_refusals": sum(1 for r in refused if r.get("refused")),
            "hallucinated": sum(1 for r in refused if r.get("hallucinated")),
            "n": len(refused),
        },
    }
    for category in sorted({r["category"] for r in answerable}):
        subset = [r for r in answerable if r["category"] == category]
        result["by_category"][category] = {
            "n": len(subset),
            "faithfulness": agg(subset, "faithfulness"),
            "answer_relevancy": agg(subset, "answer_relevancy"),
            "context_precision": agg(subset, "context_precision"),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 答案层评测（LLM-as-judge）")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--java", default="http://127.0.0.1:18080")
    parser.add_argument("--ai", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（快速冒烟）")
    parser.add_argument("--only", default="", help="按类别过滤，逗号分隔：book,rule,faq,refuse")
    parser.add_argument("--sleep", type=float, default=0.3, help="每条之间的间隔（避免限流）")
    args = parser.parse_args()

    env = load_env()
    golden = load_golden(Path(args.golden))
    wanted = {x.strip() for x in args.only.split(",") if x.strip()}
    if wanted:
        golden = [g for g in golden if g["category"] in wanted]
    if args.limit:
        golden = golden[: args.limit]

    rows: list[dict] = []
    with httpx.Client() as client:
        login = client.post(
            f"{args.java}/api/auth/login",
            json={"username": env.get("DEMO_USER", "reader"), "password": env.get("DEMO_PASSWORD", "reader123")},
        ).json()
        if login.get("code") != 200:
            raise SystemExit(f"登录失败：{login.get('message')}")
        token = login["data"]["token"]

        total = len(golden)
        print(f"开始评测 {total} 条（真实模型 + 真实检索 + DeepSeek judge）…\n")
        for index, item in enumerate(golden, start=1):
            question = item["question"]
            result = None
            last_error = ""
            for attempt in range(3):  # 真实模型/网络偶发中断 → 重试两次
                try:
                    candidate = ask_agent(client, args.ai, token, question)
                    if candidate["answer"] and candidate["last_event"] == "done":
                        result = candidate
                        break
                    last_error = f"空答案或异常结束（last_event={candidate['last_event']}, error={candidate.get('error')}）"
                except Exception as exc:  # noqa: BLE001
                    last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(2.0 * (attempt + 1))
            if result is None:
                print(f"[{index}/{total}] {question[:30]} → ❌ 两次失败：{last_error}")
                rows.append({"id": item["id"], "category": item["category"], "should_refuse": item["should_refuse"],
                             "question": question, "answer": "", "error": last_error})
                continue

            row: dict[str, Any] = {
                "id": item["id"],
                "category": item["category"],
                "should_refuse": item["should_refuse"],
                "question": question,
                "answer": result["answer"],
                "n_chunks": len(result["chunks"]),
                "latency_ms": result["latency_ms"],
                "usage": result["usage"],
            }
            try:
                if item["should_refuse"]:
                    verdict = judge_refusal(client, env, question, result["answer"])
                    row.update({"refused": bool(verdict.get("refused")), "hallucinated": bool(verdict.get("hallucinated"))})
                    mark = "✅拒答" if row["refused"] and not row["hallucinated"] else "⚠️"
                else:
                    verdict = judge(client, env, question, result["answer"], result["chunks"], result.get("evidence"))
                    relevant_ids = {str(x) for x in (verdict.get("relevant_chunk_ids") or [])}
                    total_chunks = max(len(result["chunks"]), 1)
                    cites = citation_stats(result["answer"], result["chunks"], relevant_ids)
                    row.update({
                        "faithfulness": round(float(verdict.get("faithfulness") or 0), 3),
                        "answer_relevancy": round(float(verdict.get("answer_relevancy") or 0), 3),
                        "context_precision": round(len(relevant_ids & {str(c.get("chunk_id")) for c in result["chunks"]}) / total_chunks, 3),
                        "citation_accuracy": cites["accuracy"],
                        "citation_coverage": cites["coverage"],
                        "unsupported_claims": verdict.get("unsupported_claims") or [],
                    })
                    mark = f"F={row['faithfulness']:.2f} R={row['answer_relevancy']:.2f}"
            except Exception as exc:  # noqa: BLE001
                row["judge_error"] = str(exc)
                mark = f"⚠️ judge 失败 {exc}"
            rows.append(row)
            print(f"[{index}/{total}] [{item['category']}] {question[:26]} … {mark}  ({row.get('latency_ms') or '-'} ms)")
            time.sleep(args.sleep)

    report = summarize(rows)
    report["rows"] = rows
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 74)
    print(f"答案层评测（{report['n_answerable']} 条可答 + {report['n_refuse']} 条应拒答）")
    print("-" * 74)
    print(f"{'类别':<8}{'用例':>5}{'faithfulness':>14}{'relevancy':>12}{'ctx_precision':>15}")
    for category, stats in report["by_category"].items():
        print(f"{category:<8}{stats['n']:>5}{stats['faithfulness']:>14.3f}"
              f"{stats['answer_relevancy']:>12.3f}{stats['context_precision']:>15.3f}")
    overall = report["overall"]
    print("-" * 74)
    print(f"{'总体':<8}{report['n_answerable']:>5}{overall['faithfulness']:>14.3f}"
          f"{overall['answer_relevancy']:>12.3f}{overall['context_precision']:>15.3f}")
    print(f"\n引用：准确率 {overall['citation_accuracy']} / 覆盖率 {overall['citation_coverage']}"
          f"  |  平均延迟 {overall['avg_latency_ms']} ms")
    refusal = report["refusal"]
    print(f"拒答：正确 {refusal['correct_refusals']}/{refusal['n']}，编造 {refusal['hallucinated']}")
    print(f"\n报告已写入 {out_path}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
