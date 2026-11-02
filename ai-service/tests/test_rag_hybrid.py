"""W3-3b 混合检索测试（全离线：无网络、无真实 embedding / rerank API）。

覆盖：

1. ``RagService`` 三模式：``bm25`` / ``vector`` / ``hybrid`` 的返回结构与分数语义；
2. hybrid = BM25 top-20 + 向量 top-20 → **RRF(k=60)** → top-20，去重后按 RRF 排序；
3. ``top_k`` / ``source_types`` 在融合前后都正确生效；
4. 降级链：向量组件不可用 → bm25；运行时 embedding 失败 → 本次查询降级 bm25；
5. 重排钩子：``app.rag.rerank`` 缺失（task-7 未交付时）→ 跳过且不影响检索；
   模块存在 → 懒加载调用；重排报错/返回异常 → 回退未重排结果；
6. ``search()`` 签名/字段向后兼容（bm25 模式字段集与 W3-3 基线完全一致）。
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
import types
from pathlib import Path

import pytest

from app.rag.bm25 import BM25Index
from app.rag.embedding import EmbeddingError, EmbeddingProvider
from app.rag.rag_config import RagConfig
from app.rag.service import DEFAULT_TOP_K, RagService
from app.rag.vector_store import NumpyVectorStore

LOGGER = "ai-service.rag.service"
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
RETRIEVAL_KEYS = {"mode", "bm25_rank", "vector_rank", "rrf_score"}

QUERY = "逾期罚金怎么算"


# ----------------------------------------------------------------------
# 受控语料 + 受控向量/查询向量
# ----------------------------------------------------------------------
def _chunk(chunk_id: str, text: str, source_type: str, title: str, **extra) -> dict:
    return {
        "id": chunk_id,
        "text": text,
        "source_type": source_type,
        "book_id": extra.get("book_id"),
        "title": title,
        "category": extra.get("category", "测试"),
        "url": extra.get("url"),
        "chunk_no": 0,
    }


@pytest.fixture
def chunks() -> list[dict]:
    return [
        _chunk("rule:penalty-a", "逾期罚金：图书逾期后按每天 0.20 元累计罚金", "rule", "逾期罚金"),
        _chunk("rule:penalty-b", "罚金缴纳：归还图书时系统自动结算罚金", "rule", "罚金缴纳"),
        _chunk("faq:first-borrow", "第一次借书要怎么操作？登录后点击借阅", "faq", "第一次借书"),
        _chunk("faq:reserve", "图书预约后保留三天，超时自动取消", "faq", "预约保留"),
        _chunk("book:1", "《Java编程思想》 作者：Bruce Eckel", "book", "Java编程思想", book_id=1),
    ]


# 向量方向受控：让 hybrid 用例的向量排名可精确断言
CHUNK_VECTORS: dict[str, list[float]] = {
    "rule:penalty-a": [0.6, 0.4, 0.0],  # 与 query 余弦 ≈ 0.832 → 向量 rank 4
    "rule:penalty-b": [1.0, 0.0, 0.0],  # 余弦 1.0 → 向量 rank 1
    "faq:first-borrow": [0.8, 0.2, 0.0],  # ≈ 0.970 → rank 3
    "faq:reserve": [0.9, 0.1, 0.0],  # ≈ 0.994 → rank 2
    "book:1": [0.0, 0.0, 1.0],  # 0.0 → rank 5
}
QUERY_VECTOR = [1.0, 0.0, 0.0]


class StubEmbeddingProvider(EmbeddingProvider):
    """离线 embedding 替身：查询向量可精确控制，可模拟运行时故障。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    @property
    def name(self) -> str:
        return "stub"

    @property
    def dimension(self) -> int:
        return 3

    @property
    def model(self) -> str:
        return "stub-3"

    def encode(self, texts, batch_size=None):  # noqa: ANN001 - 与基类一致
        if self.fail:
            raise EmbeddingError("stub embedding 故意故障")
        return [list(QUERY_VECTOR) for _ in texts]


@pytest.fixture
def corpus_file(tmp_path: Path, chunks: list[dict]) -> Path:
    path = tmp_path / "knowledge.jsonl"
    path.write_text(
        "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def vector_store(chunks: list[dict]) -> NumpyVectorStore:
    return NumpyVectorStore.build(
        chunks,
        [CHUNK_VECTORS[chunk["id"]] for chunk in chunks],
        provider="stub",
        model="stub-3",
        corpus_sha256="sha-test",
    )


def _config(**overrides) -> RagConfig:
    base = dict(
        retrieval_mode="hybrid",
        candidate_k=20,
        rrf_k=60,
        rerank_enabled=False,
        embedding_provider="stub",
        embedding_dimension=3,
    )
    base.update(overrides)
    return RagConfig(**base)


def _service(
    corpus_file: Path,
    chunks: list[dict],
    store: NumpyVectorStore,
    *,
    config: RagConfig | None = None,
    provider: EmbeddingProvider | None = None,
) -> RagService:
    return RagService(
        corpus_file,
        config=config or _config(),
        embedding_provider=provider if provider is not None else StubEmbeddingProvider(),
        vector_store=store,
    )


def _bm25_reference(chunks: list[dict], query: str, source_types: list[str] | None = None) -> list[dict]:
    return BM25Index.build(chunks).search(query, top_k=20, source_types=source_types)


def _ids(results: list[dict]) -> list[str]:
    return [result["chunk_id"] for result in results]


# ----------------------------------------------------------------------
# 1. hybrid：RRF 融合
# ----------------------------------------------------------------------
def test_hybrid_fuses_bm25_and_vector_with_rrf(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore
) -> None:
    service = _service(corpus_file, chunks, vector_store)
    results = service.search(QUERY, top_k=5)

    assert len(results) == 5
    assert _ids(results) == [
        "rule:penalty-b",  # bm25 rank2 + vector rank1 → RRF 最高
        "rule:penalty-a",  # bm25 rank1 + vector rank4
        "faq:reserve",  # 仅向量 rank2
        "faq:first-borrow",  # 仅向量 rank3
        "book:1",  # 仅向量 rank5
    ]
    # 分数 = RRF 分；retrieval 记录两条通道的原始排名
    penalty_b = results[0]
    assert set(penalty_b) == RESULT_KEYS | {"retrieval"}
    assert set(penalty_b["retrieval"]) == RETRIEVAL_KEYS
    assert penalty_b["retrieval"] == {
        "mode": "hybrid",
        "bm25_rank": 2,
        "vector_rank": 1,
        "rrf_score": pytest.approx(1 / 62 + 1 / 61),
    }
    # score 字段统一保留 6 位小数（与 bm25/vector 模式一致）
    assert penalty_b["score"] == pytest.approx(round(1 / 62 + 1 / 61, 6))

    penalty_a = results[1]["retrieval"]
    assert penalty_a["bm25_rank"] == 1
    assert penalty_a["vector_rank"] == 4
    assert penalty_a["rrf_score"] == pytest.approx(1 / 61 + 1 / 64)

    # 去重：每个 chunk 只出现一次
    assert len(set(_ids(results))) == len(results)
    # 排序单调不增
    scores = [result["score"] for result in results]
    assert scores == sorted(scores, reverse=True)
    assert service.retrieval_mode == "hybrid"


def test_hybrid_top_k_truncates_after_fusion(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore
) -> None:
    service = _service(corpus_file, chunks, vector_store)
    full = service.search(QUERY, top_k=5)
    assert _ids(service.search(QUERY, top_k=2)) == _ids(full)[:2]
    assert len(service.search(QUERY, top_k=1)) == 1


def test_hybrid_source_types_filter_applies_to_both_channels(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore
) -> None:
    service = _service(corpus_file, chunks, vector_store)
    results = service.search(QUERY, top_k=5, source_types=["rule"])
    assert [result["source_type"] for result in results] == ["rule", "rule"]
    # 只剩两个 rule 块时：bm25 排名与向量排名恰好对调，RRF 分相同，
    # 用 bm25_rank 做确定性 tie-break → penalty-a（bm25 rank1）在前。
    assert _ids(results) == ["rule:penalty-a", "rule:penalty-b"]
    assert results[0]["retrieval"]["rrf_score"] == results[1]["retrieval"]["rrf_score"]


def test_hybrid_uses_candidate_k_from_config(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore
) -> None:
    service = _service(
        corpus_file,
        chunks,
        vector_store,
        config=_config(candidate_k=2, rrf_k=60),
    )
    results = service.search(QUERY, top_k=2)
    assert len(results) == 2  # 融合候选上限 candidate_k=2（且 top_k=2）
    assert _ids(results) == ["rule:penalty-b", "rule:penalty-a"]
    # top_k 大于 candidate_k 时按 max(candidate_k, top_k) 取候选，避免返回条数不足
    assert len(service.search(QUERY, top_k=4)) == 4


# ----------------------------------------------------------------------
# 2. bm25 模式：与 W3-3 基线完全一致（字段集不变）
# ----------------------------------------------------------------------
def test_bm25_mode_matches_baseline_and_has_no_retrieval_field(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore
) -> None:
    service = _service(
        corpus_file,
        chunks,
        vector_store,
        config=_config(retrieval_mode="bm25"),
    )
    results = service.search(QUERY, top_k=5)
    reference = _bm25_reference(chunks, QUERY)
    assert _ids(results) == [hit["chunk"]["id"] for hit in reference]
    for result, hit in zip(results, reference):
        assert set(result) == RESULT_KEYS  # bm25 模式不多任何字段
        assert result["score"] == pytest.approx(round(hit["score"], 6))
    assert service.retrieval_mode == "bm25"


def test_bm25_mode_source_types(corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore) -> None:
    service = _service(corpus_file, chunks, vector_store, config=_config(retrieval_mode="bm25"))
    results = service.search("罚金 借书", top_k=5, source_types=["faq"])
    assert results
    assert all(result["source_type"] == "faq" for result in results)


# ----------------------------------------------------------------------
# 3. vector 模式
# ----------------------------------------------------------------------
def test_vector_mode_returns_semantic_order(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore
) -> None:
    service = _service(
        corpus_file,
        chunks,
        vector_store,
        config=_config(retrieval_mode="vector"),
    )
    results = service.search(QUERY, top_k=3)
    assert _ids(results) == ["rule:penalty-b", "faq:reserve", "faq:first-borrow"]
    for result in results:
        assert result["retrieval"]["mode"] == "vector"
        assert result["retrieval"]["bm25_rank"] is None
        assert result["retrieval"]["vector_rank"] in (1, 2, 3)
        assert result["retrieval"]["rrf_score"] is None
    assert results[0]["score"] == pytest.approx(1.0)
    assert service.retrieval_mode == "vector"


# ----------------------------------------------------------------------
# 4. 降级链
# ----------------------------------------------------------------------
def test_hybrid_degrades_to_bm25_when_embedding_provider_missing(
    corpus_file: Path,
    chunks: list[dict],
    vector_store: NumpyVectorStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = RagService(
        corpus_file,
        config=_config(),
        embedding_provider=None,  # 显式禁用
        vector_store=vector_store,
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        results = service.search(QUERY, top_k=5)
    assert results
    assert all("retrieval" not in result for result in results)
    assert _ids(results) == [hit["chunk"]["id"] for hit in _bm25_reference(chunks, QUERY)]
    assert service.retrieval_mode == "bm25"
    assert "降级" in caplog.text
    stats = service.stats()
    assert stats["configured_mode"] == "hybrid"
    assert stats["effective_mode"] == "bm25"
    assert stats["vector_error"]


def test_hybrid_degrades_when_vector_index_missing(
    corpus_file: Path, chunks: list[dict], tmp_path: Path
) -> None:
    config = _config(embeddings_dir=tmp_path / "empty-embeddings")  # 索引目录为空
    service = RagService(corpus_file, config=config)
    results = service.search(QUERY, top_k=5)
    assert results  # 仍能回答（走 BM25）
    assert all("retrieval" not in result for result in results)
    assert service.retrieval_mode == "bm25"


def test_vector_mode_runtime_embedding_failure_degrades_to_bm25(
    corpus_file: Path,
    chunks: list[dict],
    vector_store: NumpyVectorStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = StubEmbeddingProvider(fail=True)
    service = _service(
        corpus_file,
        chunks,
        vector_store,
        config=_config(retrieval_mode="vector"),
        provider=provider,
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        results = service.search(QUERY, top_k=5)
    assert _ids(results) == [hit["chunk"]["id"] for hit in _bm25_reference(chunks, QUERY)]
    assert "运行时失败" in caplog.text


def test_hybrid_runtime_embedding_failure_degrades_to_bm25(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore
) -> None:
    service = _service(
        corpus_file,
        chunks,
        vector_store,
        config=_config(),
        provider=StubEmbeddingProvider(fail=True),
    )
    results = service.search(QUERY, top_k=5)
    assert _ids(results) == [hit["chunk"]["id"] for hit in _bm25_reference(chunks, QUERY)]
    assert all("retrieval" not in result for result in results)


# ----------------------------------------------------------------------
# 5. 重排钩子（task-7 模块缺失 / 存在 / 失败）
# ----------------------------------------------------------------------
class _ReverseReranker:
    """把候选顺序反转（模拟一个"有效但排序不同"的重排器）。"""

    def rerank(self, query: str, candidates, top_k: int):
        return list(reversed(candidates))[:top_k]


def _install_fake_rerank_module(monkeypatch: pytest.MonkeyPatch, reranker) -> None:
    module = types.ModuleType("app.rag.rerank")
    module.get_reranker = lambda *args, **kwargs: reranker
    monkeypatch.setitem(sys.modules, "app.rag.rerank", module)


def test_rerank_hook_skipped_when_module_missing(
    corpus_file: Path,
    chunks: list[dict],
    vector_store: NumpyVectorStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # task-7 的 rerank 模块还没交付：检索必须照常工作（ImportError 被吞掉）
    baseline = _service(corpus_file, chunks, vector_store).search(QUERY, top_k=5)
    monkeypatch.setitem(sys.modules, "app.rag.rerank", None)
    service = _service(corpus_file, chunks, vector_store, config=_config(rerank_enabled=True))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        results = service.search(QUERY, top_k=5)
    assert _ids(results) == _ids(baseline)
    assert "重排" in caplog.text


def test_rerank_hook_applied_when_module_present(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _service(corpus_file, chunks, vector_store).search(QUERY, top_k=5)
    _install_fake_rerank_module(monkeypatch, _ReverseReranker())
    service = _service(corpus_file, chunks, vector_store, config=_config(rerank_enabled=True))
    results = service.search(QUERY, top_k=5)
    assert _ids(results) == list(reversed(_ids(baseline)))
    # 重排只改顺序，不破坏字段结构
    for result in results:
        assert result["retrieval"]["mode"] == "hybrid"


def test_rerank_hook_accepts_id_only_output(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _IdOnlyReranker:
        def rerank(self, query: str, candidates, top_k: int):
            return [candidate["chunk_id"] for candidate in reversed(candidates)][:top_k]

    baseline = _service(corpus_file, chunks, vector_store).search(QUERY, top_k=5)
    _install_fake_rerank_module(monkeypatch, _IdOnlyReranker())
    service = _service(corpus_file, chunks, vector_store, config=_config(rerank_enabled=True))
    assert _ids(service.search(QUERY, top_k=5)) == list(reversed(_ids(baseline)))


def test_rerank_hook_accepts_rerank_score(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _ScoringReranker:
        def rerank(self, query: str, candidates, top_k: int):
            return [
                {"chunk_id": candidate["chunk_id"], "rerank_score": 0.99 - index * 0.1}
                for index, candidate in enumerate(candidates[:top_k])
            ]

    _install_fake_rerank_module(monkeypatch, _ScoringReranker())
    service = _service(corpus_file, chunks, vector_store, config=_config(rerank_enabled=True))
    results = service.search(QUERY, top_k=2)
    assert [result["score"] for result in results] == [0.99, 0.89]


def test_rerank_failure_falls_back_to_unranked_results(
    corpus_file: Path,
    chunks: list[dict],
    vector_store: NumpyVectorStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    baseline = _service(corpus_file, chunks, vector_store).search(QUERY, top_k=5)

    class _BrokenReranker:
        def rerank(self, query: str, candidates, top_k: int):
            raise RuntimeError("rerank api down")

    _install_fake_rerank_module(monkeypatch, _BrokenReranker())
    service = _service(corpus_file, chunks, vector_store, config=_config(rerank_enabled=True))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        results = service.search(QUERY, top_k=5)
    assert _ids(results) == _ids(baseline)  # 回退未重排顺序
    assert "重排失败" in caplog.text


def test_rerank_not_called_in_bm25_mode(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _ExplodingReranker:
        def rerank(self, query: str, candidates, top_k: int):  # pragma: no cover - 不应被调用
            raise AssertionError("bm25 模式不应触发重排")

    _install_fake_rerank_module(monkeypatch, _ExplodingReranker())
    service = _service(
        corpus_file,
        chunks,
        vector_store,
        config=_config(retrieval_mode="bm25", rerank_enabled=True),
    )
    assert service.search(QUERY, top_k=5)  # 不抛异常


# ----------------------------------------------------------------------
# 6. 兼容性 / 运维辅助
# ----------------------------------------------------------------------
def test_search_signature_backward_compatible() -> None:
    parameters = inspect.signature(RagService.search).parameters
    assert list(parameters) == ["self", "query", "top_k", "source_types"]
    assert parameters["top_k"].default == DEFAULT_TOP_K == 5
    assert parameters["source_types"].default is None


def test_search_edge_cases(corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore) -> None:
    service = _service(corpus_file, chunks, vector_store)
    assert service.search("") == []
    assert service.search("   ") == []
    assert service.search(QUERY, top_k=0) == []
    assert service.search(QUERY, top_k=-1) == []


def test_stats_reports_vector_state(corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore) -> None:
    service = _service(corpus_file, chunks, vector_store)
    stats = service.stats()
    assert stats["available"] is True
    assert stats["configured_mode"] == "hybrid"
    assert stats["effective_mode"] == "hybrid"
    assert stats["vector_store"] == "numpy"
    assert stats["vector_count"] == len(chunks)
    assert stats["vector_dimension"] == 3
    assert stats["vector_error"] is None
    assert stats["rerank_enabled"] is False


def test_reload_keeps_injected_components_and_works(
    corpus_file: Path, chunks: list[dict], vector_store: NumpyVectorStore
) -> None:
    service = _service(corpus_file, chunks, vector_store)
    before = service.search(QUERY, top_k=3)
    service.reload()
    after = service.search(QUERY, top_k=3)
    assert _ids(after) == _ids(before)
