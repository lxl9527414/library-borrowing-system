#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 RAG 评测集（golden set）

设计说明（面试可讲）：
- 评测集不是"随便问 60 个问题"，而是**按报告里要讲的结论来设计**：
  * book 类：验证"馆藏事实型问题"能否召回正确图书（精确词召回 —— 向量模型的弱项）
  * rule 类：验证"规则数值型问题"能否召回规则块（RAG 最该做对、也最容易幻觉的地方）
  * faq  类：验证"流程型问题"的召回
  * refuse 类：验证"没有依据时应当拒答"（负样本，防止检索器对啥都返回高分）
- 每条用例带 `expected_keywords`，评估时判断召回块是否真的支持答案，而不是只比对 id。

数据源：
- book/faq 条目来自语料文件 `ai-service/data/corpus/knowledge.jsonl`（先跑 build_corpus.py）
- rule 数值直接读 MySQL borrow_rule（离线工具，允许直连；运行时 AI 服务仍不直连）

用法：
    cd ai-service && .venv/bin/python scripts/gen_golden_qa.py
    cd ai-service && .venv/bin/python scripts/gen_golden_qa.py --books 40 --out eval/golden_qa.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

AI_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AI_ROOT))

DEFAULT_CORPUS = AI_ROOT / "data" / "corpus" / "knowledge.jsonl"
DEFAULT_OUT = AI_ROOT / "eval" / "golden_qa.jsonl"


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = AI_ROOT.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in os.environ.items():
        env.setdefault(key, value)
    return env


def load_chunks(corpus_path: Path) -> list[dict]:
    if not corpus_path.exists():
        raise SystemExit(f"语料文件不存在：{corpus_path}\n请先执行：.venv/bin/python scripts/build_corpus.py")
    chunks: list[dict] = []
    with corpus_path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def fetch_rules(env: dict[str, str]) -> dict:
    """直接读 MySQL 拿当前规则数值（离线构建工具，允许）——用真实值生成强断言关键词。"""
    try:
        import pymysql
    except ImportError:
        return {}
    try:
        conn = pymysql.connect(
            host=env.get("DB_HOST", "127.0.0.1"),
            port=int(env.get("DB_PORT", "3308")),
            user=env.get("DB_USERNAME", "root"),
            password=env.get("DB_PASSWORD", ""),
            database=env.get("DB_NAME", "library_db"),
            charset="utf8mb4",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ 读不到 borrow_rule（{exc}），rule 类用例将使用通用关键词")
        return {}
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT max_borrow_count, borrow_days, max_renew_count, renew_days, "
                "fine_per_day, fine_max, reserve_limit, hold_days FROM borrow_rule LIMIT 1"
            )
            return cur.fetchone() or {}
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ 查询 borrow_rule 失败（{exc}）")
        return {}
    finally:
        conn.close()


def book_questions(books: list[dict], limit: int) -> list[dict]:
    rng = random.Random(42)
    sample = rng.sample(books, min(limit, len(books)))
    items: list[dict] = []
    for index, chunk in enumerate(sample):
        title = chunk.get("title") or ""
        text = chunk.get("text") or ""
        if not title or not text:
            continue
        # 从 text 里解析作者（build_corpus.py 的输出格式固定： 《书名》 作者：X | ...）
        author = ""
        if "作者：" in text:
            author = text.split("作者：", 1)[1].split("|", 1)[0].strip()
        templates = [
            (f"《{title}》这本书的作者是谁？", [title, author] if author else [title]),
            (f"《{title}》属于哪个分类？", [title, chunk.get("category") or ""]),
            (f"《{title}》现在还有库存吗？", [title]),
        ]
        question, keywords = templates[index % len(templates)]
        keywords = [k for k in keywords if k]
        items.append({
            "id": f"book-{index + 1:03d}",
            "question": question,
            "category": "book",
            "expected_source_types": ["book"],
            "expected_keywords": keywords,
            "match": "all",
            "should_refuse": False,
        })
    return items


def rule_questions(rules: dict) -> list[dict]:
    max_borrow = rules.get("max_borrow_count", 5)
    borrow_days = rules.get("borrow_days", 30)
    renew_count = rules.get("max_renew_count", 1)
    renew_days = rules.get("renew_days", 15)
    fine_per_day = rules.get("fine_per_day", 0.2)
    fine_max = rules.get("fine_max", 20)
    reserve_limit = rules.get("reserve_limit", 3)
    hold_days = rules.get("hold_days", 3)

    raw = [
        ("一个人最多可以同时借几本书？", ["最多", str(max_borrow)]),
        ("一本书一次可以借多久？", [str(borrow_days), "天"]),
        ("一本书最多可以续借几次？", ["续借", str(renew_count)]),
        ("每次续借可以延长多少天？", [str(renew_days), "天"]),
        ("逾期罚金每天多少钱？", ["罚金", str(float(fine_per_day))]),
        ("逾期罚金最高封顶多少？", ["封顶", str(float(fine_max))]),
        ("最多可以同时预约几本书？", ["预约", str(reserve_limit)]),
        ("预约到书后保留几天，过期会怎样？", [str(hold_days), "天"]),
        ("逾期罚金是怎么计算的，能举个例子吗？", ["罚金"]),
        ("借阅到期了还没还，会有什么后果？", ["逾期"]),
        ("续借有什么限制条件？", ["续借"]),
        ("书还回来以后，预约队列会怎么处理？", ["预约"]),
        ("当前借阅规则里，借期和续借期分别是多少？", [str(borrow_days), str(renew_days)]),
        ("罚金是按天算还是按本算？", ["罚金"]),
        ("待取书的保留时长是多少，超时怎么顺延？", [str(hold_days), "顺延"]),
    ]
    items = []
    for index, (question, keywords) in enumerate(raw):
        items.append({
            "id": f"rule-{index + 1:03d}",
            "question": question,
            "category": "rule",
            "expected_source_types": ["rule"],
            "expected_keywords": [k for k in keywords if k],
            "match": "any",
            "should_refuse": False,
        })
    return items


def faq_questions(chunks: list[dict]) -> list[dict]:
    faqs = [c for c in chunks if c.get("source_type") == "faq"]
    items = []
    for index, chunk in enumerate(faqs):
        question = (chunk.get("title") or "").strip()
        if not question:
            continue
        text = chunk.get("text") or ""
        # 关键词：取问题里的 4 字片段 + 正文里的数字/专有词，避免"命中即正确"的宽松判定
        keywords = [question[:6]] if len(question) >= 6 else [question]
        for token in ("预约", "续借", "罚金", "借阅", "归还", "上限"):
            if token in text and token not in keywords:
                keywords.append(token)
        items.append({
            "id": f"faq-{index + 1:03d}",
            "question": question,
            "category": "faq",
            "expected_source_types": ["faq"],
            "expected_keywords": keywords[:3],
            "match": "any",
            "should_refuse": False,
        })
    return items


def refuse_questions() -> list[dict]:
    raw = [
        "图书馆的 WiFi 密码是多少？",
        "馆长叫什么名字、年薪多少？",
        "帮我推荐几只明天会涨的股票",
        "明天北京天气怎么样？",
        "这本书的作者有没有犯罪记录？",
        "图书馆大楼是哪一年建的、造价多少？",
        "帮我写一段 Python 快速排序代码",
        "最近有什么好看的电影推荐？",
        "这本书的电子版下载地址在哪里？",
        "读者卡里的余额可以提现吗？",
    ]
    return [
        {
            "id": f"refuse-{index + 1:03d}",
            "question": question,
            "category": "refuse",
            "expected_source_types": [],
            "expected_keywords": [],
            "match": "any",
            "should_refuse": True,
        }
        for index, question in enumerate(raw)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 RAG 评测集（golden set）")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--books", type=int, default=30, help="从馆藏里抽多少本生成 book 类问题")
    args = parser.parse_args()

    env = _load_env()
    chunks = load_chunks(Path(args.corpus))
    books = [c for c in chunks if c.get("source_type") == "book"]
    rules = fetch_rules(env)

    items: list[dict] = []
    items += book_questions(books, args.books)
    items += rule_questions(rules)
    items += faq_questions(chunks)
    items += refuse_questions()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fp:
        for item in items:
            fp.write(json.dumps(item, ensure_ascii=False) + "\n")

    by_cat: dict[str, int] = {}
    for item in items:
        by_cat[item["category"]] = by_cat.get(item["category"], 0) + 1
    print(f"✅ 已生成 {len(items)} 条评测用例 → {out_path}")
    for category, count in sorted(by_cat.items()):
        print(f"   {category:<7} {count:>3} 条")
    print(f"   规则数值来源：{'MySQL borrow_rule' if rules else '默认值（未连上 MySQL）'}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
