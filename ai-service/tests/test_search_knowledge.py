"""W3-5 search_knowledge 工具 + 引用/拒答提示词测试（全离线）。

覆盖：

1. schema：名称/必填参数/top_k 范围/source_types 枚举、在 TOOLS 中、非写工具；
2. executor 正常命中：契约字段、citation_no 递增、text 截断 500 字、score 降序、summary 计数；
3. 参数校验：top_k 越界/非整数、source_types 未知/类型错误、query 空 → ``TOOL_INVALID_ARGS``；
4. 降级：空结果、检索抛异常、检索服务不可用 → ``ok:true + chunks: []`` + 说明性 summary，不抛异常；
5. 提示词：包含“先检索、引用编号、空结果拒答”规则；
6. 其它：只读工具不触发 Java 后端请求、结果可 JSON 序列化。
"""

from __future__ import annotations

import json

import pytest

from app.agent.prompts import build_system_prompt
from app.tools.executor import (
    DEFAULT_KNOWLEDGE_TOP_K,
    EMPTY_KNOWLEDGE_SUMMARY,
    MAX_CHUNK_CHARS,
    MAX_KNOWLEDGE_TOP_K,
    CODE_TOOL_INVALID_ARGS,
    CODE_TOOL_NOT_FOUND,
    RAG_UNAVAILABLE_SUMMARY,
    ToolExecutor,
)
from app.tools.schemas import (
    KNOWLEDGE_SOURCE_TYPES,
    READ_TOOLS,
    TOOL_NAMES,
    TOOL_SCHEMAS,
    WRITE_TOOLS,
    is_known_tool,
    is_write_tool,
)
from tests.conftest import build_java_client
from tests.fakes import JavaBackendMock

CHUNK_FIELDS = {
    "citation_no",
    "chunk_id",
    "source_type",
    "title",
    "text",
    "book_id",
    "url",
    "score",
}


# ----------------------------------------------------------------------
# 离线替身：可编排的检索服务
# ----------------------------------------------------------------------
class FakeRagService:
    """模拟 ``RagService.search()``；记录调用参数，可返回结果或抛异常。"""

    def __init__(
        self,
        results: list[dict] | None = None,
        error: Exception | None = None,
        mode: str | None = None,
    ) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[dict] = []
        if mode is not None:  # 只有显式指定时才暴露 retrieval_mode（便于测试阈值分支）
            self.retrieval_mode = mode

    def search(self, query: str, top_k: int = 5, source_types: list[str] | None = None):
        self.calls.append({"query": query, "top_k": top_k, "source_types": source_types})
        if self.error is not None:
            raise self.error
        return [dict(item) if isinstance(item, dict) else item for item in self.results]


def _hit(
    chunk_id: str,
    *,
    source_type: str = "rule",
    title: str = "借阅规则",
    text: str = "规则正文",
    score: float = 0.5,
    book_id: int | None = None,
    url: str | None = None,
) -> dict:
    """构造 RagService.search() 的原始返回项（8 字段，含 category）。"""
    return {
        "chunk_id": chunk_id,
        "text": text,
        "source_type": source_type,
        "title": title,
        "book_id": book_id,
        "category": "测试",
        "url": url,
        "score": score,
    }


def _call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"id": call_id, "name": name, "args": args}


@pytest.fixture
def rag() -> FakeRagService:
    return FakeRagService(
        [
            _hit("faq:0", source_type="faq", title="罚金怎么交？", score=0.42),
            _hit(
                "rule:1:2",
                source_type="rule",
                title="借阅规则 · 逾期罚金",
                text="逾期罚金：图书超过应还日期未还即视为逾期，按每天 0.20 元累计。" * 30,
                score=0.91,
            ),
            _hit("book:1", source_type="book", title="Java编程思想", score=0.73, book_id=1, url="/books?id=1"),
        ]
    )


def _executor(settings, backend: JavaBackendMock, rag: FakeRagService | None) -> ToolExecutor:
    return ToolExecutor(
        build_java_client(settings, backend),
        trace_id="t-test",
        session_id="s-test",
        rag_service=rag,
    )


# ----------------------------------------------------------------------
# 1. schema
# ----------------------------------------------------------------------
def _schema() -> dict:
    return next(
        schema for schema in TOOL_SCHEMAS if schema["function"]["name"] == "search_knowledge"
    )


def test_schema_is_registered_and_read_only() -> None:
    schema = _schema()
    assert schema["type"] == "function"
    assert "search_knowledge" in TOOL_NAMES
    assert is_known_tool("search_knowledge") is True
    assert is_write_tool("search_knowledge") is False
    assert "search_knowledge" in READ_TOOLS
    assert "search_knowledge" not in WRITE_TOOLS


def test_schema_parameters_contract() -> None:
    parameters = _schema()["function"]["parameters"]
    assert parameters["required"] == ["query"]
    properties = parameters["properties"]
    assert properties["query"]["type"] == "string"
    assert properties["top_k"]["type"] == "integer"
    assert properties["top_k"]["minimum"] == 1
    assert properties["top_k"]["maximum"] == MAX_KNOWLEDGE_TOP_K == 10
    assert properties["source_types"]["type"] == "array"
    assert properties["source_types"]["items"]["enum"] == list(KNOWLEDGE_SOURCE_TYPES)
    assert set(KNOWLEDGE_SOURCE_TYPES) == {"book", "rule", "faq"}


def test_schema_description_mentions_knowledge_intents() -> None:
    description = _schema()["function"]["description"]
    for keyword in ("借阅规则", "罚金", "预约", "馆藏事实", "使用流程"):
        assert keyword in description
    assert "citation_no" in description


# ----------------------------------------------------------------------
# 2. executor：正常命中
# ----------------------------------------------------------------------
async def test_executor_search_knowledge_maps_contract(
    settings, backend: JavaBackendMock, rag: FakeRagService
) -> None:
    executor = _executor(settings, backend, rag)
    result = await executor.execute(_call("search_knowledge", {"query": "逾期罚金怎么算"}))

    assert result.ok is True
    assert result.error_code is None
    assert result.is_write is False
    assert set(result.data) == {"chunks"}
    chunks = result.data["chunks"]
    assert len(chunks) == 3

    # 按 score 降序 + citation_no 从 1 递增
    assert [chunk["score"] for chunk in chunks] == [0.91, 0.73, 0.42]
    assert [chunk["citation_no"] for chunk in chunks] == [1, 2, 3]
    assert [chunk["chunk_id"] for chunk in chunks] == ["rule:1:2", "book:1", "faq:0"]

    # 契约字段一个不多一个不少（category 不返回给前端）
    for chunk in chunks:
        assert set(chunk) == CHUNK_FIELDS
    assert chunks[0]["source_type"] == "rule"
    assert chunks[0]["title"] == "借阅规则 · 逾期罚金"
    assert chunks[1]["book_id"] == 1
    assert chunks[1]["url"] == "/books?id=1"

    # text 截断 500 字
    assert len(chunks[0]["text"]) == MAX_CHUNK_CHARS
    assert chunks[0]["text"] == ("逾期罚金：图书超过应还日期未还即视为逾期，按每天 0.20 元累计。" * 30)[:500]

    # summary 含依据数量与来源分布
    assert "检索到 3 段馆藏依据" in result.summary
    assert "规则 1" in result.summary
    assert "FAQ 1" in result.summary
    assert "馆藏 1" in result.summary

    # 检索参数透传
    assert rag.calls == [{"query": "逾期罚金怎么算", "top_k": DEFAULT_KNOWLEDGE_TOP_K, "source_types": None}]
    # 只读工具不 requests Java 后端
    assert backend.requests == []


async def test_executor_search_knowledge_respects_top_k_and_source_types(
    settings, backend: JavaBackendMock
) -> None:
    rag = FakeRagService([_hit(f"rule:{index}", score=1.0 - index * 0.1) for index in range(5)])
    executor = _executor(settings, backend, rag)
    result = await executor.execute(
        _call("search_knowledge", {"query": "借阅规则", "top_k": 2, "source_types": ["rule", "faq"]})
    )
    assert result.ok is True
    assert len(result.data["chunks"]) == 2
    assert [chunk["citation_no"] for chunk in result.data["chunks"]] == [1, 2]
    assert rag.calls[0]["top_k"] == 2
    assert rag.calls[0]["source_types"] == ["rule", "faq"]


async def test_executor_search_knowledge_empty_source_types_means_no_filter(
    settings, backend: JavaBackendMock, rag: FakeRagService
) -> None:
    executor = _executor(settings, backend, rag)
    result = await executor.execute(_call("search_knowledge", {"query": "借阅", "source_types": []}))
    assert result.ok is True
    assert rag.calls[0]["source_types"] is None  # 空数组 = 不过滤


async def test_executor_search_knowledge_dedupes_chunk_ids(
    settings, backend: JavaBackendMock
) -> None:
    rag = FakeRagService(
        [
            _hit("rule:1", score=0.9),
            _hit("rule:1", score=0.8),  # 重复 chunk 只保留分数高的那条
            _hit("rule:2", score=0.7),
        ]
    )
    executor = _executor(settings, backend, rag)
    result = await executor.execute(_call("search_knowledge", {"query": "罚金"}))
    chunks = result.data["chunks"]
    assert [chunk["chunk_id"] for chunk in chunks] == ["rule:1", "rule:2"]
    assert [chunk["citation_no"] for chunk in chunks] == [1, 2]


async def test_executor_search_knowledge_tolerates_malformed_hits(
    settings, backend: JavaBackendMock
) -> None:
    rag = FakeRagService([None, _hit("rule:1", score=0.9), "oops"], mode="vector")  # type: ignore[list-item]
    executor = _executor(settings, backend, rag)
    result = await executor.execute(_call("search_knowledge", {"query": "罚金"}))
    assert result.ok is True
    assert [chunk["chunk_id"] for chunk in result.data["chunks"]] == ["rule:1"]


async def test_executor_search_knowledge_tolerates_bad_scores(
    settings, backend: JavaBackendMock
) -> None:
    rag = FakeRagService(
        [_hit("a", score=None), _hit("b", score=float("nan")), _hit("c", score=0.3)]  # type: ignore[arg-type]
    )
    executor = _executor(settings, backend, rag)
    result = await executor.execute(_call("search_knowledge", {"query": "规则"}))
    assert result.ok is True
    chunks = result.data["chunks"]
    assert [chunk["citation_no"] for chunk in chunks] == [1, 2, 3]
    # NaN / None 分数按 0.0 处理，不会破坏排序或抛异常
    assert [chunk["score"] for chunk in chunks] == [0.3, 0.0, 0.0]


# ----------------------------------------------------------------------
# 2b. executor：弱相关阈值（向量检索总会返回 top-k，无关问题要能过滤成空）
# ----------------------------------------------------------------------
async def test_executor_search_knowledge_filters_weak_matches_in_vector_mode(
    settings, backend: JavaBackendMock
) -> None:
    rag = FakeRagService(
        [
            _hit("rule:1", score=0.90),
            _hit("book:1", source_type="book", score=0.50),  # 低于 vector 默认阈值 0.67
            _hit("faq:0", source_type="faq", score=0.75),
        ],
        mode="vector",
    )
    executor = _executor(settings, backend, rag)
    result = await executor.execute(_call("search_knowledge", {"query": "逾期罚金"}))
    assert result.ok is True
    assert [chunk["chunk_id"] for chunk in result.data["chunks"]] == ["rule:1", "faq:0"]
    assert [chunk["citation_no"] for chunk in result.data["chunks"]] == [1, 2]
    assert "检索到 2 段馆藏依据" in result.summary


async def test_executor_search_knowledge_all_weak_returns_empty(
    settings, backend: JavaBackendMock
) -> None:
    rag = FakeRagService([_hit("faq:9", source_type="faq", score=0.66)], mode="vector")
    executor = _executor(settings, backend, rag)
    result = await executor.execute(_call("search_knowledge", {"query": "图书馆的 WiFi 密码是多少"}))
    assert result.ok is True
    assert result.data == {"chunks": []}
    assert result.summary == EMPTY_KNOWLEDGE_SUMMARY


async def test_executor_search_knowledge_threshold_not_applied_in_bm25_mode(
    settings, backend: JavaBackendMock
) -> None:
    """bm25 分数量纲是 0~100，不能套用 vector 的 0.67 阈值（否则会全被过滤）。"""
    rag = FakeRagService([_hit("rule:1", score=0.05)], mode="bm25")
    executor = _executor(settings, backend, rag)
    result = await executor.execute(_call("search_knowledge", {"query": "罚金"}))
    assert result.ok is True
    assert len(result.data["chunks"]) == 1


async def test_executor_search_knowledge_threshold_env_override(
    settings, backend: JavaBackendMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    rag = FakeRagService([_hit("book:1", source_type="book", score=0.50)], mode="vector")
    executor = _executor(settings, backend, rag)

    monkeypatch.setenv("RAG_TOOL_MIN_SCORE", "0.4")  # 调低阈值 → 保留
    kept = await executor.execute(_call("search_knowledge", {"query": "三体"}))
    assert len(kept.data["chunks"]) == 1

    monkeypatch.setenv("RAG_TOOL_MIN_SCORE", "0")  # 0 = 关闭过滤
    all_kept = await executor.execute(_call("search_knowledge", {"query": "三体"}))
    assert len(all_kept.data["chunks"]) == 1


# ----------------------------------------------------------------------
# 3. executor：降级（空结果 / 异常 / 服务不可用）
# ----------------------------------------------------------------------
async def test_executor_search_knowledge_empty_result_degrades_to_empty_chunks(
    settings, backend: JavaBackendMock
) -> None:
    executor = _executor(settings, backend, FakeRagService(results=[]))
    result = await executor.execute(_call("search_knowledge", {"query": "图书馆的 WiFi 密码是多少"}))
    assert result.ok is True
    assert result.error_code is None
    assert result.data == {"chunks": []}
    assert result.summary == EMPTY_KNOWLEDGE_SUMMARY
    assert "没有检索到相关依据" in result.summary


async def test_executor_search_knowledge_rag_exception_does_not_raise(
    settings, backend: JavaBackendMock, caplog: pytest.LogCaptureFixture
) -> None:
    executor = _executor(settings, backend, FakeRagService(error=RuntimeError("embedding api down")))
    with caplog.at_level("WARNING", logger="ai-service.tools.executor"):
        result = await executor.execute(_call("search_knowledge", {"query": "逾期罚金"}))
    assert result.ok is True
    assert result.data == {"chunks": []}
    assert result.summary == RAG_UNAVAILABLE_SUMMARY
    assert "暂不可用" in result.summary
    assert [getattr(record, "code", None) for record in caplog.records] == ["RAG_SEARCH_TOOL_FAILED"]


async def test_executor_search_knowledge_service_unavailable_degrades(
    settings, backend: JavaBackendMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.rag.service as rag_service_module

    def _boom():  # noqa: ANN202 - 测试替身
        raise RuntimeError("rag service 初始化失败")

    monkeypatch.setattr(rag_service_module, "get_rag_service", _boom)
    executor = _executor(settings, backend, rag=None)  # 走懒加载路径
    result = await executor.execute(_call("search_knowledge", {"query": "逾期罚金"}))
    assert result.ok is True
    assert result.data == {"chunks": []}
    assert result.summary == RAG_UNAVAILABLE_SUMMARY


# ----------------------------------------------------------------------
# 4. executor：参数校验（模型可自我纠正 → ok:false + INVALID_ARGS）
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "args",
    [
        {"query": "借阅规则", "top_k": 0},
        {"query": "借阅规则", "top_k": -3},
        {"query": "借阅规则", "top_k": 11},
        {"query": "借阅规则", "top_k": "abc"},
        {"query": "借阅规则", "source_types": ["video"]},
        {"query": "借阅规则", "source_types": "rule"},
        {"query": ""},
        {"query": "   "},
        {},
    ],
)
async def test_executor_search_knowledge_invalid_args(
    settings, backend: JavaBackendMock, args: dict
) -> None:
    rag = FakeRagService(results=[])
    executor = _executor(settings, backend, rag)
    result = await executor.execute(_call("search_knowledge", args))
    assert result.ok is False
    assert result.error_code == CODE_TOOL_INVALID_ARGS
    assert "参数" in result.summary
    assert rag.calls == []  # 参数校验失败不调用检索层


async def test_unknown_tool_summary_lists_search_knowledge(settings, backend: JavaBackendMock) -> None:
    executor = _executor(settings, backend, FakeRagService())
    result = await executor.execute(_call("delete_all_books", {}))
    assert result.ok is False
    assert result.error_code == CODE_TOOL_NOT_FOUND
    assert "search_knowledge" in result.summary


async def test_search_knowledge_tool_message_payload_is_json_serializable(
    settings, backend: JavaBackendMock, rag: FakeRagService
) -> None:
    executor = _executor(settings, backend, rag)
    result = await executor.execute(_call("search_knowledge", {"query": "逾期罚金怎么算"}))
    payload = json.loads(json.dumps(result.to_tool_message_payload(), ensure_ascii=False))
    assert payload["ok"] is True
    assert payload["data"]["chunks"][0]["citation_no"] == 1
    assert payload["summary"] == result.summary


# ----------------------------------------------------------------------
# 5. 提示词：引用与拒答
# ----------------------------------------------------------------------
def test_prompt_requires_search_knowledge_first() -> None:
    prompt = build_system_prompt(None, None)
    assert "search_knowledge" in prompt
    assert "必须先调用 search_knowledge" in prompt
    assert "不得凭记忆编造规则数值" in prompt


def test_prompt_defines_citation_rules() -> None:
    prompt = build_system_prompt(None, None)
    assert "citation_no" in prompt
    assert "[1]" in prompt and "[2]" in prompt
    assert "不得编造编号" in prompt
    assert "不要加编号" in prompt  # 写操作/借阅记录结果不编号


def test_prompt_defines_refusal_when_no_chunks() -> None:
    prompt = build_system_prompt(None, None)
    assert "馆藏资料里没有找到依据，要不要我帮你在馆藏里检索相关图书？" in prompt
    assert "没有检索到相关依据" in prompt
    assert "禁止编造" in prompt


def test_prompt_keeps_existing_constraints() -> None:
    """新增引用规则不能破坏既有约束（身份/罚金口径/写操作确认）。"""
    prompt = build_system_prompt(
        {"user": {"username": "reader", "realName": "张小明", "role": "READER"}, "rule": {}},
        None,
    )
    assert "张小明" in prompt
    assert "确认卡片" in prompt
    assert "不要对在借图书报罚金金额" in prompt
    assert "只报逾期天数" in prompt
