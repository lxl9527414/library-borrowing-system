"""W3-4 重排模块测试（**全离线**：全部用 ``httpx.MockTransport``，不打真实 API）。

覆盖：
1. 通义 ``gte-rerank-v2`` 解析成功：按分数重排、字段不丢、长度 ≤ top_k、请求体正确；
2. 失败降级：HTTP 500 / 超时 / 连接错误 / 非 JSON / 结构异常 → 原顺序前 top_k，不抛异常；
3. 无 Key → 降级 Noop（工厂）/ 直退（实例）；
4. top_n 截断、空候选、单候选、top_k<=0；
5. ``get_reranker()`` 工厂：开关关闭、无 Key、provider=none/未知/别名、默认 dashscope；
6. DeepSeek listwise 可选实现：解析成功 / 代码块包裹 / 解析失败回退；
7. 统计字段 ``last_stats``（耗时、候选数、返回数、ok/fallback/error/scores）。
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.rag.rerank import (
    DASHSCOPE_RERANK_MODEL,
    DASHSCOPE_RERANK_URL,
    DEFAULT_MAX_CHARS,
    DEFAULT_TIMEOUT_SECONDS,
    DashScopeReranker,
    LLMReranker,
    NoopReranker,
    get_reranker,
)

LOGGER_NAME = "ai-service.rag.rerank"


# ----------------------------------------------------------------------
# fixtures / helpers
# ----------------------------------------------------------------------
@pytest.fixture
def candidates() -> list[dict]:
    """4 个候选，字段与 RagService.search() 返回结构一致（外加一个自定义字段）。"""
    return [
        {
            "chunk_id": "faq:4",
            "text": "问：罚金怎么交？答：归还图书时系统自动结算，可在「我的借阅」中查看。",
            "source_type": "faq",
            "title": "罚金怎么交？",
            "book_id": None,
            "category": "常见问题",
            "url": None,
            "score": 0.62,
            "custom_field": {"nested": [1, 2]},
        },
        {
            "chunk_id": "book:262",
            "text": "《三体》 作者：刘慈欣 | 分类：文学 | 简介：科幻小说。",
            "source_type": "book",
            "title": "三体",
            "book_id": 262,
            "category": "文学",
            "url": "/books?id=262",
            "score": 0.30,
            "custom_field": {"nested": [3]},
        },
        {
            "chunk_id": "rule:1:2",
            "text": (
                "逾期与罚金：图书超过应还日期未还即视为逾期，逾期罚金按每天 0.20 元累计，"
                "从应还日次日开始计算，单笔罚金封顶 20.00 元。"
            ),
            "source_type": "rule",
            "title": "借阅规则 · 逾期罚金",
            "book_id": None,
            "category": "借阅规则",
            "url": None,
            "score": 0.55,
            "custom_field": {"nested": [4, 5]},
        },
        {
            "chunk_id": "faq:0",
            "text": "问：第一次借书要怎么操作？答：登录后进入图书详情页点击借阅即可。",
            "source_type": "faq",
            "title": "第一次借书要怎么操作？",
            "book_id": None,
            "category": "常见问题",
            "url": None,
            "score": 0.12,
            "custom_field": None,
        },
    ]


@pytest.fixture
def mock_client():
    """工厂：``mock_client(handler)`` → 注入 MockTransport 的 httpx.Client。"""
    clients: list[httpx.Client] = []

    def factory(handler) -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


def _ids(items: list[dict]) -> list[str]:
    return [item["chunk_id"] for item in items]


def _assert_original_order_fallback(
    result: list[dict], candidates: list[dict], limit: int
) -> None:
    """断言回退结果是「原顺序前 limit 条」，且字段一个不少。"""
    assert _ids(result) == _ids(candidates[:limit])
    for original, returned in zip(candidates[:limit], result, strict=True):
        assert set(returned) == set(original)
        assert returned["custom_field"] == original["custom_field"]


def _results_response(index_score: list[tuple[int, float]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "output": {
                "results": [
                    {"index": index, "relevance_score": score}
                    for index, score in index_score
                ]
            }
        },
    )


# ----------------------------------------------------------------------
# 1. DashScopeReranker：解析成功
# ----------------------------------------------------------------------
def test_dashscope_rerank_reorders_by_score_and_keeps_fields(
    candidates: list[dict], mock_client
) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return _results_response([(2, 0.91), (0, 0.25)])

    reranker = DashScopeReranker(api_key="test-key", client=mock_client(handler))
    result = reranker.rerank("逾期罚金怎么算", candidates, top_k=2)

    assert _ids(result) == ["rule:1:2", "faq:4"]
    assert len(result) <= 2
    # 原有字段全部保留（含自定义字段），且是浅拷贝、不污染输入
    assert set(result[0]) == set(candidates[2])
    assert result[0] is not candidates[2]
    assert result[0]["custom_field"] == {"nested": [4, 5]}

    # 请求契约
    assert captured["url"] == DASHSCOPE_RERANK_URL
    assert captured["auth"] == "Bearer test-key"
    payload = captured["payload"]
    assert payload["model"] == DASHSCOPE_RERANK_MODEL == "gte-rerank-v2"
    assert payload["input"]["query"] == "逾期罚金怎么算"
    assert len(payload["input"]["documents"]) == len(candidates)
    assert payload["parameters"] == {"return_documents": False, "top_n": 2}

    # 统计字段
    stats = reranker.last_stats
    assert stats["ok"] is True
    assert stats["fallback"] is False
    assert stats["error"] is None
    assert stats["candidates"] == 4
    assert stats["requested"] == 2
    assert stats["returned"] == 2
    assert stats["scores"] == [0.91, 0.25]
    assert stats["elapsed_ms"] >= 0
    assert reranker.last_candidate_count == 4
    assert reranker.last_elapsed_ms == stats["elapsed_ms"]


def test_dashscope_top_n_is_min_top_k_and_candidate_count(
    candidates: list[dict], mock_client
) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        # 返回 4、3 两个下标（模拟接口已按相关度排好序）
        return _results_response([(2, 0.8), (1, 0.4)])

    reranker = DashScopeReranker(api_key="k", client=mock_client(handler))
    result = reranker.rerank("罚金", candidates, top_k=2)

    assert captured["payload"]["parameters"]["top_n"] == 2
    assert _ids(result) == ["rule:1:2", "book:262"]
    assert len(result) == 2


def test_dashscope_truncates_each_document_to_max_chars(mock_client) -> None:
    candidates = [
        {"chunk_id": "long", "text": "逾" * 2000, "source_type": "rule"},
    ]
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return _results_response([(0, 0.5)])

    reranker = DashScopeReranker(
        api_key="k", max_chars=512, client=mock_client(handler)
    )
    assert reranker.rerank("逾期罚金", candidates, top_k=1)
    assert len(captured["payload"]["input"]["documents"][0]) == 512

    # 自定义更短截断也生效
    captured.clear()
    short = DashScopeReranker(api_key="k", max_chars=10, client=mock_client(handler))
    assert short.rerank("逾期罚金", candidates, top_k=1)
    assert len(captured["payload"]["input"]["documents"][0]) == 10


def test_dashscope_uses_title_when_text_missing(mock_client) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return _results_response([(0, 0.3)])

    candidates = [{"chunk_id": "rule:1", "text": "", "title": "逾期罚金", "source_type": "rule"}]
    reranker = DashScopeReranker(api_key="k", client=mock_client(handler))
    assert reranker.rerank("罚金", candidates, top_k=1)
    assert captured["payload"]["input"]["documents"] == ["逾期罚金"]


def test_dashscope_ties_sorted_by_original_index(mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _results_response([(2, 0.5), (1, 0.5)])

    candidates = [
        {"chunk_id": f"c{i}", "text": f"文档{i}", "source_type": "faq"} for i in range(3)
    ]
    reranker = DashScopeReranker(api_key="k", client=mock_client(handler))
    assert _ids(reranker.rerank("q", candidates, top_k=2)) == ["c1", "c2"]


# ----------------------------------------------------------------------
# 2. 失败降级：绝不抛异常，原顺序前 top_k
# ----------------------------------------------------------------------
def test_dashscope_http_500_falls_back(
    candidates: list[dict], mock_client, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": "InternalError"})

    reranker = DashScopeReranker(api_key="k", client=mock_client(handler))
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = reranker.rerank("逾期罚金怎么算", candidates, top_k=2)

    _assert_original_order_fallback(result, candidates, 2)
    assert reranker.last_stats["ok"] is False
    assert reranker.last_stats["fallback"] is True
    assert "500" in reranker.last_stats["error"]
    assert "重排失败" in caplog.text


def test_dashscope_timeout_falls_back(candidates: list[dict], mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    reranker = DashScopeReranker(api_key="k", timeout_seconds=20, client=mock_client(handler))
    result = reranker.rerank("逾期罚金怎么算", candidates, top_k=3)  # 不抛异常

    _assert_original_order_fallback(result, candidates, 3)
    assert reranker.last_stats["fallback"] is True
    assert "Timeout" in reranker.last_stats["error"]


def test_dashscope_connection_error_falls_back(candidates: list[dict], mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    reranker = DashScopeReranker(api_key="k", client=mock_client(handler))
    result = reranker.rerank("罚金", candidates, top_k=2)

    _assert_original_order_fallback(result, candidates, 2)
    assert "ConnectError" in reranker.last_stats["error"]


def test_dashscope_non_json_body_falls_back(candidates: list[dict], mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway error</html>")

    reranker = DashScopeReranker(api_key="k", client=mock_client(handler))
    result = reranker.rerank("罚金", candidates, top_k=2)

    _assert_original_order_fallback(result, candidates, 2)
    assert reranker.last_stats["fallback"] is True


@pytest.mark.parametrize(
    "bad_body",
    [
        {"output": {}},
        {"output": {"results": []}},
        {"output": {"results": "oops"}},
        {"output": {"results": [{"index": 0}]}},
        {"output": {"results": [{"index": 99, "relevance_score": 0.9}]}},
        {"output": {"results": [{"index": 0, "relevance_score": 0.5}, "bad"]}},
        {},
    ],
)
def test_dashscope_malformed_response_falls_back(
    bad_body: dict, candidates: list[dict], mock_client
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bad_body)

    reranker = DashScopeReranker(api_key="k", client=mock_client(handler))
    result = reranker.rerank("罚金", candidates, top_k=2)

    _assert_original_order_fallback(result, candidates, 2)
    assert reranker.last_stats["ok"] is False
    assert reranker.last_stats["fallback"] is True
    assert reranker.last_stats["error"]


def test_dashscope_no_api_key_falls_back_without_request(
    candidates: list[dict], mock_client, caplog: pytest.LogCaptureFixture
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _results_response([(0, 1.0)])

    reranker = DashScopeReranker(api_key="", client=mock_client(handler))
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        result = reranker.rerank("罚金", candidates, top_k=2)  # 不抛异常

    _assert_original_order_fallback(result, candidates, 2)
    assert calls["n"] == 0
    assert "DASHSCOPE_API_KEY" in reranker.last_stats["error"]
    assert "DASHSCOPE_API_KEY" in caplog.text


def test_dashscope_blank_query_falls_back_without_request(
    candidates: list[dict], mock_client
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _results_response([(0, 1.0)])

    reranker = DashScopeReranker(api_key="k", client=mock_client(handler))
    result = reranker.rerank("   ", candidates, top_k=2)

    _assert_original_order_fallback(result, candidates, 2)
    assert calls["n"] == 0
    assert "query 为空" in reranker.last_stats["error"]


# ----------------------------------------------------------------------
# 3. 边界：空候选 / 单候选 / top_k<=0
# ----------------------------------------------------------------------
def test_dashscope_empty_candidates_returns_empty_without_request(mock_client) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _results_response([(0, 1.0)])

    reranker = DashScopeReranker(api_key="k", client=mock_client(handler))
    assert reranker.rerank("罚金", [], top_k=5) == []
    assert calls["n"] == 0
    assert reranker.last_stats["candidates"] == 0
    assert reranker.last_stats["returned"] == 0


def test_dashscope_single_candidate(mock_client) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return _results_response([(0, 0.77)])

    candidates = [{"chunk_id": "rule:1", "text": "逾期罚金规则", "source_type": "rule"}]
    reranker = DashScopeReranker(api_key="k", client=mock_client(handler))
    result = reranker.rerank("罚金", candidates, top_k=5)

    assert _ids(result) == ["rule:1"]
    assert captured["payload"]["parameters"]["top_n"] == 1
    assert reranker.last_stats["requested"] == 1


def test_dashscope_non_positive_top_k_returns_empty(mock_client) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _results_response([(0, 1.0)])

    reranker = DashScopeReranker(api_key="k", client=mock_client(handler))
    candidates = [{"chunk_id": "c0", "text": "x", "source_type": "faq"}]

    assert reranker.rerank("q", candidates, top_k=0) == []
    assert reranker.rerank("q", candidates, top_k=-3) == []
    assert calls["n"] == 0


# ----------------------------------------------------------------------
# 4. NoopReranker
# ----------------------------------------------------------------------
def test_noop_reranker_passthrough(candidates: list[dict]) -> None:
    reranker = NoopReranker(reason="unit-test")
    result = reranker.rerank("罚金", candidates, top_k=2)

    assert reranker.name == "none"
    assert _ids(result) == _ids(candidates[:2])
    # 直通返回原 dict 引用，字段必然完整
    assert result[0] is candidates[0]
    assert reranker.last_stats["ok"] is True
    assert reranker.last_stats["fallback"] is False
    assert reranker.last_stats["candidates"] == 4
    assert reranker.last_stats["returned"] == 2


def test_noop_reranker_edge_cases(candidates: list[dict]) -> None:
    reranker = NoopReranker()
    assert reranker.rerank("q", [], top_k=5) == []
    assert reranker.rerank("q", candidates, top_k=0) == []
    assert len(reranker.rerank("", candidates, top_k=1)) == 1


# ----------------------------------------------------------------------
# 5. get_reranker() 工厂
# ----------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_rerank_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例从明确的空配置开始，避免受真实 .env / 上一用例污染。"""
    for name in (
        "RAG_RERANK_ENABLED",
        "RAG_RERANK_PROVIDER",
        "RAG_RERANK_MAX_CHARS",
        "RAG_RERANK_TIMEOUT_SECONDS",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        monkeypatch.setenv(name, "")


def test_factory_disabled_returns_noop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RAG_RERANK_ENABLED", "false")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        reranker = get_reranker()

    assert isinstance(reranker, NoopReranker)
    assert reranker.name == "none"
    assert "关闭" in caplog.text


def test_factory_dashscope_missing_key_returns_noop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RAG_RERANK_ENABLED", "true")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        reranker = get_reranker()

    assert isinstance(reranker, NoopReranker)
    assert "DASHSCOPE_API_KEY" in caplog.text
    assert "DASHSCOPE_API_KEY" in reranker.reason


def test_factory_default_provider_is_dashscope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_RERANK_ENABLED", "true")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    # provider 未配置 → 默认 dashscope

    reranker = get_reranker()

    assert isinstance(reranker, DashScopeReranker)
    assert reranker.name == "dashscope-gte-rerank-v2"
    assert reranker.api_key == "test-key"
    assert reranker.max_chars == DEFAULT_MAX_CHARS == 512
    assert reranker.timeout_seconds == DEFAULT_TIMEOUT_SECONDS == 20.0


def test_factory_reads_max_chars_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_RERANK_ENABLED", "true")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("RAG_RERANK_MAX_CHARS", "200")
    monkeypatch.setenv("RAG_RERANK_TIMEOUT_SECONDS", "7.5")

    reranker = get_reranker()

    assert isinstance(reranker, DashScopeReranker)
    assert reranker.max_chars == 200
    assert reranker.timeout_seconds == 7.5


def test_factory_provider_none_returns_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_RERANK_ENABLED", "true")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "none")

    assert isinstance(get_reranker(), NoopReranker)


def test_factory_unknown_provider_returns_noop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "cohere")

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        reranker = get_reranker()

    assert isinstance(reranker, NoopReranker)
    assert "未知重排 provider" in caplog.text


def test_factory_provider_alias_and_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "DASHSCOPE")
    assert isinstance(get_reranker(), DashScopeReranker)

    # 旧值 llm / qwen 也兼容
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "qwen")
    assert isinstance(get_reranker(), DashScopeReranker)

    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "llm")
    reranker = get_reranker()
    assert isinstance(reranker, LLMReranker)
    assert reranker.name == "deepseek-listwise"


def test_factory_deepseek_missing_key_returns_noop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("RAG_RERANK_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        reranker = get_reranker()

    assert isinstance(reranker, NoopReranker)
    assert "DEEPSEEK_API_KEY" in caplog.text


def test_factory_rereads_config_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """工厂不缓存：同一进程内改环境变量后下次调用立即生效（评测/测试友好）。"""
    monkeypatch.setenv("RAG_RERANK_ENABLED", "false")
    assert isinstance(get_reranker(), NoopReranker)

    monkeypatch.setenv("RAG_RERANK_ENABLED", "true")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    assert isinstance(get_reranker(), DashScopeReranker)


# ----------------------------------------------------------------------
# 6. LLMReranker（可选实现，默认不走）
# ----------------------------------------------------------------------
def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        },
    )


def test_llm_reranker_parses_ranking(candidates: list[dict], mock_client) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return _chat_response('{"ranking": [2, 0, 1]}')

    reranker = LLMReranker(api_key="ds-key", client=mock_client(handler))
    result = reranker.rerank("逾期罚金怎么算", candidates, top_k=2)

    assert _ids(result) == ["rule:1:2", "faq:4"]
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer ds-key"
    assert captured["payload"]["model"] == "deepseek-chat"
    assert captured["payload"]["temperature"] == 0
    assert "逾期罚金怎么算" in captured["payload"]["messages"][-1]["content"]
    assert reranker.last_stats["ok"] is True
    assert reranker.last_stats["returned"] == 2


def test_llm_reranker_accepts_markdown_fence(candidates: list[dict], mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response('```json\n{"ranking": [1, 2]}\n```')

    reranker = LLMReranker(api_key="ds-key", client=mock_client(handler))
    result = reranker.rerank("罚金", candidates, top_k=3)

    # 未排到的下标按原顺序补在末尾
    assert _ids(result) == ["book:262", "rule:1:2", "faq:4"]


@pytest.mark.parametrize(
    "content",
    [
        "我觉得第二个更相关",
        "{}",
        '{"ranking": []}',
        '{"ranking": [9, 0]}',
        '{"ranking": [0, 0]}',
        '{"ranking": "0,1"}',
    ],
)
def test_llm_reranker_parse_failure_falls_back(
    content: str, candidates: list[dict], mock_client
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response(content)

    reranker = LLMReranker(api_key="ds-key", client=mock_client(handler))
    result = reranker.rerank("罚金", candidates, top_k=2)

    _assert_original_order_fallback(result, candidates, 2)
    assert reranker.last_stats["ok"] is False
    assert reranker.last_stats["fallback"] is True


def test_llm_reranker_http_error_falls_back(candidates: list[dict], mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    reranker = LLMReranker(api_key="ds-key", client=mock_client(handler))
    result = reranker.rerank("罚金", candidates, top_k=2)

    _assert_original_order_fallback(result, candidates, 2)
    assert "503" in reranker.last_stats["error"]


def test_llm_reranker_without_key_falls_back(candidates: list[dict], mock_client) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _chat_response('{"ranking": [0]}')

    reranker = LLMReranker(api_key="", client=mock_client(handler))
    result = reranker.rerank("罚金", candidates, top_k=2)

    _assert_original_order_fallback(result, candidates, 2)
    assert calls["n"] == 0
    assert "DEEPSEEK_API_KEY" in reranker.last_stats["error"]
