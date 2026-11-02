"""pytest fixtures：应用实例、MockTransport 后端、SSE 解析、Agent 运行辅助。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx
import pytest
from langchain_core.messages import HumanMessage

from app.agent.events import EventEmitter
from app.agent.graph import get_graph, reset_graph
from app.agent.runner import AgentStreamRunner
from app.agent.state import RunContext
from app.config import Settings
from app.llm.gateway import LLMGateway
from app.main import app
from app.memory.store import reset_stores
from app.tools.executor import ToolExecutor
from app.tools.java_client import JavaClient
from tests.fakes import JavaBackendMock

AUTH_HEADERS = {"Authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def clean_runtime_state() -> AsyncIterator[None]:
    """每个用例都从干净的检查点与会话存储开始，避免用例间互相污染。"""
    reset_graph()
    reset_stores()
    yield
    reset_graph()
    reset_stores()
    app.state.java_transport = None


@pytest.fixture
def settings() -> Settings:
    return Settings(
        llm_provider="mock",
        java_api_base="http://java.test",
        ai_internal_token="test-internal-token",
        llm_timeout_seconds=5.0,
        java_connect_timeout_seconds=1.0,
        java_read_timeout_seconds=2.0,
        java_context_timeout_seconds=1.0,
        ai_session_ttl_seconds=3600,
        ai_max_history_turns=8,
        confirmation_ttl_seconds=300,
        agent_max_steps=8,
        cors_origins="http://localhost:5173",
    )


@pytest.fixture
def backend() -> JavaBackendMock:
    return JavaBackendMock()


def build_java_client(
    settings: Settings, backend: JavaBackendMock, session_id: str = "s-test", trace_id: str = "t-test"
) -> JavaClient:
    return JavaClient(
        settings,
        authorization="Bearer test-token",
        trace_id=trace_id,
        transport=backend.transport(),
    )


def build_executor(settings: Settings, backend: JavaBackendMock) -> ToolExecutor:
    return ToolExecutor(
        build_java_client(settings, backend),
        trace_id="t-test",
        session_id="s-test",
    )


async def collect_sse(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    """把 SSE 响应解析成 (event, data) 列表。"""
    events: list[tuple[str, dict[str, Any]]] = []
    event_name: str | None = None
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
        elif line == "":
            if event_name:
                payload = json.loads(data_lines[-1]) if data_lines else {}
                events.append((event_name, payload))
            event_name, data_lines = None, []
    if event_name and data_lines:  # 兜底：最后没有空行
        events.append((event_name, json.loads(data_lines[-1])))
    return events


@dataclass
class SseResult:
    status: int
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    content_type: str = ""

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def first(self, name: str) -> dict[str, Any] | None:
        return next((data for event, data in self.events if event == name), None)


class ApiClient:
    """封装 ASGI 测试客户端 + SSE 请求。"""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._client.get(path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._client.post(path, **kwargs)

    async def sse_post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> SseResult:
        async with self._client.stream(
            "POST", path, json=payload, headers=headers if headers is not None else AUTH_HEADERS
        ) as response:
            status = response.status_code
            content_type = response.headers.get("content-type", "")
            events = await collect_sse(response)
        return SseResult(status=status, events=events, content_type=content_type)

    async def chat(self, message: str, session_id: str | None = None, **kwargs: Any) -> SseResult:
        payload: dict[str, Any] = {"message": message}
        if session_id:
            payload["sessionId"] = session_id
        return await self.sse_post("/api/ai/chat", payload, **kwargs)

    async def confirm(
        self,
        session_id: str,
        confirmation_id: str,
        approved: bool = True,
        **kwargs: Any,
    ) -> SseResult:
        return await self.sse_post(
            "/api/ai/chat/confirm",
            {"sessionId": session_id, "confirmationId": confirmation_id, "approved": approved},
            **kwargs,
        )


@pytest.fixture
async def api(backend: JavaBackendMock) -> AsyncIterator[ApiClient]:
    app.state.java_transport = backend.transport()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as client:
        yield ApiClient(client)
    app.state.java_transport = None


@dataclass
class AgentRun:
    events: list[tuple[str, dict[str, Any]]]
    run_ctx: RunContext

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def first(self, name: str) -> dict[str, Any] | None:
        return next((data for event, data in self.events if event == name), None)


async def run_agent(
    settings: Settings,
    *,
    message: str | None = None,
    session_id: str = "s-graph-test",
    system_prompt: str = "你是云阁图书馆智能助手。",
    gateway: Any = None,
    executor: ToolExecutor | None = None,
    backend: JavaBackendMock | None = None,
    max_steps: int = 8,
    graph_input: Any = None,
    on_confirm_required: Any = None,
) -> AgentRun:
    """直接驱动 LangGraph（不经过 HTTP），用于断言事件序列与错误处理。"""
    backend = backend or JavaBackendMock()
    if executor is None:
        executor = build_executor(settings, backend)
    run_ctx = RunContext(
        session_id=session_id,
        trace_id="t-test",
        emitter=EventEmitter(),
        gateway=gateway if gateway is not None else LLMGateway(settings),
        executor=executor,
        system_prompt=system_prompt,
        max_steps=max_steps,
        max_history_messages=settings.ai_max_history_turns * 4,
        on_confirm_required=on_confirm_required,
    )
    runner = AgentStreamRunner(run_ctx, get_graph())
    payload = graph_input if graph_input is not None else {"messages": [HumanMessage(content=message or "")]}
    events = [(event.event, event.data) async for event in runner.astream(payload)]
    return AgentRun(events=events, run_ctx=run_ctx)
