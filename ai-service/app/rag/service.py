"""RAG 检索服务：进程级单例 + 懒加载 + 三级降级（W3-2 / W3-3b）。

检索模式（``RAG_RETRIEVAL_MODE``）：

================  ==========================================================================
``bm25``          纯关键词检索（基线档，零依赖、可完全离线）；
``vector``        embedding 语义检索（余弦相似度，使用 ``data/embeddings/`` 下的离线索引）；
``hybrid``        默认：BM25 top-20 + 向量 top-20 → **RRF(k=60)** 融合 → top-20 → 重排钩子。
================  ==========================================================================

降级链（任何一步失败都不向调用方抛异常）：::

    hybrid ──向量不可用──> bm25
    vector ──向量不可用──> bm25（若 BM25 也不可用则空结果）
    运行时 embedding 调用失败 ──> 该次查询降级 BM25（只告警一次）
    重排模块缺失/报错 ──> 跳过重排（ImportError 属预期：重排由 task-7 并行交付）

边界（《AI 架构与接口契约》§1）：

- AI 服务**运行时**不直连数据库、不依赖任何外部服务；语料只来自离线生成的
  ``data/corpus/knowledge.jsonl``，向量索引只来自离线生成的 ``data/embeddings/``。
- 本模块 import 阶段只做标准库导入，**不读文件、不建索引**；真正加载推迟到首次
  ``search()``，即使语料/索引缺失也不会让 ``import app.rag.service`` 崩掉。
- ``search()`` 的签名与返回字段保持 W3-3 基线不变（``chunk_id/text/source_type/
  title/book_id/category/url/score``）；仅当实际使用向量/混合检索时**额外增加**
  ``retrieval: {mode, bm25_rank, vector_rank, rrf_score}``，bm25 模式字段集与基线完全一致。
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

from app.obs.logging import get_logger
from app.rag.bm25 import DEFAULT_B, DEFAULT_K1, BM25Index
from app.rag.corpus import DEFAULT_CORPUS_PATH, load_chunks, resolve_corpus_path
from app.rag.embedding import EmbeddingProvider, get_embedding_provider
from app.rag.rag_config import RagConfig, load_rag_config
from app.rag.vector_store import VectorStore, open_vector_store

logger = get_logger("rag.service")

DEFAULT_TOP_K = 5
RRF_DEFAULT_K = 60

# 构造参数哨兵：区分「未传入（自动加载）」与「显式传 None（强制不可用）」。
_UNSET: Any = object()

# 进程级单例（get_rag_service / reset_rag_service 共享）
_service: "RagService | None" = None
_service_lock = threading.Lock()


class RagService:
    """检索门面：BM25 + 可选向量/混合检索，对外只暴露 ``search()`` 与元信息。

    同一进程内只加载一次（语料 ~500 块建 BM25 < 100ms，向量索引 2.2MB 读盘 < 50ms），
    之后检索是纯内存操作。组件可用性在首次 ``search()`` 时确定。
    """

    def __init__(
        self,
        corpus_path: str | Path | None = None,
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
        config: RagConfig | None = None,
        embedding_provider: EmbeddingProvider | None = _UNSET,
        vector_store: VectorStore | None = _UNSET,
    ) -> None:
        self.corpus_path: Path = resolve_corpus_path(corpus_path)
        self.k1 = float(k1)
        self.b = float(b)
        self.config: RagConfig = config if config is not None else load_rag_config()
        # 注入的组件（测试用）；哨兵表示「首次检索时按配置自动加载」
        self._injected_provider: EmbeddingProvider | None = (
            embedding_provider if embedding_provider is not _UNSET else _UNSET
        )
        self._injected_store: VectorStore | None = (
            vector_store if vector_store is not _UNSET else _UNSET
        )

        self._lock = threading.RLock()
        self._ready = False
        self._bm25: BM25Index | None = None
        self._provider: EmbeddingProvider | None = None
        self._store: VectorStore | None = None
        self._load_error: str | None = None
        self._vector_error: str | None = None
        self._configured_mode: str = self.config.effective_retrieval_mode
        self._effective_mode: str = self._configured_mode

        # 重排钩子（task-7 并行交付；缺失时属预期，跳过即可）
        self._reranker: Any = None
        self._reranker_attempted = False
        self._rerank_failed = False
        self._vector_runtime_failed = False

    # ------------------------------------------------------------------
    # 懒加载：BM25 + 向量组件 + 模式裁决
    # ------------------------------------------------------------------
    def _ensure_ready(self) -> bool:
        """首次调用时加载全部组件；返回是否有任何检索能力可用。"""
        if self._ready:
            return self._any_available()
        with self._lock:
            if self._ready:
                return self._any_available()
            try:
                self._load_bm25()
                self._configure_vector()
            finally:
                self._ready = True
        return self._any_available()

    def _load_bm25(self) -> None:
        try:
            chunks = load_chunks(self.corpus_path)
            self._bm25 = BM25Index.build(chunks, k1=self.k1, b=self.b)
        except Exception as exc:  # 语料缺失/损坏/权限问题都不应打断对话
            self._bm25 = None
            self._load_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "RAG 索引不可用，检索已降级为空结果；"
                f"请先运行 ai-service/scripts/build_corpus.py 生成语料（{exc}）",
                extra={"code": "RAG_INDEX_UNAVAILABLE"},
            )
        else:
            logger.info(
                f"RAG 索引就绪：chunks={self._bm25.size}，"
                f"vocabulary={self._bm25.vocabulary_size}，"
                f"avgDocLength={self._bm25.avg_doc_length:.1f}，"
                f"corpus={self.corpus_path}",
                extra={"code": "RAG_INDEX_READY"},
            )
            self._load_error = None

    def _configure_vector(self) -> None:
        """按配置加载向量组件并裁决实际检索模式（失败只降级 + 告警）。"""
        self._configured_mode = configured = self.config.effective_retrieval_mode
        self._effective_mode = configured
        if configured == "bm25":
            return
        if self._load_error is not None:
            # 语料都读不到时不加载向量索引：索引元数据里的 chunk 文本与语料版本不一致，
            # 继续用会得到"看似有结果但无法溯源"的脏召回。
            self._effective_mode = "bm25"
            return
        self._effective_mode = self._open_vector_components(configured)

    def _open_vector_components(self, configured: str) -> str:
        provider = self._injected_provider
        if provider is _UNSET:
            provider = get_embedding_provider(
                self.config.effective_embedding_provider,
                model_path=self.config.embedding_model_path,
            )
            if provider is None:
                self._vector_error = "embedding provider 不可用"
                logger.warning(
                    "embedding provider 不可用，检索降级为 BM25",
                    extra={"code": "RAG_VECTOR_UNAVAILABLE", "provider": self.config.effective_embedding_provider},
                )
                return "bm25"
        if provider is None:
            self._vector_error = "embedding provider 被禁用"
            logger.warning(
                "embedding provider 被显式禁用，检索降级为 BM25",
                extra={"code": "RAG_VECTOR_UNAVAILABLE"},
            )
            return "bm25"

        store = self._injected_store
        if store is _UNSET:
            store = open_vector_store(
                self.config,
                provider,
                corpus_sha256=self._corpus_sha256(),
            )
        if store is None:
            self._vector_error = self._vector_error or "向量索引不可用"
            logger.warning(
                "向量索引不可用，检索降级为 BM25",
                extra={"code": "RAG_VECTOR_UNAVAILABLE"},
            )
            return "bm25"

        self._provider = provider
        self._store = store
        self._vector_error = None
        logger.info(
            f"检索组件就绪：mode={configured}（实际实现 {configured}），"
            f"vector_store={store.name}，vector_count={store.size}，"
            f"dimension={store.dimension}，rrf_k={self.config.rrf_k}",
            extra={"code": "RAG_HYBRID_READY"},
        )
        if configured == "hybrid" and self._bm25 is None:
            logger.warning("BM25 不可用，混合检索降级为纯向量检索", extra={"code": "RAG_HYBRID_DEGRADED"})
            return "vector"
        return configured

    def _corpus_sha256(self) -> str | None:
        try:
            return hashlib.sha256(self.corpus_path.read_bytes()).hexdigest()
        except OSError:
            return None

    def _any_available(self) -> bool:
        return self._bm25 is not None or self._store is not None

    # ------------------------------------------------------------------
    # 对外检索接口
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        source_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """检索知识块，按相关度降序返回 top_k 条。

        :param query: 用户问题（中英混合均可）
        :param top_k: 返回条数上限（<=0 返回空列表）
        :param source_types: 可选来源过滤，如 ``["book"]`` / ``["rule", "faq"]``
        :return: ``[{chunk_id, text, source_type, title, book_id, category, url, score}]``；
            使用向量/混合检索时额外带 ``retrieval`` 字段（各检索通道的排名与 RRF 分）
        """
        query_text = (query or "").strip()
        if not query_text or top_k <= 0:
            return []
        if not self._ensure_ready():
            return []

        mode = self._effective_mode
        try:
            if mode == "bm25":
                results = self._search_bm25(query_text, top_k=top_k, source_types=source_types)
            elif mode == "vector":
                results = self._search_vector(query_text, top_k=top_k, source_types=source_types)
                if results is None:  # 运行时 embedding 失败 → 本次查询降级 BM25
                    results = self._search_bm25(query_text, top_k=top_k, source_types=source_types)
            else:
                results = self._search_hybrid_or_bm25(query_text, top_k=top_k, source_types=source_types)
        except Exception as exc:  # 极端情况（如索引被并发替换）也不抛给 Agent
            logger.warning(
                f"RAG 检索失败，已降级为空结果：{type(exc).__name__}: {exc}",
                extra={"code": "RAG_SEARCH_FAILED"},
            )
            return []

        # 重排是「检索之后的后处理」，对所有模式都适用（bm25 模式同样可以靠重排纠正排序）。
        # 是否真正启用由 config.rerank_enabled 决定（默认 true，单测里显式关掉以保持离线）。
        results = self._maybe_rerank(query_text, results, top_k)
        return results[:top_k]

    # -- 各检索通道 -----------------------------------------------------
    def _search_bm25(
        self,
        query_text: str,
        *,
        top_k: int,
        source_types: list[str] | None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if self._bm25 is None:
            return []
        hits = self._bm25.search(query_text, top_k=limit or top_k, source_types=source_types)
        return [self._to_result(hit["chunk"], hit["score"]) for hit in hits]

    def _search_vector(
        self,
        query_text: str,
        *,
        top_k: int,
        source_types: list[str] | None,
        limit: int | None = None,
    ) -> list[dict[str, Any]] | None:
        """纯向量检索；embedding 运行时失败返回 None（由调用方降级 BM25）。"""
        if self._provider is None or self._store is None:
            return None
        candidate_limit = limit or max(top_k, self.config.candidate_k)
        query_vector = self._encode_query(query_text)
        if query_vector is None:
            return None
        hits = self._store.search(query_vector, top_k=candidate_limit, source_types=source_types)
        results = []
        for rank, hit in enumerate(hits, start=1):
            retrieval = {
                "mode": "vector",
                "bm25_rank": None,
                "vector_rank": rank,
                "rrf_score": None,
            }
            results.append(self._to_result(hit["chunk"], hit["score"], retrieval=retrieval))
        return results

    def _search_hybrid_or_bm25(
        self,
        query_text: str,
        *,
        top_k: int,
        source_types: list[str] | None,
    ) -> list[dict[str, Any]]:
        """混合检索；向量组件运行时不可用时降级 BM25。"""
        if self._provider is None or self._store is None or self._bm25 is None:
            return self._search_bm25(query_text, top_k=top_k, source_types=source_types)
        query_vector = self._encode_query(query_text)
        if query_vector is None:
            return self._search_bm25(query_text, top_k=top_k, source_types=source_types)

        candidate_k = max(int(self.config.candidate_k), int(top_k))
        rrf_k = max(1, int(self.config.rrf_k))
        bm25_hits = self._bm25.search(query_text, top_k=candidate_k, source_types=source_types)
        vector_hits = self._store.search(query_vector, top_k=candidate_k, source_types=source_types)

        fused: dict[str, dict[str, Any]] = {}
        for rank, hit in enumerate(bm25_hits, start=1):
            key = str(hit["chunk"].get("id"))
            entry = fused.setdefault(
                key,
                {"chunk": hit["chunk"], "bm25_rank": None, "vector_rank": None, "rrf_score": 0.0},
            )
            entry["bm25_rank"] = rank
            entry["rrf_score"] += 1.0 / (rrf_k + rank)
        for rank, hit in enumerate(vector_hits, start=1):
            key = str(hit["chunk"].get("id"))
            entry = fused.setdefault(
                key,
                {"chunk": hit["chunk"], "bm25_rank": None, "vector_rank": None, "rrf_score": 0.0},
            )
            entry["vector_rank"] = rank
            entry["rrf_score"] += 1.0 / (rrf_k + rank)

        ordered = sorted(
            fused.values(),
            key=lambda entry: (
                -entry["rrf_score"],
                entry["bm25_rank"] if entry["bm25_rank"] is not None else 10**6,
                entry["vector_rank"] if entry["vector_rank"] is not None else 10**6,
            ),
        )
        results = []
        for entry in ordered[:candidate_k]:
            rrf_score = round(float(entry["rrf_score"]), 8)
            retrieval = {
                "mode": "hybrid",
                "bm25_rank": entry["bm25_rank"],
                "vector_rank": entry["vector_rank"],
                "rrf_score": rrf_score,
            }
            results.append(self._to_result(entry["chunk"], rrf_score, retrieval=retrieval))
        return results

    # -- 辅助 -----------------------------------------------------------
    def _encode_query(self, query_text: str) -> list[float] | None:
        if self._provider is None:
            return None
        try:
            return self._provider.encode_one(query_text)
        except Exception as exc:  # noqa: BLE001 - 运行时调用失败也只降级
            if not self._vector_runtime_failed:
                self._vector_runtime_failed = True
                logger.warning(
                    f"向量检索运行时失败，已降级为 BM25（后续同类失败不再重复告警）："
                    f"{type(exc).__name__}: {exc}",
                    extra={"code": "RAG_VECTOR_RUNTIME_FAILED"},
                )
            return None

    @staticmethod
    def _to_result(
        chunk: dict[str, Any],
        score: float,
        *,
        retrieval: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = {
            "chunk_id": chunk.get("id"),
            "text": chunk.get("text", ""),
            "source_type": chunk.get("source_type"),
            "title": chunk.get("title", ""),
            "book_id": chunk.get("book_id"),
            "category": chunk.get("category", ""),
            "url": chunk.get("url"),
            "score": round(float(score), 6),
        }
        if retrieval is not None:
            result["retrieval"] = retrieval
        return result

    # ------------------------------------------------------------------
    # 重排钩子（task-7 并行交付；缺失/报错时静默跳过）
    # ------------------------------------------------------------------
    def _get_reranker(self) -> Any:
        if not self.config.rerank_enabled or self._rerank_failed:
            return None
        if self._reranker_attempted:
            return self._reranker
        with self._lock:
            if self._reranker_attempted:
                return self._reranker
            self._reranker_attempted = True
            try:
                from app.rag.rerank import get_reranker  # type: ignore[import-not-found]
            except ImportError:
                # 重排模块由 task-7 并行交付：本任务必须在其缺失时正常工作。
                logger.info(
                    "重排模块未提供（task-7），跳过重排",
                    extra={"code": "RAG_RERANK_SKIPPED"},
                )
                return None
            except Exception as exc:  # noqa: BLE001 - 重排模块自身导入异常也不影响检索
                logger.warning(
                    f"重排模块导入失败，跳过重排：{type(exc).__name__}: {exc}",
                    extra={"code": "RAG_RERANK_SKIPPED"},
                )
                return None
            try:
                try:
                    self._reranker = get_reranker(self.config)
                except TypeError:
                    self._reranker = get_reranker()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"重排器初始化失败，跳过重排：{type(exc).__name__}: {exc}",
                    extra={"code": "RAG_RERANK_SKIPPED"},
                )
                self._reranker = None
        return self._reranker

    def _maybe_rerank(
        self,
        query_text: str,
        results: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        reranker = self._get_reranker()
        if reranker is None or len(results) <= 1:
            return results
        try:
            try:
                reranked = reranker.rerank(query_text, results, top_k=top_k)
            except TypeError:  # 兼容只接受 (query, candidates) 的实现
                reranked = reranker.rerank(query_text, results)
        except Exception as exc:  # noqa: BLE001 - 重排失败回退未重排结果
            self._rerank_failed = True
            self._reranker = None
            logger.warning(
                f"重排失败，已回退未重排结果：{type(exc).__name__}: {exc}",
                extra={"code": "RAG_RERANK_FAILED"},
            )
            return results
        normalized = self._normalize_reranked(reranked, results)
        if not normalized:
            self._rerank_failed = True
            logger.warning("重排返回空结果，已回退未重排结果", extra={"code": "RAG_RERANK_FAILED"})
            return results
        return normalized

    @staticmethod
    def _normalize_reranked(
        reranked: Any,
        original: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """把重排器返回值对齐回基线结果结构（重排器只需给出 id 顺序或完整结果）。"""
        if not isinstance(reranked, (list, tuple)) or not reranked:
            return []
        by_id = {str(item.get("chunk_id")): item for item in original}
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in reranked:
            if isinstance(item, dict):
                chunk_id = item.get("chunk_id") or item.get("id")
            else:
                chunk_id = item
            key = str(chunk_id) if chunk_id is not None else ""
            if not key or key in seen or key not in by_id:
                continue
            result = dict(by_id[key])
            if isinstance(item, dict):
                for score_key in ("rerank_score", "score"):
                    value = item.get(score_key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        result["score"] = round(float(value), 6)
                        break
            normalized.append(result)
            seen.add(key)
        return normalized

    # ------------------------------------------------------------------
    # 运维 / 测试辅助
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """丢弃已加载组件，下次 search 重新读取语料/索引/配置（语料更新后热重载用）。"""
        with self._lock:
            self._ready = False
            self._bm25 = None
            self._provider = None
            self._store = None
            self._load_error = None
            self._vector_error = None
            self._vector_runtime_failed = False
            self._reranker = None
            self._reranker_attempted = False
            self._rerank_failed = False
            self._configured_mode = self.config.effective_retrieval_mode
            self._effective_mode = self._configured_mode

    @property
    def available(self) -> bool:
        """是否有任何检索通道可用（会触发一次懒加载）。"""
        return self._ensure_ready()

    @property
    def load_error(self) -> str | None:
        """最近一次语料加载失败的原因（成功为 None）。"""
        self._ensure_ready()
        return self._load_error

    @property
    def retrieval_mode(self) -> str:
        """实际生效的检索模式（会触发一次懒加载）。"""
        self._ensure_ready()
        return self._effective_mode

    def stats(self) -> dict[str, Any]:
        """索引规模、检索模式与降级原因，供 /health 或评测脚本展示。"""
        self._ensure_ready()
        return {
            "corpus_path": str(self.corpus_path),
            "available": self._any_available(),
            "chunk_count": self._bm25.size if self._bm25 is not None else 0,
            "vocabulary_size": self._bm25.vocabulary_size if self._bm25 is not None else 0,
            "load_error": self._load_error,
            "configured_mode": self._configured_mode,
            "effective_mode": self._effective_mode,
            "vector_store": self._store.name if self._store is not None else None,
            "vector_count": self._store.size if self._store is not None else 0,
            "vector_dimension": self._store.dimension if self._store is not None else 0,
            "vector_error": self._vector_error,
            "rerank_enabled": bool(self.config.rerank_enabled),
        }


def get_rag_service() -> RagService:
    """进程级单例（懒加载语料/索引，首次 search 时才真正加载；配置按 RAG_* 读取）。"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = RagService()
    return _service


def reset_rag_service(corpus_path: str | Path | None = None, **kwargs: Any) -> RagService:
    """重置单例（测试 / 切换语料用）；返回新的服务实例。"""
    global _service
    with _service_lock:
        _service = RagService(corpus_path, **kwargs)
    return _service


def default_corpus_path() -> Path:
    """默认语料路径（供脚本 / 健康检查展示）。"""
    return DEFAULT_CORPUS_PATH


__all__ = [
    "DEFAULT_TOP_K",
    "RRF_DEFAULT_K",
    "RagService",
    "default_corpus_path",
    "get_rag_service",
    "reset_rag_service",
]
