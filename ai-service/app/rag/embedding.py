"""Embedding Provider 抽象与三种实现（W3-2 语义检索的「模型层」）。

为什么单独一层：

- **可替换**：检索层只依赖 ``EmbeddingProvider.encode()``，换模型（通义 / 本地
  sentence-transformers / 测试替身）不动 service / vector_store；
- **可降级**：任何 provider 初始化失败都**返回 None 而不是抛异常**，
  由 ``RagService`` 降级到 BM25，保证「模型不可用 ≠ 服务不可用」；
- **可离线测试**：``FakeEmbeddingProvider`` 确定性、零网络，单元测试永远不碰真实 API。

三种实现：

============================  ==========================================================
``DashScopeEmbeddingProvider``  阿里云百炼 OpenAI 兼容端点，``text-embedding-v3``，1024 维；
                                官方限制**单批最多 10 条**，本模块负责分批 + 429/5xx 退避重试。
``LocalEmbeddingProvider``     sentence-transformers 本地模型（当前环境拉不到模型 → 只保留
                                降级逻辑：路径缺失时构造失败，检索层回退 BM25）。
``FakeEmbeddingProvider``      测试替身：对分词结果做确定性哈希桶累加，64 维、同文同向量。
============================  ==========================================================

配置读取复用 :mod:`app.rag.rag_config`（不在这里重复解析 ``.env``）：provider 名、模型名、
维度、本地模型路径分别对应 ``RAG_EMBEDDING_PROVIDER`` / ``RAG_EMBEDDING_MODEL`` /
``RAG_EMBEDDING_DIM``（兼容 ``RAG_EMBEDDING_DIMENSION``）/ ``RAG_EMBEDDING_MODEL_PATH``。

安全约定：本模块只检查 ``DASHSCOPE_API_KEY`` 是否存在，**不打印 Key**；异常信息里也
只包含模型名 / HTTP 状态码 / 响应体片段（响应体不含 Key）。
"""

from __future__ import annotations

import abc
import hashlib
import math
import threading
import time
from pathlib import Path
from typing import Sequence

import httpx

from app.obs.logging import get_logger
from app.rag.rag_config import EMBEDDING_PROVIDERS, rag_env, rag_int
from app.rag.tokenizer import tokenize

logger = get_logger("rag.embedding")

DASHSCOPE_API_KEY_ENV = "DASHSCOPE_API_KEY"
DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DASHSCOPE_DEFAULT_MODEL = "text-embedding-v3"
DASHSCOPE_DEFAULT_DIMENSION = 1024
# 官方限制：text-embedding-v3 单次请求 input 最多 10 条
DASHSCOPE_MAX_BATCH_SIZE = 10
# 这些 HTTP 状态码值得重试（429 限流 + 5xx 服务端抖动）
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

FAKE_DEFAULT_DIMENSION = 64


class EmbeddingError(RuntimeError):
    """embedding 调用/解析失败（运行时）。"""


class EmbeddingConfigError(EmbeddingError):
    """embedding 配置缺失（缺 Key / 缺模型路径 / 缺依赖），属于「预期内不可用」。"""


# ----------------------------------------------------------------------
# 配置解析（统一走 rag_config，避免第二套 .env 解析）
# ----------------------------------------------------------------------
def resolve_embedding_dimension(default: int = DASHSCOPE_DEFAULT_DIMENSION) -> int:
    """读取 embedding 维度。

    ``.env`` 模板里写的是 ``RAG_EMBEDDING_DIM``，而 ``rag_config`` 读的是
    ``RAG_EMBEDDING_DIMENSION``；这里**两个都认**，前者优先，避免用户按模板配置
    后维度被静默忽略（维度错了索引就对不上）。
    """
    return rag_int("RAG_EMBEDDING_DIM", rag_int("RAG_EMBEDDING_DIMENSION", default))


def resolve_dashscope_api_key() -> str | None:
    """读取 DashScope Key（进程环境变量 > .env），空字符串视为未配置。"""
    value = rag_env(DASHSCOPE_API_KEY_ENV)
    return value.strip() if value and value.strip() else None


def resolve_embedding_model(default: str = DASHSCOPE_DEFAULT_MODEL) -> str:
    """读取模型名（``RAG_EMBEDDING_MODEL``）。"""
    return rag_env("RAG_EMBEDDING_MODEL", default) or default


# ----------------------------------------------------------------------
# 抽象基类
# ----------------------------------------------------------------------
class EmbeddingProvider(abc.ABC):
    """embedding 提供方：检索层只依赖 ``name`` / ``dimension`` / ``encode``。"""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """provider 短名（用于索引文件名，如 ``dashscope-1024``）。"""

    @property
    @abc.abstractmethod
    def dimension(self) -> int:
        """向量维度。"""

    @abc.abstractmethod
    def encode(
        self,
        texts: Sequence[str],
        batch_size: int | None = None,
    ) -> list[list[float]]:
        """把文本编码成向量；返回顺序必须与 ``texts`` 一致。"""

    def encode_one(self, text: str) -> list[float]:
        """单条编码（检索时编码 query 用）。"""
        vectors = self.encode([text])
        if not vectors:
            raise EmbeddingError("embedding 返回空结果")
        return vectors[0]


# ----------------------------------------------------------------------
# 1. DashScope（通义 text-embedding-v3）
# ----------------------------------------------------------------------
class DashScopeEmbeddingProvider(EmbeddingProvider):
    """阿里云百炼 OpenAI 兼容 embedding。

    端点：``POST {base_url}/embeddings``，body::

        {"model": "text-embedding-v3", "input": [...], "dimensions": 1024}

    设计要点：

    - **单批 ≤ 10**：官方限制，调用方传更大的 batch 会被自动夹到 10；
    - **退避重试**：429/5xx/网络错误按 ``1s, 2s, 4s`` 指数退避，默认最多 3 次；
    - **批间不 sleep**：535 块 ≈ 54 批，单批 200~500ms，已远低于 RPM 限额；
      若未来语料放大到 1 万块，再按 QPS 需要加 ``RAG_EMBEDDING_BATCH_DELAY``。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        dimension: int | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        max_batch_size: int = DASHSCOPE_MAX_BATCH_SIZE,
        max_retries: int = 3,
        retry_backoff_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        key = api_key if api_key is not None else resolve_dashscope_api_key()
        key = (key or "").strip()
        if not key:
            raise EmbeddingConfigError(
                "缺少 DASHSCOPE_API_KEY，DashScope embedding 不可用；"
                "请在 .env 中配置 DASHSCOPE_API_KEY=...，"
                "或设置 RAG_EMBEDDING_PROVIDER=fake/local 使用离线方案（检索会自动降级 BM25）"
            )
        self._api_key = key
        self._model = (model or resolve_embedding_model()).strip()
        self._dimension = int(dimension or resolve_embedding_dimension())
        if self._dimension <= 0:
            raise EmbeddingConfigError(f"embedding 维度非法：{self._dimension}（应为正整数，text-embedding-v3 默认 1024）")
        base = (base_url or rag_env("RAG_EMBEDDING_BASE_URL", DASHSCOPE_DEFAULT_BASE_URL) or "").strip()
        self._base_url = base.rstrip("/") or DASHSCOPE_DEFAULT_BASE_URL
        self._max_batch_size = max(1, min(int(max_batch_size), DASHSCOPE_MAX_BATCH_SIZE))
        self._max_retries = max(1, int(max_retries))
        self._retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)

    # -- 只读属性 ------------------------------------------------------
    @property
    def name(self) -> str:
        return "dashscope"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model(self) -> str:
        return self._model

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}/embeddings"

    def close(self) -> None:
        """释放底层连接池（脚本退出前调用；失败忽略）。"""
        try:
            self._client.close()
        except Exception:  # pragma: no cover - 关闭失败无影响
            pass

    # -- 编码 ----------------------------------------------------------
    def encode(
        self,
        texts: Sequence[str],
        batch_size: int | None = None,
    ) -> list[list[float]]:
        items = ["" if text is None else str(text) for text in texts]
        if not items:
            return []
        size = self._resolve_batch_size(batch_size)
        vectors: list[list[float]] = []
        for start in range(0, len(items), size):
            vectors.extend(self._encode_batch(items[start : start + size]))
        return vectors

    def _resolve_batch_size(self, batch_size: int | None) -> int:
        if batch_size is None:
            return self._max_batch_size
        try:
            requested = int(batch_size)
        except (TypeError, ValueError):
            return self._max_batch_size
        return max(1, min(requested, self._max_batch_size))

    def _payload(self, batch: Sequence[str]) -> dict[str, object]:
        return {
            "model": self._model,
            "input": list(batch),
            "dimensions": self._dimension,
            "encoding_format": "float",
        }

    def _encode_batch(self, batch: Sequence[str]) -> list[list[float]]:
        last_error = "unknown"
        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._client.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=self._payload(batch),
                )
            except httpx.HTTPError as exc:  # 网络层错误（超时 / DNS / 连接重置）
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code == 200:
                    try:
                        return self._parse_response(response.json(), expected=len(batch))
                    except EmbeddingError as exc:  # 响应结构/维度不对，重试无意义
                        raise exc
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                if response.status_code not in _RETRYABLE_STATUS:
                    break
            if attempt < self._max_retries:
                delay = self._retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    f"DashScope embedding 第 {attempt}/{self._max_retries} 次调用失败"
                    f"（{last_error}），{delay:.1f}s 后重试",
                    extra={"code": "RAG_EMBEDDING_RETRY", "provider": "dashscope"},
                )
                time.sleep(delay)
        raise EmbeddingError(
            f"DashScope embedding 调用失败（model={self._model}，"
            f"已重试 {self._max_retries} 次）：{last_error}"
        )

    def _parse_response(self, payload: object, *, expected: int) -> list[list[float]]:
        if not isinstance(payload, dict):
            raise EmbeddingError(f"DashScope embedding 响应不是 JSON 对象：{str(payload)[:200]}")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected:
            actual = len(data) if isinstance(data, list) else "非列表"
            raise EmbeddingError(
                f"DashScope embedding 返回条数不符：期望 {expected}，实际 {actual}；"
                f"响应片段：{str(payload)[:200]}"
            )
        indexed: list[tuple[int, list[float]]] = []
        for position, item in enumerate(data):
            if not isinstance(item, dict):
                raise EmbeddingError(f"DashScope embedding 返回项不是对象：{str(item)[:100]}")
            vector = item.get("embedding")
            if not isinstance(vector, list):
                raise EmbeddingError(f"DashScope embedding 返回项缺少 embedding 数组：{str(item)[:100]}")
            try:
                floats = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise EmbeddingError(f"DashScope embedding 向量含非数值元素：{exc}") from exc
            if len(floats) != self._dimension:
                raise EmbeddingError(
                    f"embedding 维度不符：期望 {self._dimension}，实际 {len(floats)}；"
                    "请检查 RAG_EMBEDDING_DIM / RAG_EMBEDDING_MODEL（text-embedding-v3 支持 1024）"
                )
            if not all(math.isfinite(value) for value in floats):
                raise EmbeddingError("DashScope embedding 向量包含 NaN/Inf，拒绝入库")
            index = item.get("index")
            try:
                position_key = int(index) if index is not None else position
            except (TypeError, ValueError):
                position_key = position
            indexed.append((position_key, floats))
        indexed.sort(key=lambda pair: pair[0])
        return [vector for _, vector in indexed]


# ----------------------------------------------------------------------
# 2. 本地 sentence-transformers（当前环境拉不到模型 → 只保留降级逻辑）
# ----------------------------------------------------------------------
class LocalEmbeddingProvider(EmbeddingProvider):
    """本地 sentence-transformers 模型。

    模型下载在当前环境全部超时/被拦截，因此这里**不做任何自动下载**：
    只接受一个已是本地目录的 ``RAG_EMBEDDING_MODEL_PATH``；路径不存在直接抛
    :class:`EmbeddingConfigError`，由 :func:`get_embedding_provider` 转成 ``None``。
    """

    def __init__(
        self,
        model_path: str | Path | None,
        *,
        dimension: int | None = None,
        batch_size: int = 32,
    ) -> None:
        if not model_path:
            raise EmbeddingConfigError(
                "未配置 RAG_EMBEDDING_MODEL_PATH，本地 embedding 不可用；"
                "本机无法下载模型，请改用 RAG_EMBEDDING_PROVIDER=dashscope 或 fake"
            )
        path = Path(model_path).expanduser()
        if not path.exists():
            raise EmbeddingConfigError(
                f"本地 embedding 模型路径不存在：{path}；"
                "本机拉不到 HF/ModelScope 模型，检索将自动降级为 BM25"
            )
        self._model_path = path
        self._dimension = int(dimension or resolve_embedding_dimension())
        self._batch_size = max(1, int(batch_size))
        self._model: object | None = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "local"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model(self) -> str:
        return self._model_path.name

    def _ensure_model(self) -> object:
        """懒加载 SentenceTransformer；缺依赖时给出中文可操作提示。"""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingConfigError(
                    "未安装 sentence-transformers，本地 embedding 不可用；"
                    "请安装后重试，或改用 RAG_EMBEDDING_PROVIDER=dashscope（检索会自动降级 BM25）"
                ) from exc
            try:
                model = SentenceTransformer(str(self._model_path))
            except Exception as exc:  # 模型目录损坏 / 依赖版本不匹配
                raise EmbeddingConfigError(
                    f"本地 embedding 模型加载失败（{self._model_path}）：{type(exc).__name__}: {exc}"
                ) from exc
            actual = None
            try:
                actual = int(model.get_sentence_embedding_dimension())
            except Exception:  # pragma: no cover - 老版本可能没有该方法
                actual = None
            if actual is not None and actual != self._dimension:
                raise EmbeddingConfigError(
                    f"本地模型维度 {actual} 与配置 RAG_EMBEDDING_DIM={self._dimension} 不一致，"
                    "请修正配置后重建索引"
                )
            self._model = model
        return self._model

    def encode(
        self,
        texts: Sequence[str],
        batch_size: int | None = None,
    ) -> list[list[float]]:
        items = ["" if text is None else str(text) for text in texts]
        if not items:
            return []
        model = self._ensure_model()
        size = max(1, int(batch_size)) if batch_size else self._batch_size
        raw = model.encode(  # type: ignore[attr-defined]
            items,
            batch_size=size,
            normalize_embeddings=False,
            show_progress_bar=False,
        )
        return [[float(value) for value in row] for row in raw]


# ----------------------------------------------------------------------
# 3. 测试替身
# ----------------------------------------------------------------------
class FakeEmbeddingProvider(EmbeddingProvider):
    """确定性离线替身（**仅测试用**）。

    实现为 bag-of-tokens 哈希桶：同一文本永远得到同一向量，共享 token 越多的
    文本余弦相似度越高。这样混合检索/RRF 的用例可以用「真实中文文本」验证排序，
    又完全不依赖网络与模型。
    """

    def __init__(self, dimension: int = FAKE_DEFAULT_DIMENSION) -> None:
        self._dimension = max(1, int(dimension))

    @property
    def name(self) -> str:
        return "fake"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model(self) -> str:
        return f"fake-hash-{self._dimension}"

    def encode(
        self,
        texts: Sequence[str],
        batch_size: int | None = None,
    ) -> list[list[float]]:
        return [self._vectorize("" if text is None else str(text)) for text in texts]

    def _vectorize(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token in tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimension
            vector[bucket] += 1.0
        return vector


# ----------------------------------------------------------------------
# 工厂：失败返回 None（绝不抛异常，保证检索层可降级）
# ----------------------------------------------------------------------
def get_embedding_provider(
    provider: str | None = None,
    *,
    model_path: str | None = None,
    dimension: int | None = None,
    transport: httpx.BaseTransport | None = None,
) -> EmbeddingProvider | None:
    """按 ``RAG_EMBEDDING_PROVIDER``（或显式参数）构造 provider。

    取值：

    - ``auto``（默认）：有本地模型路径先试 ``local``，否则试 ``dashscope``；
      都不可用 → 警告 + 返回 ``None``，检索层降级 BM25；
    - ``local`` / ``dashscope``：显式指定但不可用时同样只告警，不抛异常；
    - ``fake``：测试替身（64 维，确定性）。

    任何预期内失败（缺 Key / 缺路径 / 缺依赖）都会转成 ``None``；
    非预期异常也会被兜住并记录 ERROR，避免初始化阶段打断服务启动。
    """
    requested = (provider or rag_env("RAG_EMBEDDING_PROVIDER", "auto") or "auto").strip().lower()
    if requested not in EMBEDDING_PROVIDERS:
        logger.warning(
            f"未知的 RAG_EMBEDDING_PROVIDER={requested!r}，回退 auto（可选：{'/'.join(EMBEDDING_PROVIDERS)}）",
            extra={"code": "RAG_EMBEDDING_PROVIDER_INVALID", "provider": requested},
        )
        requested = "auto"
    path = model_path if model_path is not None else rag_env("RAG_EMBEDDING_MODEL_PATH")
    resolved_dimension = dimension if dimension is not None else resolve_embedding_dimension()

    if requested == "fake":
        return FakeEmbeddingProvider(dimension if dimension is not None else FAKE_DEFAULT_DIMENSION)

    if requested in ("auto", "local"):
        if path:
            try:
                return LocalEmbeddingProvider(path, dimension=resolved_dimension)
            except EmbeddingConfigError as exc:
                logger.warning(str(exc), extra={"code": "RAG_EMBEDDING_LOCAL_UNAVAILABLE", "provider": "local"})
                if requested == "local":
                    return None
            except Exception as exc:  # noqa: BLE001 - 本地模型差异太大，兜住即可
                logger.warning(
                    f"本地 embedding 初始化失败（{type(exc).__name__}: {exc}），跳过",
                    extra={"code": "RAG_EMBEDDING_LOCAL_UNAVAILABLE", "provider": "local"},
                )
                if requested == "local":
                    return None
        elif requested == "local":
            logger.warning(
                "RAG_EMBEDDING_PROVIDER=local 但未配置 RAG_EMBEDDING_MODEL_PATH，本地 embedding 不可用",
                extra={"code": "RAG_EMBEDDING_LOCAL_UNAVAILABLE", "provider": "local"},
            )
            return None

    if requested in ("auto", "dashscope"):
        try:
            return DashScopeEmbeddingProvider(
                model=None,
                dimension=resolved_dimension,
                transport=transport,
            )
        except EmbeddingConfigError as exc:
            logger.warning(str(exc), extra={"code": "RAG_EMBEDDING_DASHSCOPE_UNAVAILABLE", "provider": "dashscope"})
            return None
        except Exception as exc:  # noqa: BLE001 - 初始化异常也不能打断服务
            logger.warning(
                f"DashScope embedding 初始化失败（{type(exc).__name__}: {exc}），检索将降级 BM25",
                extra={"code": "RAG_EMBEDDING_DASHSCOPE_UNAVAILABLE", "provider": "dashscope"},
            )
            return None

    return None  # pragma: no cover - 逻辑上不可达
