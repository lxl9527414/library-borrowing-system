#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG 检索层评测（离线可跑，不需要任何 API Key）

为什么先评检索、而不是直接评"答案好不好"：
1. RAG 的答案质量上限由**召回**决定——召回不到，模型只能编；
2. 检索指标（Recall@k / MRR）**不需要 LLM 当裁判**，可离线、可进 CI、可复现；
3. 答案层指标（faithfulness / answer relevancy）需要 LLM-as-judge，等真实模型 Key 到位后再补（见 W3-6）。

指标口径：
- Recall@k：top-k 里至少有一个"能支持答案的块"的比例（块要同时满足 source_type 与关键词约束）
- MRR：第一个可支持块的倒数排名均值
- 拒答类（should_refuse）不计入召回指标；单独报告其 top1 分数分布，用于后续设置拒答阈值

用法：
    cd ai-service && .venv/bin/python scripts/run_eval.py
    cd ai-service && .venv/bin/python scripts/run_eval.py --top-k 10 --min-recall 0.7
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

AI_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AI_ROOT))

DEFAULT_GOLDEN = AI_ROOT / "eval" / "golden_qa.jsonl"
DEFAULT_OUT = AI_ROOT / "eval" / "eval_report_bm25.json"


def load_golden(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"评测集不存在：{path}\n请先执行：.venv/bin/python scripts/gen_golden_qa.py")
    items = []
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def matches(item: dict, chunk: dict) -> bool:
    """判断一个召回块是否"真的能支持这条问题的答案"。

    两个约束同时满足才算命中：
    1) source_type 符合预期（book 问题不该被 FAQ 块"蒙对"）
    2) 关键词命中（all: 全部出现；any: 任一出现）——避免只靠 id 比对的自欺
    """
    expected_types = item.get("expected_source_types") or []
    if expected_types and chunk.get("source_type") not in expected_types:
        return False
    keywords = [k for k in (item.get("expected_keywords") or []) if k]
    if not keywords:
        return True
    text = chunk.get("text") or ""
    if item.get("match") == "all":
        return all(k in text for k in keywords)
    return any(k in text for k in keywords)


def evaluate(service, golden: list[dict], top_k: int) -> dict:
    rows = []
    for item in golden:
        results = service.search(item["question"], top_k=top_k)
        rank = None
        for index, chunk in enumerate(results, start=1):
            if matches(item, chunk):
                rank = index
                break
        rows.append({
            "id": item["id"],
            "category": item["category"],
            "question": item["question"],
            "should_refuse": bool(item.get("should_refuse")),
            "hit_rank": rank,
            "top1_score": round(float(results[0]["score"]), 4) if results else 0.0,
            "top1_id": results[0]["chunk_id"] if results else None,
            "top1_title": results[0].get("title") if results else None,
        })

    def summarize(subset: list[dict]) -> dict:
        scored = [r for r in subset if not r["should_refuse"]]
        if not scored:
            return {"n": 0}
        hits = [r for r in scored if r["hit_rank"]]
        reciprocal = sum(1.0 / r["hit_rank"] for r in hits) / len(scored)
        return {
            "n": len(scored),
            "recall_at_k": round(len(hits) / len(scored), 4),
            "mrr": round(reciprocal, 4),
            "avg_top1_score": round(statistics.fmean([r["top1_score"] for r in scored]), 4),
        }

    categories = sorted({r["category"] for r in rows})
    report = {
        "top_k": top_k,
        "overall": summarize(rows),
        "by_category": {c: summarize([r for r in rows if r["category"] == c]) for c in categories},
    }

    refused = [r for r in rows if r["should_refuse"]]
    if refused:
        scores = sorted(r["top1_score"] for r in refused)
        report["refusal_pool"] = {
            "n": len(refused),
            "top1_score_min": round(scores[0], 4),
            "top1_score_median": round(statistics.median(scores), 4),
            "top1_score_max": round(scores[-1], 4),
            "note": "拒答正确性需在答案层评估（LLM 阶段）；此处仅给出负样本的分数分布，用于设定拒答阈值",
        }

    misses = [r for r in rows if not r["should_refuse"] and not r["hit_rank"]]
    report["misses"] = misses[:8]
    report["rows"] = rows
    return report


def print_report(report: dict) -> None:
    overall = report["overall"]
    k = report["top_k"]
    print(f"\n检索评测（top-k={k}）")
    print("-" * 62)
    print(f"{'类别':<10}{'用例':>6}{f'Recall@{k}':>12}{'MRR':>10}{'top1均分':>12}")
    for category, stats in report["by_category"].items():
        if not stats.get("n"):
            continue
        print(f"{category:<10}{stats['n']:>6}{stats['recall_at_k']:>12.3f}{stats['mrr']:>10.3f}"
              f"{stats['avg_top1_score']:>12.3f}")
    print("-" * 62)
    print(f"{'总体':<10}{overall['n']:>6}{overall['recall_at_k']:>12.3f}{overall['mrr']:>10.3f}"
          f"{overall['avg_top1_score']:>12.3f}")
    if "refusal_pool" in report:
        pool = report["refusal_pool"]
        print(f"\n拒答负样本 {pool['n']} 条：top1 分数 min={pool['top1_score_min']} "
              f"median={pool['top1_score_median']} max={pool['top1_score_max']}")
    if report["misses"]:
        print(f"\n未召回样例（前 {len(report['misses'])} 条，badcase 分析用）：")
        for miss in report["misses"]:
            print(f"  · [{miss['category']}] {miss['question']}  → top1={miss['top1_title']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 检索评测（离线）")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-recall", type=float, default=0.0,
                        help="设置后作为 CI 门禁：低于该阈值返回非 0 退出码")
    args = parser.parse_args()

    golden = load_golden(Path(args.golden))
    try:
        from app.rag.service import get_rag_service
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"无法导入检索服务（先完成 W3-1/W3-3）：{exc}") from exc

    service = get_rag_service()
    if not getattr(service, "available", True):
        raise SystemExit("检索服务不可用：请先执行 .venv/bin/python scripts/build_corpus.py 生成语料")

    report = evaluate(service, golden, args.top_k)
    print_report(report)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {out_path}")

    recall = report["overall"]["recall_at_k"]
    if args.min_recall and recall < args.min_recall:
        print(f"\n❌ Recall@{args.top_k}={recall} 低于门禁 {args.min_recall}")
        return 1
    print(f"\n✅ Recall@{args.top_k}={recall}  MRR={report['overall']['mrr']}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
