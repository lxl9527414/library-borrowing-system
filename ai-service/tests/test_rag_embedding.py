"""W3-2 Embedding Provider 测试（**全离线**：不调用任何真实 API，不需要网络）。

覆盖点：

1. ``FakeEmbeddingProvider``：确定性、维度、空输入、语义相似度可用于混合检索测试；
2. ``DashScopeEmbeddingProvider``：缺 Key 中文报错、分批 ≤10、index 乱序对齐、
   429/5xx 指数退避重试、非重试状态码快速失败、维度不符报错、错误信息不泄露 Key；
3. ``LocalEmbeddingProvider``：模型路径缺失 → 失败（本机拉不到模型），只验证降级路径；
4. ``get_embedding_provider()``：auto/local/dashscope/fake 的解析与「失败返回 None 不抛异常」；
5. 维度配置读取优先 ``RAG_EMBEDDING_DIM``（.env 模板用名），兼容 ``RAG_EMBEDDING_DIMENSION``。
"""

from __future__ import annotations

import json
import logging
import math

import httpx
import pytest

from app.rag import embedding as embedding_module
from app.rag.embedding import (
    DASHSCOPE_MAX_BATCH_SIZE,
    DashScopeEmbeddingProvider,
    EmbeddingConfigError,
    EmbeddingError,
    EmbeddingProvider,
    FakeEmbeddingProvider,
    LocalEmbeddingProvider,
    get_embedding_provider,
    resolve_embedding_dimension,
)

LOGGER = "ai-service.rag.embedding"


# ----------------------------------------------------------------------
# 离线工具
# ----------------------------------------------------------------------
def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    na = math.sqrt(sum(a * a for a in left))
    nb = math.sqrt(sum(b * b for b in right))
    return dot / (na * nb) if na and nb else 0.0


def _response(vectors: list[list[float]], *, reverse: bool = False) -> dict:
    data = [
        {"object": "embedding", "index": index, "embedding": vector}
        for index, vector in enumerate(vectors)
    ]
    if reverse:
        data.reverse()
    return {"object": "list", "model": "text-embedding-v3", "data": data}


class _RecordingTransport(httpx.BaseTransport):
    """记录每个请求的 JSON body，并按预设脚本返回响应（第 N 次调用取 scripts[N]）。"""

    def __init__(self, scripts: list[httpx.Response]) -> None:
        self.scripts = scripts
        self.requests: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content.decode("utf-8")))
        index = min(len(self.requests) - 1, len(self.scripts) - 1)
        return self.scripts[index]


def _provider(transport: _RecordingTransport, *, dimension: int = 4) -> DashScopeEmbeddingProvider:
    return DashScopeEmbeddingProvider(
        api_key="test-key-not-real",
        model="text-embedding-v3",
        dimension=dimension,
        max_batch_size=DASHSCOPE_MAX_BATCH_SIZE,
        max_retries=3,
        transport=transport,
    )


# ----------------------------------------------------------------------
# 1. FakeEmbeddingProvider
# ----------------------------------------------------------------------
def test_fake_provider_metadata_and_determinism() -> None:
    provider = FakeEmbeddingProvider()
    assert isinstance(provider, EmbeddingProvider)
    assert provider.name == "fake"
    assert provider.dimension == 64

    first = provider.encode(["逾期罚金怎么算", "三体"])
    second = provider.encode(["逾期罚金怎么算", "三体"])
    assert len(first) == 2
    assert all(len(vector) == 64 for vector in first)
    assert first == second  # 确定性：同文本永远同向量
    assert first[0] != first[1]  # 不同文本不同向量
    assert provider.encode([]) == []


def test_fake_provider_empty_and_similarity_ordering() -> None:
    provider = FakeEmbeddingProvider(dimension=128)
    vectors = provider.encode(
        [
            "逾期罚金按每天零点二元计算",
            "超期不还每天会被扣罚金",
            "今天天气晴朗适合出游",
        ]
    )
    similar = _cosine(vectors[0], vectors[1])
    unrelated = _cosine(vectors[0], vectors[2])
    # bag-of-tokens 哈希桶：共享词越多越相似，够混合检索用例做排序断言
    assert similar > unrelated
    assert len(provider.encode_one("三体")) == 128


# ----------------------------------------------------------------------
# 2. DashScopeEmbeddingProvider：配置与错误
# ----------------------------------------------------------------------
def test_dashscope_missing_key_raises_actionable_chinese_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_module, "resolve_dashscope_api_key", lambda: None)
    with pytest.raises(EmbeddingConfigError) as excinfo:
        DashScopeEmbeddingProvider()
    message = str(excinfo.value)
    assert "DASHSCOPE_API_KEY" in message
    assert "BM25" in message  # 降级提示
    assert "缺少" in message


def test_dashscope_blank_explicit_key_rejected() -> None:
    with pytest.raises(EmbeddingConfigError):
        DashScopeEmbeddingProvider(api_key="   ")


def test_dashscope_invalid_dimension_rejected() -> None:
    with pytest.raises(EmbeddingConfigError):
        DashScopeEmbeddingProvider(api_key="test-key", dimension=-5)


def test_dashscope_payload_contains_model_batch_and_dimensions() -> None:
    transport = _RecordingTransport([httpx.Response(200, json=_response([[0.1] * 4]))])
    provider = _provider(transport, dimension=4)
    assert provider.encode(["三体"]) == [[0.1] * 4]
    payload = transport.requests[0]
    assert payload["model"] == "text-embedding-v3"
    assert payload["input"] == ["三体"]
    assert payload["dimensions"] == 4
    provider.close()


# ----------------------------------------------------------------------
# 3. DashScopeEmbeddingProvider：分批与响应解析
# ----------------------------------------------------------------------
def test_dashscope_batches_more_than_ten_requests() -> None:
    texts = [f"文本-{index}" for index in range(23)]
    calls: list[int] = []
    seen_texts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        calls.append(len(payload["input"]))
        # 向量值编码全局序号，便于验证「按 index 对齐」与「按输入顺序返回」
        vectors = []
        for text in payload["input"]:
            seen_texts.append(text)
            global_index = int(text.split("-")[1])
            vectors.append([float(global_index)] * 4)
        return httpx.Response(200, json=_response(vectors, reverse=True))

    provider = DashScopeEmbeddingProvider(
        api_key="test-key",
        dimension=4,
        transport=httpx.MockTransport(handler),
    )
    vectors = provider.encode(texts)
    assert len(vectors) == 23
    assert calls == [10, 10, 3]  # 官方单批上限 10
    assert seen_texts == texts  # 请求顺序与输入一致
    # 响应在 mock 里被 reverse 了，仍须按 index 还原成输入顺序
    assert vectors[0] == [0.0] * 4
    assert vectors[10] == [10.0] * 4
    assert vectors[22] == [22.0] * 4
    provider.close()


def test_dashscope_explicit_batch_size_is_respected() -> None:
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append(len(payload["input"]))
        return httpx.Response(200, json=_response([[0.1] * 4 for _ in payload["input"]]))

    provider = DashScopeEmbeddingProvider(
        api_key="test-key", dimension=4, transport=httpx.MockTransport(handler)
    )
    provider.encode(["a", "b", "c", "d", "e"], batch_size=2)
    assert seen == [2, 2, 1]
    provider.close()


def test_dashscope_batch_size_cannot_exceed_official_limit() -> None:
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append(len(payload["input"]))
        return httpx.Response(200, json=_response([[0.0] * 4 for _ in payload["input"]]))

    provider = DashScopeEmbeddingProvider(
        api_key="test-key", dimension=4, transport=httpx.MockTransport(handler)
    )
    provider.encode([str(index) for index in range(25)], batch_size=100)
    assert seen == [10, 10, 5]
    provider.close()


def test_dashscope_empty_input_makes_no_request() -> None:
    transport = _RecordingTransport([])
    provider = _provider(transport)
    assert provider.encode([]) == []
    assert transport.requests == []
    provider.close()


# ----------------------------------------------------------------------
# 4. DashScopeEmbeddingProvider：重试与失败
# ----------------------------------------------------------------------
def test_dashscope_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_module.time, "sleep", lambda seconds: None)
    transport = _RecordingTransport(
        [
            httpx.Response(429, text="throttled"),
            httpx.Response(200, json=_response([[0.5] * 4])),
        ]
    )
    provider = _provider(transport)
    assert provider.encode(["三体"]) == [[0.5] * 4]
    assert len(transport.requests) == 2
    provider.close()


def test_dashscope_retries_on_503_until_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_module.time, "sleep", lambda seconds: None)
    transport = _RecordingTransport([httpx.Response(503, text="unavailable")])
    provider = DashScopeEmbeddingProvider(
        api_key="test-key", dimension=4, max_retries=2, transport=transport
    )
    with pytest.raises(EmbeddingError) as excinfo:
        provider.encode(["三体"])
    assert "503" in str(excinfo.value)
    assert len(transport.requests) == 2
    provider.close()


def test_dashscope_non_retryable_status_fails_fast_and_hides_key() -> None:
    secret = "sk-should-never-appear"
    transport = _RecordingTransport([httpx.Response(401, text="invalid api key")])
    provider = DashScopeEmbeddingProvider(api_key=secret, dimension=4, transport=transport)
    with pytest.raises(EmbeddingError) as excinfo:
        provider.encode(["三体"])
    message = str(excinfo.value)
    assert "HTTP 401" in message
    assert secret not in message  # 不泄露 Key
    assert len(transport.requests) == 1  # 401 不重试
    provider.close()


def test_dashscope_dimension_mismatch_raises() -> None:
    transport = _RecordingTransport([httpx.Response(200, json=_response([[0.0] * 8]))])
    provider = _provider(transport, dimension=4)
    with pytest.raises(EmbeddingError) as excinfo:
        provider.encode(["三体"])
    assert "维度" in str(excinfo.value)
    assert "1024" in str(excinfo.value)  # 报错里给 text-embedding-v3 的正确维度提示
    provider.close()


def test_dashscope_wrong_item_count_raises() -> None:
    transport = _RecordingTransport([httpx.Response(200, json=_response([[0.0] * 4]))])
    provider = _provider(transport)
    with pytest.raises(EmbeddingError) as excinfo:
        provider.encode(["一", "二"])
    assert "条数" in str(excinfo.value)
    provider.close()


def test_dashscope_nan_embedding_rejected() -> None:
    body = b'{"data":[{"index":0,"embedding":[NaN,NaN,NaN,NaN]}]}'
    transport = _RecordingTransport([httpx.Response(200, content=body)])
    provider = _provider(transport)
    with pytest.raises(EmbeddingError) as excinfo:
        provider.encode(["三体"])
    assert "NaN" in str(excinfo.value)
    provider.close()


def test_dashscope_network_error_retried_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_module.time, "sleep", lambda seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = DashScopeEmbeddingProvider(
        api_key="test-key", dimension=4, max_retries=2, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(EmbeddingError) as excinfo:
        provider.encode(["三体"])
    assert "ConnectError" in str(excinfo.value)
    provider.close()


# ----------------------------------------------------------------------
# 5. LocalEmbeddingProvider：只验证降级（模型下载在当前环境不可用）
# ----------------------------------------------------------------------
def test_local_provider_missing_path_degraded() -> None:
    with pytest.raises(EmbeddingConfigError) as excinfo:
        LocalEmbeddingProvider(None)
    assert "RAG_EMBEDDING_MODEL_PATH" in str(excinfo.value)


def test_local_provider_nonexistent_path_degraded(tmp_path) -> None:
    with pytest.raises(EmbeddingConfigError) as excinfo:
        LocalEmbeddingProvider(tmp_path / "not-downloaded-model")
    assert "路径不存在" in str(excinfo.value)


def test_local_provider_unloadable_dir_raises_chinese_error(tmp_path) -> None:
    # 目录存在但不是合法模型目录；sentence-transformers 已装 → 走「模型加载失败」
    # 未装（CI 极简环境）→ 走「未安装」提示。两者都必须是 EmbeddingConfigError。
    provider = LocalEmbeddingProvider(tmp_path, dimension=8)
    assert provider.name == "local"
    assert provider.dimension == 8
    with pytest.raises(EmbeddingConfigError) as excinfo:
        provider.encode(["三体"])
    message = str(excinfo.value)
    assert "sentence-transformers" in message or "模型加载失败" in message


# ----------------------------------------------------------------------
# 6. get_embedding_provider：解析与降级
# ----------------------------------------------------------------------
def test_get_embedding_provider_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "fake")
    provider = get_embedding_provider()
    assert isinstance(provider, FakeEmbeddingProvider)
    assert provider.dimension == 64
    assert get_embedding_provider("fake", dimension=32).dimension == 32


def test_get_embedding_provider_dashscope_missing_key_returns_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setattr(embedding_module, "resolve_dashscope_api_key", lambda: None)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        provider = get_embedding_provider("dashscope")
    assert provider is None  # 不抛异常
    assert "DASHSCOPE_API_KEY" in caplog.text


def test_get_embedding_provider_local_missing_path_returns_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "local")
    monkeypatch.delenv("RAG_EMBEDDING_MODEL_PATH", raising=False)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert get_embedding_provider("local") is None
    assert "RAG_EMBEDDING_MODEL_PATH" in caplog.text


def test_get_embedding_provider_auto_degrades_to_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "auto")
    monkeypatch.delenv("RAG_EMBEDDING_MODEL_PATH", raising=False)
    monkeypatch.setattr(embedding_module, "resolve_dashscope_api_key", lambda: None)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert get_embedding_provider("auto") is None
    assert "DASHSCOPE_API_KEY" in caplog.text  # 说明降级原因，便于定位


def test_get_embedding_provider_invalid_name_falls_back_auto(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("RAG_EMBEDDING_MODEL_PATH", raising=False)
    monkeypatch.setattr(embedding_module, "resolve_dashscope_api_key", lambda: None)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert get_embedding_provider("not-a-provider") is None
    assert "未知" in caplog.text


# ----------------------------------------------------------------------
# 7. 维度配置读取
# ----------------------------------------------------------------------
def _patch_rag_env(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    """把 rag_config 的环境读取替换成固定 map，避免仓库 .env 干扰维度断言。"""
    import app.rag.rag_config as rag_config_module

    monkeypatch.setattr(
        rag_config_module,
        "rag_env",
        lambda name, default=None: values.get(name, default),
    )


def test_resolve_dimension_prefers_rag_embedding_dim(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_rag_env(monkeypatch, {"RAG_EMBEDDING_DIM": "1024", "RAG_EMBEDDING_DIMENSION": "512"})
    assert resolve_embedding_dimension() == 1024


def test_resolve_dimension_falls_back_to_dimension_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_rag_env(monkeypatch, {"RAG_EMBEDDING_DIMENSION": "768"})
    assert resolve_embedding_dimension() == 768


def test_resolve_dimension_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_rag_env(monkeypatch, {})
    assert resolve_embedding_dimension() == 1024
