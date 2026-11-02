"""向量索引存储：NumPy 内存实现（默认）+ pgvector（可选，连不上自动降级）。

选型说明：

- **默认 ``NumpyVectorStore``**：535 块 × 1024 维 = 2.2MB float32，全量驻留内存，
  归一化后直接做矩阵点积；单次检索 < 1ms，没有任何服务依赖，完全离线可复核。
  索引持久化为 ``data/embeddings/<provider>-<dim>.npz`` + 同名 ``.meta.json``：
  npz 存向量与 id，meta 存来源/模型/维度/语料 sha256 与 chunk 元数据——
  **语料变更后必须重建索引**，否则服务端会因为 sha256 不一致而拒绝加载（避免脏召回）。
- **``PgVectorStore`` 是可选后端**：psycopg 未安装、数据库连不上、缺 ``vector``
  扩展时都**不抛异常**，只告警并让服务降级回 NumPy / BM25；不阻断启动。
  当前环境不部署 PG，因此该实现的存在意义是「接口就绪 + 优雅降级」，不做端到端验收。

对外接口统一为::

    store.search(query_vector, top_k=5, source_types=None)
    -> [{"chunk": {...}, "score": float, "rank": int}, ...]

分数语义：NumPy 后端为**余弦相似度**（向量已 L2 归一化，点积即余弦）；pgvector 为
``1 - cosine_distance``，与 NumPy 后端同量纲，便于评测脚本直接对比。
"""

from __future__ import annotations

import json
import math
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from app.obs.logging import get_logger
from app.rag.rag_config import RagConfig, rag_env

logger = get_logger("rag.vector_store")

META_VERSION = 1
# npz 内部键名（固定，避免依赖 np.savez 的默认命名行为）
VECTORS_KEY = "vectors"
IDS_KEY = "ids"


class VectorStoreError(RuntimeError):
    """向量索引不可用 / 文件损坏 / 维度不一致。"""


def read_meta(meta_path: str | Path) -> dict[str, Any]:
    """读取并做最小校验的 ``.meta.json``（损坏时抛 :class:`VectorStoreError`）。"""
    path = Path(meta_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VectorStoreError(f"向量索引元数据不存在：{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise VectorStoreError(f"向量索引元数据无法解析（{path}）：{type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise VectorStoreError(f"向量索引元数据格式错误（{path}）：顶层应为 JSON 对象")
    for key in ("provider", "dimension", "count", "chunks"):
        if key not in payload:
            raise VectorStoreError(f"向量索引元数据缺少字段 {key!r}（{path}）")
    chunks = payload.get("chunks")
    if not isinstance(chunks, list):
        raise VectorStoreError(f"向量索引元数据 chunks 应为数组（{path}）")
    return payload


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """按行 L2 归一化；零向量保持全零（检索时会被跳过）。"""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms > 0.0, norms, 1.0)
    return matrix / norms


class VectorStore(ABC):
    """向量索引抽象：供 ``RagService`` 按配置替换后端。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """后端短名（``numpy`` / ``pgvector``）。"""

    @property
    @abstractmethod
    def size(self) -> int:
        """索引中的 chunk 数。"""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度。"""

    @abstractmethod
    def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 5,
        source_types: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """返回 ``[{chunk, score, rank}]``，按分数降序、rank 从 1 开始。"""


# ----------------------------------------------------------------------
# NumPy 后端
# ----------------------------------------------------------------------
class NumpyVectorStore(VectorStore):
    """内存向量索引（归一化后点积 = 余弦相似度）。"""

    def __init__(
        self,
        vectors: np.ndarray,
        chunks: Sequence[dict[str, Any]],
        *,
        provider: str = "unknown",
        model: str | None = None,
        corpus_path: str | None = None,
        corpus_sha256: str | None = None,
        dimension: int | None = None,
    ) -> None:
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2:
            raise VectorStoreError(f"向量矩阵应为二维 (n, dim)，实际 shape={matrix.shape}")
        if matrix.shape[0] != len(chunks):
            raise VectorStoreError(
                f"向量条数 {matrix.shape[0]} 与 chunk 数 {len(chunks)} 不一致，索引未对齐"
            )
        if matrix.shape[0] == 0:
            raise VectorStoreError("向量索引为空（chunks=0）")
        if dimension is not None and int(dimension) != int(matrix.shape[1]):
            raise VectorStoreError(
                f"向量维度 {matrix.shape[1]} 与声明的 dimension={dimension} 不一致"
            )
        ids = [str(chunk.get("id") or "") for chunk in chunks]
        if not all(ids):
            raise VectorStoreError("chunk 缺少 id，无法建立向量索引")
        if len(set(ids)) != len(ids):
            raise VectorStoreError("chunk id 重复，无法建立向量索引")

        self._ids = ids
        self._chunks = [dict(chunk) for chunk in chunks]
        self._raw = matrix
        self._matrix = _normalize_rows(matrix.astype(np.float32, copy=True))
        self._dimension = int(matrix.shape[1])
        self._provider = str(provider)
        self._model = model
        self._corpus_path = corpus_path
        self._corpus_sha256 = corpus_sha256

    # -- 构造 / 持久化 -------------------------------------------------
    @classmethod
    def build(
        cls,
        chunks: Sequence[dict[str, Any]],
        vectors: Sequence[Sequence[float]],
        *,
        provider: str = "unknown",
        model: str | None = None,
        corpus_path: str | None = None,
        corpus_sha256: str | None = None,
    ) -> "NumpyVectorStore":
        """从 chunk 列表 + 向量列表构建索引。"""
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim != 2:
            raise VectorStoreError(f"向量列表无法转为二维矩阵（shape={matrix.shape}）")
        return cls(
            matrix,
            chunks,
            provider=provider,
            model=model,
            corpus_path=corpus_path,
            corpus_sha256=corpus_sha256,
        )

    def save(self, npz_path: str | Path, meta_path: str | Path) -> tuple[Path, Path]:
        """原子写入 npz + meta（先写临时文件再 replace，避免半成品索引）。"""
        npz = Path(npz_path)
        meta = Path(meta_path)
        npz.parent.mkdir(parents=True, exist_ok=True)
        meta.parent.mkdir(parents=True, exist_ok=True)

        tmp_npz = npz.with_name(npz.name + ".tmp")
        with tmp_npz.open("wb") as handle:
            np.savez(handle, **{VECTORS_KEY: self._raw, IDS_KEY: np.asarray(self._ids, dtype=np.str_)})
        tmp_npz.replace(npz)

        payload = {
            "version": META_VERSION,
            "provider": self._provider,
            "model": self._model,
            "dimension": self._dimension,
            "count": len(self._ids),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "corpus_path": self._corpus_path,
            "corpus_sha256": self._corpus_sha256,
            "chunks": self._chunks,
        }
        tmp_meta = meta.with_name(meta.name + ".tmp")
        tmp_meta.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_meta.replace(meta)
        return npz, meta

    @classmethod
    def load(
        cls,
        npz_path: str | Path,
        meta_path: str | Path | None = None,
        *,
        meta_payload: dict[str, Any] | None = None,
    ) -> "NumpyVectorStore":
        """加载已持久化的索引；文件缺失/损坏/维度不符抛 :class:`VectorStoreError`。"""
        npz = Path(npz_path)
        meta_file = Path(meta_path) if meta_path is not None else npz.with_suffix(".meta.json")
        payload = meta_payload if meta_payload is not None else read_meta(meta_file)
        try:
            with np.load(npz, allow_pickle=False) as archive:
                vectors = np.asarray(archive[VECTORS_KEY], dtype=np.float32)
                ids = [str(item) for item in archive[IDS_KEY].tolist()]
        except FileNotFoundError as exc:
            raise VectorStoreError(
                f"向量索引文件不存在：{npz}；请先运行 scripts/index_corpus.py 构建索引"
            ) from exc
        except (OSError, KeyError, ValueError) as exc:
            raise VectorStoreError(f"向量索引文件无法加载（{npz}）：{type(exc).__name__}: {exc}") from exc

        chunks = payload.get("chunks") or []
        if len(chunks) != len(ids):
            raise VectorStoreError(
                f"索引元数据 chunks={len(chunks)} 与向量条数 {len(ids)} 不一致（{meta_file}）"
            )
        meta_ids = [str(chunk.get("id") or "") for chunk in chunks]
        if meta_ids != ids:
            raise VectorStoreError(f"索引元数据 chunk id 与 npz 中 ids 顺序不一致（{meta_file}）")
        if int(payload.get("dimension") or 0) != int(vectors.shape[1] if vectors.ndim == 2 else 0):
            raise VectorStoreError(
                f"索引元数据 dimension={payload.get('dimension')} 与向量实际维度 "
                f"{vectors.shape[1] if vectors.ndim == 2 else '未知'} 不一致（{meta_file}）"
            )
        return cls(
            vectors,
            chunks,
            provider=str(payload.get("provider") or "unknown"),
            model=payload.get("model"),
            corpus_path=payload.get("corpus_path"),
            corpus_sha256=payload.get("corpus_sha256"),
            dimension=int(payload.get("dimension") or 0) or None,
        )

    # -- 只读属性 -------------------------------------------------------
    @property
    def name(self) -> str:
        return "numpy"

    @property
    def size(self) -> int:
        return len(self._ids)

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model(self) -> str | None:
        return self._model

    @property
    def corpus_sha256(self) -> str | None:
        return self._corpus_sha256

    # -- 检索 -----------------------------------------------------------
    def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 5,
        source_types: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        if top_k <= 0 or not self._ids:
            return []
        query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if query.shape[0] != self._dimension:
            raise VectorStoreError(
                f"查询向量维度 {query.shape[0]} 与索引维度 {self._dimension} 不一致"
            )
        norm = float(np.linalg.norm(query))
        if not math.isfinite(norm) or norm <= 0.0:
            return []

        scores = self._matrix @ (query / norm)
        allowed: set[str] | None = None
        if source_types is not None:
            allowed = {str(value).strip() for value in source_types if str(value).strip()}
            if not allowed:
                return []
            mask = np.asarray(
                [str(chunk.get("source_type")) in allowed for chunk in self._chunks],
                dtype=bool,
            )
            scores = np.where(mask, scores, -np.inf)

        k = min(int(top_k), scores.shape[0])
        # 稳定排序：同分时保持语料原始顺序（结果可复现，测试不 flaky）
        order = np.argsort(-scores, kind="stable")[:k]
        hits: list[dict[str, Any]] = []
        for index in order:
            score = float(scores[index])
            if not math.isfinite(score):
                continue
            hits.append(
                {
                    "chunk": self._chunks[int(index)],
                    "score": score,
                    "rank": len(hits) + 1,
                }
            )
        return hits


# ----------------------------------------------------------------------
# pgvector 后端（可选；当前环境不部署，只保证优雅降级）
# ----------------------------------------------------------------------
class PgVectorStore(VectorStore):
    """PostgreSQL + pgvector 后端。

    **不阻断启动**：``psycopg`` 没装、DSN 没配、数据库连不上、缺 ``vector`` 扩展，
    都只会把 ``available`` 置 False 并告警一次；``search()`` 返回空列表。
    """

    def __init__(
        self,
        *,
        dsn: str | None = None,
        table: str = "rag_embeddings",
        dimension: int = 1024,
        connect_timeout: float = 2.0,
    ) -> None:
        self._dsn = (dsn if dsn is not None else rag_env("RAG_PGVECTOR_DSN") or "").strip()
        self._table = table
        self._dimension = max(1, int(dimension))
        self._connect_timeout = max(0.5, float(connect_timeout))
        self._available: bool | None = None
        self._error: str | None = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "pgvector"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def size(self) -> int:
        """未实现计数查询：不可用返回 0，可用时查一次表。"""
        if not self.available:
            return 0
        try:
            driver = self._load_driver()
            with driver.connect(self._dsn, connect_timeout=self._connect_timeout) as conn:  # type: ignore[union-attr]
                with conn.cursor() as cursor:
                    cursor.execute(f"SELECT count(*) FROM {self._table}")
                    row = cursor.fetchone()
                    return int(row[0]) if row else 0
        except Exception as exc:  # noqa: BLE001 - 查询失败只影响展示
            logger.warning(f"pgvector 统计失败：{type(exc).__name__}: {exc}")
            return 0

    @property
    def available(self) -> bool:
        if self._available is None:
            with self._lock:
                if self._available is None:
                    self._probe()
        return bool(self._available)

    @property
    def error(self) -> str | None:
        """最近一次探测失败原因（成功为 None）。"""
        _ = self.available
        return self._error

    def _load_driver(self) -> Any | None:
        """延迟导入 psycopg（未安装则返回 None，便于测试注入替身）。"""
        try:
            import psycopg  # type: ignore[import-not-found]
        except ImportError:
            return None
        return psycopg

    def _probe(self) -> None:
        if not self._dsn:
            self._error = "未配置 RAG_PGVECTOR_DSN"
            self._available = False
            logger.warning(
                "pgvector 后端未配置 DSN，已降级为内存向量库 / BM25",
                extra={"code": "RAG_PGVECTOR_UNAVAILABLE", "provider": "pgvector"},
            )
            return
        driver = self._load_driver()
        if driver is None:
            self._error = "未安装 psycopg"
            self._available = False
            logger.warning(
                "未安装 psycopg，pgvector 后端不可用，已降级为内存向量库 / BM25",
                extra={"code": "RAG_PGVECTOR_UNAVAILABLE", "provider": "pgvector"},
            )
            return
        try:
            with driver.connect(self._dsn, connect_timeout=self._connect_timeout) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                    if cursor.fetchone() is None:
                        raise VectorStoreError("数据库未安装 pgvector 扩展")
                    cursor.execute("SELECT to_regclass(%s)", (self._table,))
                    if cursor.fetchone() is None:
                        raise VectorStoreError(f"向量表 {self._table} 不存在，请先执行建表/入库")
            self._available = True
            self._error = None
        except Exception as exc:  # noqa: BLE001 - 连不上就降级，不阻断启动
            self._available = False
            self._error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                f"pgvector 后端不可用（{self._error}），已降级为内存向量库 / BM25",
                extra={"code": "RAG_PGVECTOR_UNAVAILABLE", "provider": "pgvector"},
            )

    def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 5,
        source_types: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        if top_k <= 0 or not self.available:
            return []
        vector = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        if vector.shape[0] != self._dimension:
            raise VectorStoreError(
                f"查询向量维度 {vector.shape[0]} 与索引维度 {self._dimension} 不一致"
            )
        literal = _vector_literal(vector)
        allowed = None
        sql = (
            f"SELECT chunk, 1 - (embedding <=> %s::vector) AS score "
            f"FROM {self._table}"
        )
        params: list[Any] = [literal]
        if source_types is not None:
            allowed = [str(value).strip() for value in source_types if str(value).strip()]
            if not allowed:
                return []
            sql += " WHERE source_type = ANY(%s)"
            params.append(allowed)
        sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
        params.extend([literal, int(top_k)])
        try:
            driver = self._load_driver()
            with driver.connect(self._dsn, connect_timeout=self._connect_timeout) as conn:  # type: ignore[union-attr]
                with conn.cursor() as cursor:
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001 - 查询失败按空结果降级
            self._available = False
            self._error = f"{type(exc).__name__}: {exc}"
            logger.warning(f"pgvector 检索失败，已降级为空结果：{self._error}")
            return []
        hits: list[dict[str, Any]] = []
        for position, row in enumerate(rows, start=1):
            chunk = row[0]
            if isinstance(chunk, str):  # psycopg 未配置 json 适配器时按文本返回
                try:
                    chunk = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
            if not isinstance(chunk, dict):
                continue
            hits.append({"chunk": chunk, "score": float(row[1]), "rank": position})
        return hits


def _vector_literal(vector: Sequence[float]) -> str:
    """把向量转成 pgvector 字面量 ``[0.1,0.2,...]``（不依赖 pgvector 适配包）。"""
    return "[" + ",".join(f"{float(value):.8f}" for value in vector) + "]"


# ----------------------------------------------------------------------
# 工厂：向量索引加载（任何失败都返回 None + 告警，由 service 降级）
# ----------------------------------------------------------------------
def open_vector_store(
    config: RagConfig,
    provider: Any,
    *,
    corpus_sha256: str | None = None,
) -> VectorStore | None:
    """按配置打开向量索引。

    - ``numpy``（默认）：读 ``<embeddings_dir>/<provider>-<dim>.npz|.meta.json``，
      并校验 provider/model/维度/**语料 sha256**，任一不一致 → ``None``（避免脏召回）；
    - ``pgvector``：探测连接与扩展，不可用 → ``None``；
    - 任何异常都被兜住并告警，**不抛给调用方**。
    """
    if provider is None:
        logger.warning(
            "embedding provider 不可用，跳过向量索引加载（检索降级 BM25）",
            extra={"code": "RAG_VECTOR_UNAVAILABLE"},
        )
        return None
    kind = config.effective_vector_store
    if kind == "pgvector":
        return _open_pgvector_store(config, provider)
    return _open_numpy_store(config, provider, corpus_sha256=corpus_sha256)


def _open_numpy_store(
    config: RagConfig,
    provider: Any,
    *,
    corpus_sha256: str | None,
) -> VectorStore | None:
    dimension = int(getattr(provider, "dimension", 0) or 0)
    provider_name = str(getattr(provider, "name", "unknown"))
    npz_path, meta_path = config.index_paths(provider_name, dimension)
    if not npz_path.exists() or not meta_path.exists():
        logger.info(
            f"向量索引不存在：{npz_path}；先运行 "
            f"cd ai-service && .venv/bin/python scripts/index_corpus.py --provider {provider_name}"
            "（检索降级 BM25）",
            extra={"code": "RAG_VECTOR_INDEX_MISSING"},
        )
        return None
    try:
        payload = read_meta(meta_path)
    except VectorStoreError as exc:
        logger.warning(
            f"{exc}；检索降级 BM25",
            extra={"code": "RAG_VECTOR_INDEX_INVALID"},
        )
        return None

    if str(payload.get("provider")) != provider_name:
        logger.warning(
            f"向量索引 provider={payload.get('provider')!r} 与当前 {provider_name!r} 不一致，"
            "请重新构建索引（检索降级 BM25）",
            extra={"code": "RAG_VECTOR_INDEX_MISMATCH"},
        )
        return None
    if int(payload.get("dimension") or 0) != dimension:
        logger.warning(
            f"向量索引维度 {payload.get('dimension')} 与当前 provider 维度 {dimension} 不一致，"
            "请重新构建索引（检索降级 BM25）",
            extra={"code": "RAG_VECTOR_INDEX_MISMATCH"},
        )
        return None
    model = getattr(provider, "model", None)
    if payload.get("model") and model and str(payload["model"]) != str(model):
        logger.warning(
            f"向量索引模型 {payload.get('model')!r} 与当前模型 {model!r} 不一致，"
            "请重新构建索引（检索降级 BM25）",
            extra={"code": "RAG_VECTOR_INDEX_MISMATCH"},
        )
        return None
    if corpus_sha256 and payload.get("corpus_sha256") and str(payload["corpus_sha256"]) != str(corpus_sha256):
        logger.warning(
            "向量索引与当前语料不一致（corpus sha256 不匹配），"
            "请重新运行 scripts/index_corpus.py 重建索引（检索降级 BM25）",
            extra={"code": "RAG_VECTOR_INDEX_STALE"},
        )
        return None
    try:
        store = NumpyVectorStore.load(npz_path, meta_path, meta_payload=payload)
    except (VectorStoreError, ValueError) as exc:
        logger.warning(
            f"向量索引加载失败：{exc}；检索降级 BM25",
            extra={"code": "RAG_VECTOR_INDEX_INVALID"},
        )
        return None
    except Exception as exc:  # noqa: BLE001 - numpy/IO 异常类型不可穷举
        logger.warning(
            f"向量索引加载异常（{type(exc).__name__}: {exc}）；检索降级 BM25",
            extra={"code": "RAG_VECTOR_INDEX_INVALID"},
        )
        return None
    logger.info(
        f"向量索引就绪：store=numpy，count={store.size}，dimension={store.dimension}，"
        f"provider={provider_name}，model={store.model}，path={npz_path}",
        extra={"code": "RAG_VECTOR_INDEX_READY"},
    )
    return store


def _open_pgvector_store(config: RagConfig, provider: Any) -> VectorStore | None:
    store = PgVectorStore(
        table=str(rag_env("RAG_PGVECTOR_TABLE", "rag_embeddings") or "rag_embeddings"),
        dimension=int(getattr(provider, "dimension", 0) or 0),
    )
    if not store.available:
        logger.warning(
            f"pgvector 后端不可用（{store.error}）；检索降级 BM25",
            extra={"code": "RAG_VECTOR_UNAVAILABLE", "provider": "pgvector"},
        )
        return None
    return store
