"""W3-3 基线档测试：分词 / BM25 排序 / RagService 降级与过滤。

全部离线：不连 MySQL、不调用模型、不依赖网络；语料用临时 JSONL fixture。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.bm25 import DEFAULT_B, DEFAULT_K1, BM25Index
from app.rag.service import RagService, get_rag_service, reset_rag_service
from app.rag.tokenizer import tokenize
from scripts.build_corpus import build_chunks, write_jsonl

RESULT_KEYS = {
    "chunk_id",
    "text",
    "source_type",
    "title",
    "book_id",
    "category",
    "url",
    "score",
}


# ----------------------------------------------------------------------
# fixture
# ----------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_service_singleton() -> None:
    """每个用例都从干净的单例开始，避免 service 状态跨用例污染。"""
    reset_rag_service()
    yield
    reset_rag_service()


@pytest.fixture
def sample_chunks() -> list[dict]:
    """7 块覆盖 rule / faq / 三类 book 的语义语料。"""
    return [
        {
            "id": "rule:1:2",
            "text": "逾期与罚金：图书超过应还日期未还即视为逾期，逾期罚金按每天 0.20 元累计，"
            "从应还日次日开始计算，单笔罚金封顶 20.00 元。",
            "source_type": "rule",
            "book_id": None,
            "title": "借阅规则 · 逾期罚金",
            "category": "借阅规则",
            "url": None,
            "chunk_no": 2,
        },
        {
            "id": "rule:1:4",
            "text": "取书与取消：预约图书到馆后为读者保留 3 天，请在保留期内到馆取书，超时未取会自动取消。",
            "source_type": "rule",
            "book_id": None,
            "title": "借阅规则 · 取书与取消",
            "category": "借阅规则",
            "url": None,
            "chunk_no": 4,
        },
        {
            "id": "faq:0",
            "text": "问：第一次借书要怎么操作？\n答：登录后进入图书详情页点击借阅即可。",
            "source_type": "faq",
            "book_id": None,
            "title": "第一次借书要怎么操作？",
            "category": "常见问题",
            "url": None,
            "chunk_no": 0,
        },
        {
            "id": "faq:4",
            "text": "问：罚金怎么交？\n答：归还图书时系统自动结算，可在「我的借阅」中查看并结清。",
            "source_type": "faq",
            "book_id": None,
            "title": "罚金怎么交？",
            "category": "常见问题",
            "url": None,
            "chunk_no": 0,
        },
        {
            "id": "book:262",
            "text": "《三体》 作者：刘慈欣 | 分类：文学 | 出版社：重庆出版社 | "
            "库存：可借 2/3 | 简介：科幻小说，讲述地球文明与三体文明的接触。",
            "source_type": "book",
            "book_id": 262,
            "title": "三体",
            "category": "文学",
            "url": "/books?id=262",
            "chunk_no": 0,
        },
        {
            "id": "book:400",
            "text": "《清朝十二帝》 作者：阎崇年 | 分类：历史 | 出版社：中华书局 | "
            "库存：可借 1/1 | 简介：讲述清朝历史与人物，适合历史爱好者阅读。",
            "source_type": "book",
            "book_id": 400,
            "title": "清朝十二帝",
            "category": "历史",
            "url": "/books?id=400",
            "chunk_no": 0,
        },
        {
            "id": "book:1",
            "text": "《Java编程思想》 作者：Bruce Eckel | 分类：计算机 | 出版社：机械工业出版社 | "
            "库存：可借 5/5 | 简介：系统学习Java核心知识。",
            "source_type": "book",
            "book_id": 1,
            "title": "Java编程思想",
            "category": "计算机",
            "url": "/books?id=1",
            "chunk_no": 0,
        },
    ]


@pytest.fixture
def bm25_index(sample_chunks: list[dict]) -> BM25Index:
    return BM25Index.build(sample_chunks)


@pytest.fixture
def corpus_file(tmp_path: Path, sample_chunks: list[dict]) -> Path:
    output = tmp_path / "knowledge.jsonl"
    write_jsonl(sample_chunks, output)
    return output


# ----------------------------------------------------------------------
# 1. tokenizer
# ----------------------------------------------------------------------
def test_tokenizer_chinese_unigram_and_bigram() -> None:
    tokens = tokenize("清朝历史")
    for unigram in ("清", "朝", "历", "史"):
        assert unigram in tokens
    for bigram in ("清朝", "朝历", "历史"):
        assert bigram in tokens


def test_tokenizer_english_words_and_digits() -> None:
    tokens = tokenize("Java 21 SpringBoot")
    assert "java" in tokens
    assert "21" in tokens
    assert "springboot" in tokens
    assert "Java" not in tokens  # 统一小写


def test_tokenizer_fullwidth_normalization() -> None:
    assert "java" in tokenize("ＪＡＶＡ")


def test_tokenizer_stopwords() -> None:
    tokens = tokenize("这个书怎么借")
    assert "这个" not in tokens
    assert "怎么" not in tokens
    assert "书" not in tokens  # 泛化单字停用词
    assert "借" in tokens  # 有检索意义的单字保留
    assert "的" not in tokens


def test_tokenizer_english_stopwords() -> None:
    tokens = tokenize("the book of java")
    assert "the" not in tokens
    assert "of" not in tokens
    assert "book" in tokens
    assert "java" in tokens


def test_tokenizer_empty_and_punctuation_only() -> None:
    assert tokenize("") == []
    assert tokenize("   ") == []
    assert tokenize("，。！？") == []


def test_tokenizer_mixed_chinese_english() -> None:
    tokens = tokenize("Linux内核")
    assert "linux" in tokens
    assert "内核" in tokens


# ----------------------------------------------------------------------
# 2. BM25 索引与排序语义
# ----------------------------------------------------------------------
def test_index_build_size_and_params(sample_chunks: list[dict]) -> None:
    index = BM25Index.build(sample_chunks)
    assert index.size == len(sample_chunks)
    assert index.vocabulary_size > 0
    assert index.avg_doc_length > 0
    assert index.k1 == DEFAULT_K1 == 1.5
    assert index.b == DEFAULT_B == 0.75


def test_rule_query_hits_rule_chunk(bm25_index: BM25Index) -> None:
    hits = bm25_index.search("逾期罚金怎么算", top_k=3)
    assert hits
    assert hits[0]["chunk"]["source_type"] == "rule"
    assert "逾期罚金" in hits[0]["chunk"]["text"]
    assert hits[0]["rank"] == 1


def test_history_query_hits_history_book(bm25_index: BM25Index) -> None:
    hits = bm25_index.search("有没有讲清朝历史的书", top_k=3)
    assert hits
    top = hits[0]["chunk"]
    assert top["source_type"] == "book"
    assert top["category"] == "历史"
    assert top["title"] == "清朝十二帝"
    assert top["book_id"] == 400


def test_exact_title_query_hits_three_body(bm25_index: BM25Index) -> None:
    hits = bm25_index.search("三体", top_k=3)
    assert hits
    assert hits[0]["chunk"]["id"] == "book:262"
    assert hits[0]["chunk"]["title"] == "三体"
    assert hits[0]["chunk"]["book_id"] == 262
    assert hits[0]["score"] > 0


def test_irrelevant_query_low_score_or_empty(bm25_index: BM25Index) -> None:
    """无关 query（"今天天气"）最多靠「天」单字弱命中，分数必须远低于真实问题。

    真实语料里「每天」「三天」等词会让无关 query 拿到一点分数（BM25 无法理解语义），
    因此这里断言「空 或 低分」；W3-5 会用 top1 分数阈值做拒答，正是依赖这一性质。
    """
    strong = bm25_index.search("逾期罚金怎么算", top_k=1)
    assert strong
    hits = bm25_index.search("今天天气", top_k=5)
    assert not hits or hits[0]["score"] < strong[0]["score"] * 0.3


def test_results_sorted_and_ranked(bm25_index: BM25Index) -> None:
    hits = bm25_index.search("借书 历史 罚金", top_k=5)
    assert hits
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert [hit["rank"] for hit in hits] == list(range(1, len(hits) + 1))


def test_top_k_truncation(bm25_index: BM25Index) -> None:
    hits = bm25_index.search("历史 借书 罚金", top_k=2)
    assert len(hits) == 2


def test_source_types_filter(bm25_index: BM25Index) -> None:
    book_hits = bm25_index.search("历史 三体 罚金", top_k=5, source_types=["book"])
    assert book_hits
    assert all(hit["chunk"]["source_type"] == "book" for hit in book_hits)

    rule_hits = bm25_index.search("罚金 逾期", top_k=5, source_types=["rule"])
    assert rule_hits
    assert all(hit["chunk"]["source_type"] == "rule" for hit in rule_hits)

    # 过滤发生在排序前：top_k 不会被其它来源占满
    faq_hits = bm25_index.search("逾期罚金", top_k=5, source_types=["faq"])
    assert faq_hits
    assert all(hit["chunk"]["source_type"] == "faq" for hit in faq_hits)


def test_search_edge_cases(bm25_index: BM25Index) -> None:
    assert bm25_index.search("", top_k=5) == []
    assert bm25_index.search("   ", top_k=5) == []
    assert bm25_index.search("三体", top_k=0) == []
    assert bm25_index.search("三体", top_k=-1) == []
    assert bm25_index.search("三体", top_k=5, source_types=[]) == []


def test_empty_index_search_returns_empty() -> None:
    index = BM25Index.build([])
    assert index.size == 0
    assert index.search("三体", top_k=5) == []


# ----------------------------------------------------------------------
# 3. RagService：加载、降级、过滤、单例
# ----------------------------------------------------------------------
def test_service_search_returns_schema(corpus_file: Path) -> None:
    service = RagService(corpus_file)
    hits = service.search("三体", top_k=3)
    assert hits
    assert set(hits[0]) == RESULT_KEYS
    assert hits[0]["chunk_id"] == "book:262"
    assert hits[0]["source_type"] == "book"
    assert hits[0]["book_id"] == 262
    assert hits[0]["url"] == "/books?id=262"
    assert isinstance(hits[0]["score"], float)


def test_service_missing_corpus_degrades_to_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service = RagService(tmp_path / "missing.jsonl")
    with caplog.at_level("WARNING", logger="ai-service.rag.service"):
        hits = service.search("三体")  # 不抛异常
    assert hits == []
    assert service.available is False
    assert "build_corpus.py" in caplog.text  # 日志里提示先跑构建脚本


def test_service_top_k_truncation(corpus_file: Path) -> None:
    service = RagService(corpus_file)
    assert len(service.search("历史 借书 罚金", top_k=2)) <= 2
    assert service.search("三体", top_k=0) == []


def test_service_source_types_filter(corpus_file: Path) -> None:
    service = RagService(corpus_file)
    hits = service.search("罚金 逾期", top_k=5, source_types=["faq"])
    assert hits
    assert all(hit["source_type"] == "faq" for hit in hits)


def test_service_blank_query_returns_empty(corpus_file: Path) -> None:
    service = RagService(corpus_file)
    assert service.search("   ") == []


def test_service_stats(corpus_file: Path) -> None:
    service = RagService(corpus_file)
    stats = service.stats()
    assert stats["available"] is True
    assert stats["chunk_count"] == 7
    assert stats["vocabulary_size"] > 0
    assert stats["corpus_path"] == str(corpus_file)
    assert stats["load_error"] is None


def test_get_rag_service_singleton_and_reset(
    corpus_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_RAG_CORPUS_PATH", str(corpus_file))
    first = reset_rag_service()  # 重置后按环境变量路径构造
    assert get_rag_service() is first
    assert first.search("三体", top_k=1)

    second = reset_rag_service()
    assert second is not first
    assert get_rag_service() is second


def test_service_reload_picks_up_new_corpus(corpus_file: Path) -> None:
    service = RagService(corpus_file)
    assert service.search("三体", top_k=1)

    java_only = [
        {
            "id": "book:1",
            "text": "《Java编程思想》 作者：Bruce Eckel | 分类：计算机 | 简介：Java 入门经典。",
            "source_type": "book",
            "book_id": 1,
            "title": "Java编程思想",
            "category": "计算机",
            "url": "/books?id=1",
            "chunk_no": 0,
        }
    ]
    write_jsonl(java_only, corpus_file)
    service.reload()  # 语料更新后热重载

    assert service.search("三体", top_k=1) == []
    assert service.search("java", top_k=1)[0]["title"] == "Java编程思想"
