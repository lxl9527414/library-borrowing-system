"""RAG 检索服务：进程级单例 + 懒加载 + 失败降级（W3-3 基线档）。

边界（《AI 架构与接口契约》§1）：

- AI 服务**运行时**不直连数据库、不依赖任何外部服务；语料只来自离线生成的
  ``data/corpus/knowledge.jsonl``（由 ``scripts/build_corpus.py`` 构建）。
- 本模块 import 阶段只做标准库导入，**不读文件、不建索引**，因此即使语料缺失
  （或 JSONL 损坏）也不会让 ``import app.rag.service`` 崩掉；真正加载推迟到
  ``get_rag_service().search(...)`` 的首次调用。
- 加载/检索失败一律降级为「空结果 + WARNING 日志」，绝不向调用方抛异常，
  保证 Agent 其它工具（查馆藏、借还书）仍可正常回答。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from app.obs.logging import get_logger
from app.rag.bm25 import DEFAULT_B, DEFAULT_K1, BM25Index
from app.rag.corpus import DEFAULT_CORPUS_PATH, load_chunks, resolve_corpus_path

logger = get_logger("rag.service")

DEFAULT_TOP_K = 5

_service: "RagService | None" = None
_service_lock = threading.Lock()


class RagService:
    """BM25 检索门面：对外只暴露 ``search()`` 与少量元信息。

    同一进程内只建一次索引（语料 ~500 块，建索引 < 100ms），之后检索是纯内存操作。
    """

    def __init__(
        self,
        corpus_path: str | Path | None = None,
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ) -> None:
        self.corpus_path: Path = resolve_corpus_path(corpus_path)
        self.k1 = float(k1)
        self.b = float(b)
        self._index: BM25Index | None = None
        self._load_attempted = False
        self._load_error: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 懒加载
    # ------------------------------------------------------------------
    def _ensure_index(self) -> BM25Index | None:
        """首次调用时加载语料并建索引；失败只记日志，返回 None（降级）。"""
        if self._load_attempted:
            return self._index
        with self._lock:
            if self._load_attempted:  # 双检锁：并发首个请求只加载一次
                return self._index
            try:
                chunks = load_chunks(self.corpus_path)
                self._index = BM25Index.build(chunks, k1=self.k1, b=self.b)
            except Exception as exc:  # 语料缺失/损坏/权限问题都不应打断对话
                self._index = None
                self._load_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "RAG 索引不可用，检索已降级为空结果；"
                    f"请先运行 ai-service/scripts/build_corpus.py 生成语料（{exc}）",
                    extra={"code": "RAG_INDEX_UNAVAILABLE"},
                )
            else:
                logger.info(
                    f"RAG 索引就绪：chunks={self._index.size}，"
                    f"vocabulary={self._index.vocabulary_size}，"
                    f"avgDocLength={self._index.avg_doc_length:.1f}，"
                    f"corpus={self.corpus_path}",
                    extra={"code": "RAG_INDEX_READY"},
                )
                self._load_error = None
            finally:
                self._load_attempted = True
        return self._index

    # ------------------------------------------------------------------
    # 对外检索接口
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        source_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """BM25 关键词检索，按分数降序返回 top_k 条。

        :param query: 用户问题（中英混合均可）
        :param top_k: 返回条数上限（<=0 返回空列表）
        :param source_types: 可选来源过滤，如 ``["book"]`` / ``["rule", "faq"]``
        :return: ``[{chunk_id, text, source_type, title, book_id, category, url, score}]``
        """
        query_text = (query or "").strip()
        if not query_text or top_k <= 0:
            return []
        index = self._ensure_index()
        if index is None:
            return []
        try:
            hits = index.search(query_text, top_k=top_k, source_types=source_types)
        except Exception as exc:  # 极端情况（如语料被并发替换）也不抛给 Agent
            logger.warning(
                f"RAG 检索失败，已降级为空结果：{type(exc).__name__}: {exc}",
                extra={"code": "RAG_SEARCH_FAILED"},
            )
            return []

        return [
            {
                "chunk_id": hit["chunk"].get("id"),
                "text": hit["chunk"].get("text", ""),
                "source_type": hit["chunk"].get("source_type"),
                "title": hit["chunk"].get("title", ""),
                "book_id": hit["chunk"].get("book_id"),
                "category": hit["chunk"].get("category", ""),
                "url": hit["chunk"].get("url"),
                "score": round(float(hit["score"]), 6),
            }
            for hit in hits
        ]

    # ------------------------------------------------------------------
    # 运维 / 测试辅助
    # ------------------------------------------------------------------
    def reload(self) -> None:
        """丢弃已建索引，下次 search 重新加载语料（语料更新后热重载用）。"""
        with self._lock:
            self._index = None
            self._load_attempted = False
            self._load_error = None

    @property
    def available(self) -> bool:
        """索引是否可用（会触发一次懒加载）。"""
        return self._ensure_index() is not None

    @property
    def load_error(self) -> str | None:
        """最近一次加载失败的原因（成功为 None）。"""
        self._ensure_index()
        return self._load_error

    def stats(self) -> dict[str, Any]:
        """索引规模与语料路径，供 /health 或评测脚本展示。"""
        index = self._ensure_index()
        return {
            "corpus_path": str(self.corpus_path),
            "available": index is not None,
            "chunk_count": index.size if index is not None else 0,
            "vocabulary_size": index.vocabulary_size if index is not None else 0,
            "load_error": self._load_error,
        }


def get_rag_service() -> RagService:
    """进程级单例（懒加载语料，首次 search 时才真正建索引）。"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = RagService()
    return _service


def reset_rag_service(corpus_path: str | Path | None = None) -> RagService:
    """重置单例（测试 / 切换语料用）；返回新的服务实例。"""
    global _service
    with _service_lock:
        _service = RagService(corpus_path)
    return _service


def default_corpus_path() -> Path:
    """默认语料路径（供脚本 / 健康检查展示）。"""
    return DEFAULT_CORPUS_PATH
