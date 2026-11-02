#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 RAG 向量索引（离线构建期脚本，允许联网调用 embedding API）。

产出（默认 ``ai-service/data/embeddings/``）：

- ``<provider>-<dim>.npz``：float32 向量矩阵 + chunk id 数组；
- ``<provider>-<dim>.meta.json``：provider / model / 维度 / 条数 / 语料 sha256 / chunk 元数据。

为什么把「索引构建」和「服务运行」分开：

- 运行期服务**只读**这两个文件，不直连模型 API（首次请求不会因为网络抖动而变慢）；
- 索引与语料用 **sha256 绑定**：语料（或规则数值）变了但没重建索引时，
  服务会因 sha 不一致而拒绝加载并降级 BM25，避免「召回的是旧规则」这类脏数据事故；
- 幂等：默认会检测已存在且与当前语料/模型/维度一致的索引并**跳过重建**，
  ``--force`` 强制重建；重复构建得到的向量数组与元数据完全一致。

用法：
    cd ai-service
    .venv/bin/python scripts/index_corpus.py --provider dashscope
    .venv/bin/python scripts/index_corpus.py --provider dashscope --batch 10 --force
    .venv/bin/python scripts/index_corpus.py --provider fake --batch 64      # 离线冒烟
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

AI_ROOT = Path(__file__).resolve().parent.parent
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

from app.rag.corpus import load_chunks  # noqa: E402
from app.rag.embedding import (  # noqa: E402
    EMBEDDING_PROVIDERS,
    EmbeddingError,
    EmbeddingProvider,
    get_embedding_provider,
)
from app.rag.rag_config import RagConfig, load_rag_config  # noqa: E402
from app.rag.vector_store import NumpyVectorStore, VectorStoreError, read_meta  # noqa: E402

DEFAULT_BATCH_SIZE = 10  # DashScope text-embedding-v3 单批上限


@dataclass
class IndexBuildResult:
    """一次索引构建的结果（脚本打印 + 测试断言共用）。"""

    status: str  # "built" | "skipped"
    count: int
    dimension: int
    provider: str
    model: str | None
    npz_path: Path
    meta_path: Path
    elapsed_seconds: float
    batches: int

    @property
    def per_item_ms(self) -> float:
        if self.count <= 0:
            return 0.0
        return self.elapsed_seconds * 1000.0 / self.count


def build_index(
    provider: EmbeddingProvider,
    chunks: Sequence[dict],
    *,
    config: RagConfig | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    force: bool = False,
    corpus_path: str | Path | None = None,
    corpus_sha256: str | None = None,
    progress: Callable[[int, int, float], None] | None = None,
) -> IndexBuildResult:
    """把 ``chunks`` 编码并写入 ``config.embeddings_dir``。

    - 已存在且 provider/model/维度/语料 sha256/条数全部一致 → ``status="skipped"``；
    - 否则重新编码并原子写入 → ``status="built"``。

    :param progress: 可选进度回调 ``(已完成, 总数, 已耗时秒)``，每批调用一次。
    """
    config = config or load_rag_config()
    dimension = int(getattr(provider, "dimension", 0) or 0)
    provider_name = str(getattr(provider, "name", "unknown"))
    model = getattr(provider, "model", None)
    if dimension <= 0:
        raise EmbeddingError(f"embedding provider 维度非法：{dimension}")
    if not chunks:
        raise EmbeddingError("语料为空，无法构建向量索引（先运行 scripts/build_corpus.py）")

    corpus_path = Path(corpus_path) if corpus_path is not None else None
    if corpus_sha256 is None and corpus_path is not None:
        corpus_sha256 = _sha256_file(corpus_path)
    npz_path, meta_path = config.index_paths(provider_name, dimension)

    if not force and _index_is_current(
        npz_path,
        meta_path,
        provider=provider_name,
        model=model,
        dimension=dimension,
        count=len(chunks),
        corpus_sha256=corpus_sha256,
    ):
        return IndexBuildResult(
            status="skipped",
            count=len(chunks),
            dimension=dimension,
            provider=provider_name,
            model=model,
            npz_path=npz_path,
            meta_path=meta_path,
            elapsed_seconds=0.0,
            batches=0,
        )

    texts = [str(chunk.get("text") or "") for chunk in chunks]
    size = max(1, int(batch_size))
    vectors: list[list[float]] = []
    started = time.perf_counter()
    batches = 0
    for start in range(0, len(texts), size):
        batch = texts[start : start + size]
        encoded = provider.encode(batch, batch_size=size)
        if len(encoded) != len(batch):
            raise EmbeddingError(
                f"embedding 返回条数与输入不一致：输入 {len(batch)}，返回 {len(encoded)}"
            )
        vectors.extend(encoded)
        batches += 1
        if progress is not None:
            progress(min(start + size, len(texts)), len(texts), time.perf_counter() - started)

    if len(vectors) != len(texts):
        raise EmbeddingError(f"embedding 总条数不符：文本 {len(texts)}，向量 {len(vectors)}")
    bad = [index for index, vector in enumerate(vectors) if len(vector) != dimension]
    if bad:
        raise EmbeddingError(
            f"第 {bad[0]} 条向量维度 {len(vectors[bad[0]])} 与期望 {dimension} 不一致"
            f"（共 {len(bad)} 条异常）"
        )

    store = NumpyVectorStore.build(
        chunks,
        vectors,
        provider=provider_name,
        model=model,
        corpus_path=str(corpus_path) if corpus_path is not None else None,
        corpus_sha256=corpus_sha256,
    )
    store.save(npz_path, meta_path)
    return IndexBuildResult(
        status="built",
        count=len(chunks),
        dimension=dimension,
        provider=provider_name,
        model=model,
        npz_path=npz_path,
        meta_path=meta_path,
        elapsed_seconds=time.perf_counter() - started,
        batches=batches,
    )


def _index_is_current(
    npz_path: Path,
    meta_path: Path,
    *,
    provider: str,
    model: str | None,
    dimension: int,
    count: int,
    corpus_sha256: str | None,
) -> bool:
    """已有索引是否可以直接复用（任何字段不一致都视为需要重建）。"""
    if not npz_path.exists() or not meta_path.exists():
        return False
    try:
        payload = read_meta(meta_path)
    except VectorStoreError:
        return False
    if str(payload.get("provider")) != provider:
        return False
    if int(payload.get("dimension") or 0) != dimension:
        return False
    if int(payload.get("count") or 0) != count:
        return False
    if model and payload.get("model") and str(payload["model"]) != str(model):
        return False
    if corpus_sha256 and payload.get("corpus_sha256") and str(payload["corpus_sha256"]) != str(corpus_sha256):
        return False
    # 元数据说得好听不如真读一次：npz 损坏时也要重建
    try:
        with np.load(npz_path, allow_pickle=False) as archive:
            return int(archive["vectors"].shape[0]) == count
    except Exception:  # noqa: BLE001 - 损坏就重建
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _print_result(result: IndexBuildResult) -> None:
    print("")
    print("=" * 64)
    if result.status == "skipped":
        print("✅ 向量索引已是最新，跳过重建（--force 可强制重建）")
        print(f"   provider : {result.provider}    model: {result.model}")
        print(f"   索引规模 : {result.count} × {result.dimension}")
        print(f"   索引文件 : {result.npz_path}")
        print(f"   元数据   : {result.meta_path}")
        print("=" * 64)
        return
    print("✅ 向量索引构建完成")
    print(f"   provider      : {result.provider}")
    print(f"   model         : {result.model}")
    print(f"   索引规模      : {result.count} × {result.dimension}")
    print(f"   批次数        : {result.batches}")
    print(f"   总耗时        : {result.elapsed_seconds:.2f} s")
    print(f"   单条均耗时    : {result.per_item_ms:.2f} ms/条")
    print(f"   索引文件      : {result.npz_path}")
    print(f"   元数据        : {result.meta_path}")
    print("=" * 64)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 RAG 向量索引（离线构建期脚本）")
    parser.add_argument(
        "--provider",
        default="auto",
        choices=list(EMBEDDING_PROVIDERS),
        help="embedding provider（默认按 RAG_EMBEDDING_PROVIDER，通常为 dashscope）",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"编码批大小（默认 {DEFAULT_BATCH_SIZE}；DashScope 单批上限 {DEFAULT_BATCH_SIZE}）",
    )
    parser.add_argument("--corpus", default=None, help="语料 JSONL 路径（默认 data/corpus/knowledge.jsonl）")
    parser.add_argument("--force", action="store_true", help="忽略已有索引，强制重建")
    args = parser.parse_args(argv)

    config = load_rag_config()
    config.embeddings_dir.mkdir(parents=True, exist_ok=True)

    provider = get_embedding_provider(args.provider)
    if provider is None:
        print(
            f"❌ 没有可用的 embedding provider（--provider {args.provider}）；\n"
            "   请检查 .env 中的 DASHSCOPE_API_KEY / RAG_EMBEDDING_PROVIDER，"
            "或先跑 --provider fake 做离线冒烟。",
            file=sys.stderr,
        )
        return 2

    try:
        from app.rag.corpus import resolve_corpus_path

        corpus_path = resolve_corpus_path(args.corpus)
        chunks = load_chunks(corpus_path)
        sha = _sha256_file(corpus_path)
        print(
            f"语料：{corpus_path}（{len(chunks)} 块，sha256={sha[:12]}…）\n"
            f"provider={provider.name}  model={getattr(provider, 'model', None)}  "
            f"dimension={provider.dimension}  batch={args.batch}"
        )

        def _progress(done: int, total: int, elapsed: float) -> None:
            print(f"  已编码 {done}/{total}（{elapsed:.1f}s）")

        result = build_index(
            provider,
            chunks,
            config=config,
            batch_size=args.batch,
            force=args.force,
            corpus_path=corpus_path,
            corpus_sha256=sha,
            progress=_progress,
        )
    except EmbeddingError as exc:
        print(f"❌ embedding 失败：{exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - 脚本给人看，给出可操作提示
        print(f"❌ 构建向量索引失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()

    _print_result(result)
    print(f"\n下一步：cd ai-service && .venv/bin/python scripts/run_eval.py   # RAG_RETRIEVAL_MODE=hybrid")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
