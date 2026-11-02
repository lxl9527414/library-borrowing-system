"""Langfuse tracer 离线测试（不联网、不依赖 Langfuse 服务）

覆盖点：
1. 未配置/未启用 → Noop，调用不报错、不发请求
2. 启用 + MockTransport → 一次回合产出 trace-create / tool span / generation(usage) 三类事件
3. 上报失败（500）→ 不抛异常、自动熔断，后续不再打网络
4. 采样率 0 → 不缓冲、不上报
"""

from __future__ import annotations

import base64
import json

import httpx

from app.obs.langfuse_tracer import (
    LangfuseTracer,
    NoopTracer,
    TracerConfig,
    configure_tracer,
    get_tracer,
    reset_tracer,
)


def _cfg(**kwargs) -> TracerConfig:
    base = dict(enabled=True, host="http://langfuse.local", public_key="pk-test", secret_key="sk-test")
    base.update(kwargs)
    return TracerConfig(**base)


def _simulate_turn(tracer) -> None:
    tracer.on_agent_event("tool_call", {"callId": "c1", "name": "search_knowledge", "args": {"query": "罚金"}},
                          trace_id="t-1", session_id="s-1")
    tracer.on_agent_event("tool_result", {"callId": "c1", "name": "search_knowledge", "ok": True, "summary": "命中 3 段"},
                          trace_id="t-1", session_id="s-1")
    tracer.on_agent_event("done", {"latencyMs": 1234, "usage": {"promptTokens": 100, "completionTokens": 20}},
                          trace_id="t-1", session_id="s-1")


def test_noop_when_disabled() -> None:
    tracer = configure_tracer(TracerConfig(enabled=False))
    assert isinstance(tracer, NoopTracer)
    # 调用不应抛异常
    tracer.on_agent_event("tool_call", {"callId": "c1", "name": "x"}, trace_id="t", session_id="s")
    tracer.flush()


def test_configure_returns_langfuse_when_keys_present() -> None:
    tracer = configure_tracer(_cfg())
    assert isinstance(tracer, LangfuseTracer)
    configure_tracer(None)


def test_events_are_batched_and_reported() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"success": True})

    tracer = LangfuseTracer(_cfg(), transport=httpx.MockTransport(handler))
    _simulate_turn(tracer)

    assert len(captured) == 1, "一个回合应只在 done 时发一次 batch"
    request = captured[0]
    assert str(request.url).endswith("/api/public/ingestion")
    # Basic Auth: base64(public:secret)
    expected = "Basic " + base64.b64encode(b"pk-test:sk-test").decode()
    assert request.headers["authorization"] == expected

    body = json.loads(request.content)
    batch = body["batch"]
    types = [item["type"] for item in batch]
    assert "trace-create" in types
    assert "observation-create" in types

    tool_spans = [i for i in batch if i["type"] == "observation-create" and i["body"].get("type") == "SPAN"]
    assert len(tool_spans) == 1
    assert tool_spans[0]["body"]["name"] == "tool:search_knowledge"
    assert tool_spans[0]["body"]["output"]["ok"] is True
    assert "durationMs" in tool_spans[0]["body"]["metadata"]

    generations = [
        i for i in batch if i["type"] == "observation-create" and i["body"].get("type") == "GENERATION"
    ]
    assert len(generations) == 1
    assert generations[0]["body"]["usageDetails"] == {"input": 100, "output": 20, "total": 120}

    traces = [i for i in batch if i["type"] == "trace-create"]
    assert any(t["body"]["sessionId"] == "s-1" for t in traces)


def test_http_failure_is_swallowed_and_trips_breaker() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    tracer = LangfuseTracer(_cfg(), transport=httpx.MockTransport(handler))
    _simulate_turn(tracer)  # 第一次上报失败
    assert tracer._failed is True
    assert calls["n"] == 1
    # 熔断后第二回合不应再打网络
    _simulate_turn(tracer)
    assert calls["n"] == 1


def test_zero_sample_rate_sends_nothing() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200)

    tracer = LangfuseTracer(_cfg(sample_rate=0.0), transport=httpx.MockTransport(handler))
    _simulate_turn(tracer)
    assert calls["n"] == 0


def test_emit_hook_never_raises_on_bad_payload(monkeypatch) -> None:
    tracer = LangfuseTracer(_cfg(), transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    # 缺字段的事件不应抛异常
    tracer.on_agent_event("tool_result", None, trace_id="t-2", session_id="s-2")
    tracer.on_agent_event("unknown-event", {"x": 1}, trace_id="t-2", session_id="s-2")
    tracer.flush("t-2")


def test_get_tracer_defaults_to_noop_without_keys(monkeypatch) -> None:
    reset_tracer()
    tracer = get_tracer()
    assert isinstance(tracer, NoopTracer)
    reset_tracer()
