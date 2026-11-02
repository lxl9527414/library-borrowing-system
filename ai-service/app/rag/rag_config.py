"""RAG 检索增强（W3-2 / W3-3b / W3-4）的配置读取。

为什么单独放一个模块，而不是加进 ``app/config.py``：

- ``app/config.py`` 是服务主配置（LLM / Java / Redis…），由 ``Settings`` 统一校验；
  RAG 的开关（``RAG_RETRIEVAL_MODE`` / ``RAG_RERANK_*`` / ``RAG_EMBEDDING_*``）属于
  **检索层的可选增强**，默认不配置也必须能跑，所以这里用「读不到就用默认值」的宽松策略，
  不给主配置增加必填项。
- 读取优先级：**进程环境变量 > ai-service/.env > 仓库根 .env > 代码默认值**，
  与 ``app/config.py`` 的 ``env_file`` 顺序保持一致（后加载的 ai-service/.env 覆盖根 .env）。
  这样 ``RAG_RERANK_ENABLED=false .venv/bin/python scripts/run_eval.py`` 这种
  命令行前缀写法可以直接生效，生产环境写进 ``.env`` 也能生效。
- 不做进程级缓存：每次构造检索服务时重新读一遍（成本 ~0.1ms，.env 解析结果带缓存），
  避免测试里 monkeypatch 环境变量后受上一个用例的缓存值污染。

安全约定：本模块**只读取** Key 是否存在，绝不打印 Key 内容。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

# ai-service/app/rag/rag_config.py -> ai-service/
SERVICE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SERVICE_ROOT.parent
EMBEDDINGS_DIR = SERVICE_ROOT / "data" / "embeddings"

# .env 文件加载顺序：前面的先加载，后面的优先级更高（与 app/config.py 保持一致）
_ENV_FILES: tuple[Path, ...] = (REPO_ROOT / ".env", SERVICE_ROOT / ".env")

# 检索模式 / 重排 provider 的合法取值
RETRIEVAL_MODES: tuple[str, ...] = ("bm25", "vector", "hybrid")
RERANK_PROVIDERS: tuple[str, ...] = ("llm", "none")
EMBEDDING_PROVIDERS: tuple[str, ...] = ("auto", "local", "dashscope", "fake")
VECTOR_STORES: tuple[str, ...] = ("numpy", "pgvector")

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "n", "f"})


@lru_cache(maxsize=1)
def _env_file_values() -> Mapping[str, str]:
    """解析 .env 文件（不写入 os.environ，避免污染子进程）。"""
    merged: dict[str, str] = {}
    for path in _ENV_FILES:
        try:
            if not path.is_file():
                continue
            values = dotenv_values(path, encoding="utf-8")
        except Exception:  # 损坏的 .env 不应让检索层崩掉
            continue
        for key, value in values.items():
            if key and value is not None:
                merged[str(key).strip()] = str(value).strip()
    return merged


def clear_env_file_cache() -> None:
    """清空 .env 解析缓存（测试 / 热更新配置用）。"""
    _env_file_values.cache_clear()


def rag_env(name: str, default: str | None = None) -> str | None:
    """读取一个 RAG 环境变量：os.environ > .env > default。

    空字符串视为「未配置」，返回 ``default``（这样 ``RAG_RERANK_ENABLED=`` 不会变成怪值）。
    """
    raw = os_environ_get(name)
    if raw is None:
        raw = _env_file_values().get(name)
    if raw is None:
        return default
    text = str(raw).strip()
    return text if text else default


def os_environ_get(name: str) -> str | None:
    """独立的 os.environ 读取（便于测试 monkeypatch 与本模块解耦）。"""
    return os.environ.get(name)


def rag_flag(name: str, default: bool) -> bool:
    """解析布尔开关；无法识别时回退默认值。"""
    raw = rag_env(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return default


def rag_int(name: str, default: int, *, minimum: int = 1) -> int:
    """解析正整数；非法值回退默认值（检索层宁可降级也不抛异常）。"""
    raw = rag_env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def rag_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    """解析浮点数；非法值回退默认值。"""
    raw = rag_env(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


@dataclass(frozen=True)
class RagConfig:
    """RAG 检索增强配置快照（构造检索服务时读取一次）。"""

    retrieval_mode: str = "hybrid"
    candidate_k: int = 20
    rrf_k: int = 60

    rerank_enabled: bool = True
    rerank_provider: str = "llm"
    rerank_candidates: int = 20
    rerank_max_chars: int = 200
    rerank_timeout_seconds: float = 20.0
    rerank_max_completion_tokens: int = 256

    embedding_provider: str = "auto"
    embedding_model_path: str | None = None
    embedding_dimension: int = 1024

    vector_store: str = "numpy"
    embeddings_dir: Path = EMBEDDINGS_DIR

    @property
    def effective_retrieval_mode(self) -> str:
        """校验后的检索模式；非法值回退 hybrid。"""
        mode = (self.retrieval_mode or "").strip().lower()
        return mode if mode in RETRIEVAL_MODES else "hybrid"

    @property
    def effective_rerank_provider(self) -> str:
        provider = (self.rerank_provider or "").strip().lower()
        return provider if provider in RERANK_PROVIDERS else "llm"

    @property
    def effective_embedding_provider(self) -> str:
        provider = (self.embedding_provider or "").strip().lower()
        return provider if provider in EMBEDDING_PROVIDERS else "auto"

    @property
    def effective_vector_store(self) -> str:
        store = (self.vector_store or "").strip().lower()
        return store if store in VECTOR_STORES else "numpy"

    def index_paths(self, provider_name: str, dimension: int) -> tuple[Path, Path]:
        """返回某个 embedding 索引的 (向量文件, 元数据文件) 路径。"""
        stem = f"{provider_name}-{int(dimension)}"
        return self.embeddings_dir / f"{stem}.npz", self.embeddings_dir / f"{stem}.meta.json"


def load_rag_config() -> RagConfig:
    """从环境变量 / .env 读取 RAG 配置（每次调用返回新快照）。"""
    default = RagConfig()
    model_path = rag_env("RAG_EMBEDDING_MODEL_PATH") or None
    return RagConfig(
        retrieval_mode=rag_env("RAG_RETRIEVAL_MODE", default.retrieval_mode) or default.retrieval_mode,
        candidate_k=rag_int("RAG_RETRIEVAL_CANDIDATES", default.candidate_k),
        rrf_k=rag_int("RAG_RRF_K", default.rrf_k),
        rerank_enabled=rag_flag("RAG_RERANK_ENABLED", default.rerank_enabled),
        rerank_provider=rag_env("RAG_RERANK_PROVIDER", default.rerank_provider) or default.rerank_provider,
        rerank_candidates=rag_int("RAG_RERANK_CANDIDATES", default.rerank_candidates),
        rerank_max_chars=rag_int("RAG_RERANK_MAX_CHARS", default.rerank_max_chars),
        rerank_timeout_seconds=rag_float(
            "RAG_RERANK_TIMEOUT_SECONDS", default.rerank_timeout_seconds, minimum=1.0
        ),
        rerank_max_completion_tokens=rag_int(
            "RAG_RERANK_MAX_COMPLETION_TOKENS", default.rerank_max_completion_tokens
        ),
        embedding_provider=rag_env("RAG_EMBEDDING_PROVIDER", default.embedding_provider)
        or default.embedding_provider,
        embedding_model_path=model_path,
        embedding_dimension=rag_int("RAG_EMBEDDING_DIMENSION", default.embedding_dimension),
        vector_store=rag_env("RAG_VECTOR_STORE", default.vector_store) or default.vector_store,
        embeddings_dir=EMBEDDINGS_DIR,
    )
