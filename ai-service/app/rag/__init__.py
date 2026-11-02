"""RAG 检索包（W3-3 基线档：纯 Python BM25，可完全离线运行）。

模块划分：

- ``tokenizer``：中英混合分词（中文 unigram + bigram，英文/数字整词，去停用词）；
- ``corpus``：加载 ``data/corpus/knowledge.jsonl``；
- ``bm25``：纯 Python BM25 倒排索引；
- ``service``：进程级单例 ``get_rag_service()``，对外 ``search()``，失败自动降级。

运行时（AI 服务）只读离线生成的 JSONL，不直连数据库；语料由
``ai-service/scripts/build_corpus.py`` 构建（该脚本是构建期工具，允许直连 MySQL）。
"""

from __future__ import annotations

from app.rag.bm25 import BM25Index
from app.rag.corpus import (
    DEFAULT_CORPUS_PATH,
    SOURCE_TYPES,
    CorpusFormatError,
    CorpusNotFoundError,
    count_by_source,
    load_chunks,
    resolve_corpus_path,
)
from app.rag.service import (
    DEFAULT_TOP_K,
    RagService,
    default_corpus_path,
    get_rag_service,
    reset_rag_service,
)
from app.rag.tokenizer import STOPWORDS, tokenize

__all__ = [
    "BM25Index",
    "DEFAULT_CORPUS_PATH",
    "DEFAULT_TOP_K",
    "SOURCE_TYPES",
    "STOPWORDS",
    "CorpusFormatError",
    "CorpusNotFoundError",
    "RagService",
    "count_by_source",
    "default_corpus_path",
    "get_rag_service",
    "load_chunks",
    "reset_rag_service",
    "resolve_corpus_path",
    "tokenize",
]
