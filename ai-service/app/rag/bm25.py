"""纯 Python BM25 关键词检索（W3-3 的基线档，无需向量库 / 模型 / 网络）。

参数选择：

- ``k1=1.5``：词频饱和系数。语料块普遍很短（书 ~100 字、FAQ ~80 字），
  1.2–1.5 是短文本的常用区间；取 1.5 让同一词重复出现略微加分但迅速饱和。
- ``b=0.75``：文档长度归一化强度。语料块长度差异大（书名 vs 规则段），
  0.75 是标准经验值，能压住长块靠"字数多"蒙分。
- IDF 采用带 ``+1`` 的非负变体：``log(1 + (N - df + 0.5) / (df + 0.5))``，
  避免出现负 IDF 导致"越常见越扣分"的怪异排序（Lucene 同款处理）。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable, Sequence

from app.rag.tokenizer import tokenize

DEFAULT_K1 = 1.5
DEFAULT_B = 0.75


class BM25Index:
    """内存 BM25 倒排索引：建索引 O(总 token 数)，检索 O(查询词 × 含该词的文档数)。"""

    def __init__(
        self,
        chunks: Sequence[dict[str, Any]],
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ) -> None:
        self.chunks: list[dict[str, Any]] = list(chunks)
        self.k1 = float(k1)
        self.b = float(b)

        self._term_freqs: list[Counter[str]] = [
            Counter(tokenize(str(chunk.get("text") or ""))) for chunk in self.chunks
        ]
        self._doc_lengths: list[int] = [sum(tf.values()) for tf in self._term_freqs]
        total_length = sum(self._doc_lengths)
        self._avgdl = (total_length / len(self.chunks)) if self.chunks and total_length else 0.0

        # 文档频率 / IDF（IDF 只依赖全局统计，与查询无关，可预计算）
        doc_freq: Counter[str] = Counter()
        for term_freq in self._term_freqs:
            doc_freq.update(term_freq.keys())
        self._doc_freq = doc_freq
        corpus_size = len(self.chunks)
        self._idf: dict[str, float] = {
            term: math.log(1.0 + (corpus_size - freq + 0.5) / (freq + 0.5))
            for term, freq in doc_freq.items()
        }

    @classmethod
    def build(
        cls,
        chunks: Iterable[dict[str, Any]],
        *,
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ) -> "BM25Index":
        """从 chunk 列表构建索引（chunk 需含 ``text`` 字段，其余字段原样带回）。"""
        return cls(list(chunks), k1=k1, b=b)

    # ------------------------------------------------------------------
    # 只读属性（日志 / 健康检查用）
    # ------------------------------------------------------------------
    @property
    def size(self) -> int:
        """语料块数量。"""
        return len(self.chunks)

    @property
    def vocabulary_size(self) -> int:
        """词表大小（不同 token 数）。"""
        return len(self._idf)

    @property
    def avg_doc_length(self) -> float:
        """平均文档长度（token 数），BM25 长度归一的基准。"""
        return self._avgdl

    def get_idf(self, term: str) -> float:
        """返回某个 token 的 IDF（未登录词返回 0.0）。"""
        return self._idf.get(term, 0.0)

    def search(
        self,
        query: str,
        top_k: int = 5,
        source_types: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按 BM25 分数降序返回 ``[{chunk, score, rank}]``。

        - ``source_types`` 为 ``None`` 时检索全部来源；给定时只在该来源内排序，
          这样 ``top_k`` 截断发生在过滤之后，不会出现"过滤后不够 top_k 条"的问题。
        - 分数为 0 的块不会返回；空查询 / top_k <= 0 直接返回空列表。
        """
        if top_k <= 0 or not self.chunks:
            return []
        query_text = (query or "").strip()
        if not query_text:
            return []
        query_term_freq = Counter(tokenize(query_text))
        if not query_term_freq:
            return []

        allowed: set[str] | None = None
        if source_types is not None:
            allowed = {str(source_type).strip() for source_type in source_types if str(source_type).strip()}
            if not allowed:
                return []

        scored: list[tuple[float, int]] = []
        for index, term_freq in enumerate(self._term_freqs):
            chunk = self.chunks[index]
            if allowed is not None and chunk.get("source_type") not in allowed:
                continue
            score = self._score(term_freq, self._doc_lengths[index], query_term_freq)
            if score > 0.0:
                scored.append((score, index))

        # 同分按语料原始顺序稳定排序（保证结果可复现，测试不 flaky）
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            {"chunk": self.chunks[index], "score": score, "rank": rank}
            for rank, (score, index) in enumerate(scored[:top_k], start=1)
        ]

    def _score(
        self,
        term_freq: Counter[str],
        doc_length: int,
        query_term_freq: Counter[str],
    ) -> float:
        """单个文档的 BM25 分数：Σ qtf × IDF × tf×(k1+1) / (tf + k1×(1-b+b×dl/avgdl))。"""
        if self._avgdl <= 0.0:
            return 0.0
        length_norm = self.k1 * (1.0 - self.b + self.b * doc_length / self._avgdl)
        score = 0.0
        for term, query_tf in query_term_freq.items():
            freq = term_freq.get(term)
            if not freq:
                continue
            idf = self._idf.get(term, 0.0)
            score += query_tf * idf * (freq * (self.k1 + 1.0)) / (freq + length_norm)
        return score
