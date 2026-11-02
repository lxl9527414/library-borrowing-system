"""W3-2 向量索引测试（全离线）。

覆盖：

1. ``NumpyVectorStore``：归一化余弦、排序/rank、source_types 过滤（排序前过滤）、
   零向量、维度校验、同分稳定排序、保存/加载 roundtrip、损坏文件报错；
2. 索引文件校验：meta schema、chunk 数/chunk id 顺序/维度不一致必须拒绝加载；
3. ``open_vector_store`` 工厂：索引不存在/模型或维度不匹配/语料 sha256 不一致 → None + 告警；
4. ``PgVectorStore``：缺 psycopg / 缺 DSN / 连不上 → ``available=False`` + ``search()=[]``，不抛异常；
5. ``scripts/index_corpus.build_index``：批量编码、幂等跳过、``--force``、语料变更重建、
   进度回调、元数据字段（含语料 sha256）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.rag.embedding import EmbeddingError, FakeEmbeddingProvider
from app.rag.rag_config import RagConfig
from app.rag.vector_store import (
    NumpyVectorStore,
    PgVectorStore,
    VectorStore,
    VectorStoreError,
    open_vector_store,
    read_meta,
)
from scripts.index_corpus import build_index

LOGGER = "ai-service.rag.vector_store"


# ----------------------------------------------------------------------
# fixtures / helpers
# ----------------------------------------------------------------------
def _chunk(chunk_id: str, text: str, source_type: str = "book", **extra) -> dict:
    return {
        "id": chunk_id,
        "text": text,
        "source_type": source_type,
        "book_id": extra.get("book_id"),
        "title": extra.get("title", text[:12]),
        "category": extra.get("category", "测试"),
        "url": extra.get("url"),
        "chunk_no": 0,
    }


@pytest.fixture
def sample_chunks() -> list[dict]:
    return [
        _chunk("rule:1:2", "逾期罚金按每天 0.20 元累计", "rule", title="借阅规则 · 逾期罚金"),
        _chunk("book:1", "《Java编程思想》 作者：Bruce Eckel", "book", book_id=1, title="Java编程思想"),
        _chunk("faq:0", "问：罚金怎么交？答：归还时自动结算", "faq", title="罚金怎么交？"),
    ]


@pytest.fixture
def store(sample_chunks: list[dict]) -> NumpyVectorStore:
    return NumpyVectorStore.build(
        sample_chunks,
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.6, 0.8, 0.0]],
        provider="fake",
        model="fake-hash-3",
        corpus_path="/tmp/knowledge.jsonl",
        corpus_sha256="sha-test",
    )


@pytest.fixture
def indexed_config(tmp_path: Path) -> RagConfig:
    return RagConfig(embedding_provider="fake", embeddings_dir=tmp_path, embedding_dimension=64)


def _provider_stub(*, name: str = "fake", dimension: int = 3, model: str = "fake-hash-3") -> SimpleNamespace:
    return SimpleNamespace(name=name, dimension=dimension, model=model)


def _write_corpus(path: Path, chunks: list[dict]) -> str:
    path.write_text(
        "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks) + "\n",
        encoding="utf-8",
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ----------------------------------------------------------------------
# 1. NumpyVectorStore：检索语义
# ----------------------------------------------------------------------
def test_store_metadata(store: NumpyVectorStore) -> None:
    assert isinstance(store, VectorStore)
    assert store.name == "numpy"
    assert store.size == 3
    assert store.dimension == 3
    assert store.provider_name == "fake"
    assert store.model == "fake-hash-3"
    assert store.corpus_sha256 == "sha-test"


def test_store_search_returns_cosine_order_and_ranks(store: NumpyVectorStore) -> None:
    hits = store.search([1.0, 0.0, 0.0], top_k=3)
    assert [hit["chunk"]["id"] for hit in hits] == ["rule:1:2", "faq:0", "book:1"]
    assert hits[0]["score"] == pytest.approx(1.0)
    assert [hit["rank"] for hit in hits] == [1, 2, 3]
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)

    # 查询本身不归一化：同方向不同长度应得到同一排序
    scaled = store.search([10.0, 0.0, 0.0], top_k=1)
    assert scaled[0]["chunk"]["id"] == "rule:1:2"
    assert scaled[0]["score"] == pytest.approx(1.0)


def test_store_search_source_types_filters_before_top_k(store: NumpyVectorStore) -> None:
    # faq 块与 query 相似度低于 rule，但过滤发生在截断之前，所以 top_k=1 也能取到 faq
    hits = store.search([1.0, 0.0, 0.0], top_k=1, source_types=["faq"])
    assert len(hits) == 1
    assert hits[0]["chunk"]["source_type"] == "faq"
    assert hits[0]["chunk"]["id"] == "faq:0"


def test_store_search_unknown_source_type_returns_empty(store: NumpyVectorStore) -> None:
    assert store.search([1.0, 0.0, 0.0], top_k=3, source_types=["not-exist"]) == []


def test_store_search_edge_cases(store: NumpyVectorStore) -> None:
    assert store.search([1.0, 0.0, 0.0], top_k=0) == []
    assert store.search([1.0, 0.0, 0.0], top_k=-1) == []
    assert store.search([0.0, 0.0, 0.0], top_k=3) == []  # 零向量不返回垃圾结果
    with pytest.raises(VectorStoreError):
        store.search([1.0, 0.0], top_k=3)  # 维度不符


def test_store_stable_tie_order() -> None:
    chunks = [_chunk("a", "一"), _chunk("b", "二"), _chunk("c", "三")]
    tie_store = NumpyVectorStore.build(chunks, [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    hits = tie_store.search([1.0, 0.0], top_k=3)
    assert [hit["chunk"]["id"] for hit in hits] == ["a", "b", "c"]  # 同分保持语料顺序


# ----------------------------------------------------------------------
# 2. 构建期校验
# ----------------------------------------------------------------------
def test_store_rejects_empty_chunks() -> None:
    with pytest.raises(VectorStoreError):
        NumpyVectorStore.build([], [])


def test_store_rejects_count_mismatch(sample_chunks: list[dict]) -> None:
    with pytest.raises(VectorStoreError):
        NumpyVectorStore.build(sample_chunks, [[1.0, 0.0]])


def test_store_rejects_duplicate_ids() -> None:
    chunks = [_chunk("same", "一"), _chunk("same", "二")]
    with pytest.raises(VectorStoreError):
        NumpyVectorStore.build(chunks, [[1.0, 0.0], [0.0, 1.0]])


def test_store_rejects_non_matrix_vectors() -> None:
    with pytest.raises(VectorStoreError):
        NumpyVectorStore.build([_chunk("a", "一")], [1.0, 0.0])


# ----------------------------------------------------------------------
# 3. 保存 / 加载
# ----------------------------------------------------------------------
def test_store_save_and_load_roundtrip(store: NumpyVectorStore, tmp_path: Path) -> None:
    npz = tmp_path / "nested" / "fake-3.npz"
    meta = tmp_path / "nested" / "fake-3.meta.json"
    store.save(npz, meta)
    assert npz.is_file() and meta.is_file()
    assert not list((tmp_path / "nested").glob("*.tmp"))  # 原子写入不残留临时文件

    payload = read_meta(meta)
    assert payload["provider"] == "fake"
    assert payload["model"] == "fake-hash-3"
    assert payload["dimension"] == 3
    assert payload["count"] == 3
    assert payload["corpus_sha256"] == "sha-test"
    assert [chunk["id"] for chunk in payload["chunks"]] == ["rule:1:2", "book:1", "faq:0"]

    loaded = NumpyVectorStore.load(npz, meta)
    assert loaded.size == 3 and loaded.dimension == 3
    assert loaded.provider_name == "fake"
    assert loaded.model == "fake-hash-3"
    assert loaded.corpus_sha256 == "sha-test"
    assert [hit["chunk"]["id"] for hit in loaded.search([1.0, 0.0, 0.0], top_k=3)] == [
        "rule:1:2",
        "faq:0",
        "book:1",
    ]


def test_store_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(VectorStoreError, match="不存在"):
        NumpyVectorStore.load(tmp_path / "nope.npz", tmp_path / "nope.meta.json")


def test_store_load_corrupted_npz_raises(store: NumpyVectorStore, tmp_path: Path) -> None:
    npz = tmp_path / "bad.npz"
    meta = tmp_path / "bad.meta.json"
    store.save(npz, meta)
    npz.write_bytes(b"not-a-real-npz")
    with pytest.raises(VectorStoreError, match="无法加载"):
        NumpyVectorStore.load(npz, meta)


def test_read_meta_rejects_missing_fields(tmp_path: Path) -> None:
    meta = tmp_path / "bad.meta.json"
    meta.write_text(json.dumps({"provider": "fake"}), encoding="utf-8")
    with pytest.raises(VectorStoreError, match="缺少字段"):
        read_meta(meta)
    meta.write_text("not json", encoding="utf-8")
    with pytest.raises(VectorStoreError):
        read_meta(meta)


def test_store_load_rejects_chunk_count_mismatch(store: NumpyVectorStore, tmp_path: Path) -> None:
    npz = tmp_path / "x.npz"
    meta = tmp_path / "x.meta.json"
    store.save(npz, meta)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload["chunks"] = payload["chunks"][:2]
    meta.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(VectorStoreError, match="不一致"):
        NumpyVectorStore.load(npz, meta)


def test_store_load_rejects_id_order_mismatch(store: NumpyVectorStore, tmp_path: Path) -> None:
    npz = tmp_path / "y.npz"
    meta = tmp_path / "y.meta.json"
    store.save(npz, meta)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload["chunks"] = list(reversed(payload["chunks"]))
    meta.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(VectorStoreError, match="顺序"):
        NumpyVectorStore.load(npz, meta)


def test_store_load_rejects_dimension_mismatch(store: NumpyVectorStore, tmp_path: Path) -> None:
    npz = tmp_path / "z.npz"
    meta = tmp_path / "z.meta.json"
    store.save(npz, meta)
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload["dimension"] = 99
    meta.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(VectorStoreError, match="dimension"):
        NumpyVectorStore.load(npz, meta)


# ----------------------------------------------------------------------
# 4. open_vector_store 工厂：各种不一致都要降级为 None + 告警
# ----------------------------------------------------------------------
def test_open_vector_store_missing_index_returns_none(
    indexed_config: RagConfig, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert open_vector_store(indexed_config, _provider_stub(dimension=64)) is None
    assert "向量索引不存在" in caplog.text


def test_open_vector_store_ok_and_sha_check(
    indexed_config: RagConfig, store: NumpyVectorStore, caplog: pytest.LogCaptureFixture
) -> None:
    npz, meta = indexed_config.index_paths("fake", 3)
    NumpyVectorStore.build(
        [chunk for chunk in store._chunks],  # noqa: SLF001 - 测试内复用 fixture 数据
        store._raw.tolist(),  # noqa: SLF001
        provider="fake",
        model="fake-hash-3",
        corpus_sha256="sha-ok",
    ).save(npz, meta)
    provider = _provider_stub(dimension=3)
    opened = open_vector_store(indexed_config, provider, corpus_sha256="sha-ok")
    assert opened is not None and opened.size == 3

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        stale = open_vector_store(indexed_config, provider, corpus_sha256="sha-changed")
    assert stale is None
    assert "语料" in caplog.text and "sha256" in caplog.text


def test_open_vector_store_model_mismatch_returns_none(
    indexed_config: RagConfig, store: NumpyVectorStore, caplog: pytest.LogCaptureFixture
) -> None:
    npz, meta = indexed_config.index_paths("fake", 3)
    store.save(npz, meta)  # model=fake-hash-3
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        opened = open_vector_store(
            indexed_config, _provider_stub(dimension=3, model="another-model"), corpus_sha256="sha-test"
        )
    assert opened is None
    assert "模型" in caplog.text


def test_open_vector_store_corrupt_meta_returns_none(
    indexed_config: RagConfig, store: NumpyVectorStore, caplog: pytest.LogCaptureFixture
) -> None:
    npz, meta = indexed_config.index_paths("fake", 3)
    store.save(npz, meta)
    meta.write_text("{broken", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert open_vector_store(indexed_config, _provider_stub(dimension=3)) is None
    assert "元数据" in caplog.text


def test_open_vector_store_pgvector_unavailable_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    config = RagConfig(vector_store="pgvector", embeddings_dir=Path("/nonexistent"))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert open_vector_store(config, _provider_stub(dimension=64)) is None
    assert "pgvector" in caplog.text


# ----------------------------------------------------------------------
# 5. PgVectorStore：不可用只降级，不抛异常
# ----------------------------------------------------------------------
def test_pgvector_without_dsn_unavailable() -> None:
    store = PgVectorStore(dsn="", dimension=1024)
    assert store.available is False
    assert "DSN" in (store.error or "")
    assert store.search([0.0] * 1024, top_k=5) == []


def test_pgvector_without_driver_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    store = PgVectorStore(dsn="postgresql://localhost:5432/library", dimension=1024)
    monkeypatch.setattr(store, "_load_driver", lambda: None)
    assert store.available is False
    assert "psycopg" in (store.error or "")
    assert store.search([0.0] * 1024, top_k=5) == []


def test_pgvector_connect_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenDriver:
        @staticmethod
        def connect(*args, **kwargs):
            raise OSError("connection refused")

    store = PgVectorStore(dsn="postgresql://localhost:5432/library", dimension=4)
    monkeypatch.setattr(store, "_load_driver", lambda: _BrokenDriver)
    assert store.available is False
    assert "connection refused" in (store.error or "")
    assert store.search([0.0, 0.0, 0.0, 0.0], top_k=3) == []


# ----------------------------------------------------------------------
# 6. scripts/index_corpus.build_index：构建 / 幂等 / 语料变更
# ----------------------------------------------------------------------
class _RecordingFakeEmbedding(FakeEmbeddingProvider):
    """记录每次 encode 的批大小，便于断言脚本按 --batch 分批。"""

    def __init__(self, dimension: int = 64) -> None:
        super().__init__(dimension)
        self.batches: list[int] = []

    def encode(self, texts, batch_size=None):  # noqa: ANN001 - 与基类签名一致
        self.batches.append(len(texts))
        return super().encode(texts, batch_size)


@pytest.fixture
def index_chunks() -> list[dict]:
    return [
        _chunk(f"chunk:{index}", f"第 {index} 块：借阅规则与馆藏介绍", "rule" if index % 2 else "book")
        for index in range(5)
    ]


def test_build_index_writes_npz_and_meta(
    indexed_config: RagConfig, index_chunks: list[dict], tmp_path: Path
) -> None:
    corpus = tmp_path / "knowledge.jsonl"
    sha = _write_corpus(corpus, index_chunks)
    provider = _RecordingFakeEmbedding()
    progress: list[tuple[int, int]] = []

    result = build_index(
        provider,
        index_chunks,
        config=indexed_config,
        batch_size=2,
        corpus_path=corpus,
        progress=lambda done, total, elapsed: progress.append((done, total)),
    )
    assert result.status == "built"
    assert result.count == len(index_chunks)
    assert result.dimension == 64
    assert result.batches == 3  # 5 条 / 每批 2 条
    assert result.npz_path == indexed_config.embeddings_dir / "fake-64.npz"
    assert result.npz_path.is_file() and result.meta_path.is_file()
    assert result.elapsed_seconds > 0
    assert result.per_item_ms > 0
    assert provider.batches == [2, 2, 1]
    assert progress == [(2, 5), (4, 5), (5, 5)]

    payload = read_meta(result.meta_path)
    assert payload["corpus_sha256"] == sha
    assert payload["corpus_path"] == str(corpus)
    assert payload["count"] == len(index_chunks)
    assert [chunk["id"] for chunk in payload["chunks"]] == [chunk["id"] for chunk in index_chunks]

    store = NumpyVectorStore.load(result.npz_path, result.meta_path)
    hits = store.search(provider.encode_one("借阅规则"), top_k=3)
    assert hits  # 构建出的索引可被服务端直接检索


def test_build_index_is_idempotent(
    indexed_config: RagConfig, index_chunks: list[dict], tmp_path: Path
) -> None:
    corpus = tmp_path / "knowledge.jsonl"
    _write_corpus(corpus, index_chunks)
    provider = _RecordingFakeEmbedding()

    first = build_index(provider, index_chunks, config=indexed_config, corpus_path=corpus)
    assert first.status == "built"
    first_vectors = np.load(first.npz_path)["vectors"].copy()

    second = build_index(provider, index_chunks, config=indexed_config, corpus_path=corpus)
    assert second.status == "skipped"  # 语料/provider/维度/条数都没变 → 跳过重建
    assert second.batches == 0
    assert np.array_equal(first_vectors, np.load(second.npz_path)["vectors"])

    forced = build_index(provider, index_chunks, config=indexed_config, corpus_path=corpus, force=True)
    assert forced.status == "built"
    assert np.array_equal(first_vectors, np.load(forced.npz_path)["vectors"])  # 重建结果一致


def test_build_index_rebuilds_when_corpus_changes(
    indexed_config: RagConfig, index_chunks: list[dict], tmp_path: Path
) -> None:
    corpus = tmp_path / "knowledge.jsonl"
    _write_corpus(corpus, index_chunks)
    provider = _RecordingFakeEmbedding()
    assert build_index(provider, index_chunks, config=indexed_config, corpus_path=corpus).status == "built"

    changed = list(index_chunks)
    changed[0] = _chunk("chunk:0", "改过的文本：罚金规则变了", "rule")
    _write_corpus(corpus, changed)
    result = build_index(provider, changed, config=indexed_config, corpus_path=corpus)
    assert result.status == "built"  # 语料 sha256 变了必须重建
    payload = read_meta(result.meta_path)
    assert payload["chunks"][0]["text"].startswith("改过的文本")


def test_build_index_rejects_empty_chunks(indexed_config: RagConfig) -> None:
    with pytest.raises(EmbeddingError, match="语料为空"):
        build_index(FakeEmbeddingProvider(), [], config=indexed_config)


def test_build_index_rejects_wrong_dimension(indexed_config: RagConfig, tmp_path: Path) -> None:
    class _WrongDimensionProvider(FakeEmbeddingProvider):
        def encode(self, texts, batch_size=None):  # noqa: ANN001
            return [[0.0, 0.0] for _ in texts]  # 2 维，但 provider.dimension=64

    with pytest.raises(EmbeddingError, match="维度"):
        build_index(
            _WrongDimensionProvider(),
            [_chunk("a", "文本")],
            config=indexed_config,
            batch_size=1,
            corpus_sha256="sha-x",
        )


def test_build_index_rejects_wrong_count(indexed_config: RagConfig) -> None:
    class _ShortProvider(FakeEmbeddingProvider):
        def encode(self, texts, batch_size=None):  # noqa: ANN001
            return [[0.0] * 64]

    with pytest.raises(EmbeddingError, match="条数"):
        build_index(
            _ShortProvider(),
            [_chunk("a", "一"), _chunk("b", "二")],
            config=indexed_config,
            batch_size=2,
            corpus_sha256="sha-x",
        )


def test_build_index_writes_index_that_service_can_reject_on_stale_sha(
    indexed_config: RagConfig, index_chunks: list[dict], tmp_path: Path
) -> None:
    """索引与语料 sha256 绑定：服务端用错误 sha 打开时必须拒绝（防脏召回）。"""
    corpus = tmp_path / "knowledge.jsonl"
    _write_corpus(corpus, index_chunks)
    result = build_index(
        FakeEmbeddingProvider(), index_chunks, config=indexed_config, corpus_path=corpus
    )
    store = NumpyVectorStore.load(result.npz_path, result.meta_path)
    provider = _provider_stub(dimension=64, model="fake-hash-64")
    assert open_vector_store(indexed_config, provider, corpus_sha256=store.corpus_sha256) is not None
    assert open_vector_store(indexed_config, provider, corpus_sha256="stale-sha") is None
