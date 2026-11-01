"""checkpointer 工厂：Redis 优先 / 不可用自动降级（离线可跑）。

由于本机/CI 不保证有带 RedisJSON+RediSearch 的 Redis Stack，这里用两种方式验证：
- 用假 saver 替换 `AsyncRedisSaver`，验证“可用时选中 redis 且配置正确、图可正常执行”；
- 指向未监听端口（127.0.0.1:6399），验证真实工厂的降级路径不抛异常；
- 用 `create_app` + lifespan 复现验收场景 4：Redis 不可用时服务能起、health 显示 memory、
  mock 问答仍以 done 结束。
"""

from __future__ import annotations

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.checkpointer import CHECKPOINT_PREFIX, CHECKPOINT_WRITE_PREFIX, close_checkpointer, create_checkpointer
from app.agent.graph import get_checkpointer_backend, get_graph, init_graph, reset_graph, shutdown_graph
from app.config import Settings
from app.main import create_app
from tests.conftest import AUTH_HEADERS, collect_sse, run_agent
from tests.fakes import JavaBackendMock


class _StubAsyncRedis:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class StubAsyncRedisSaver(InMemorySaver):
    """冒充 AsyncRedisSaver：只记录工厂传参，并暴露 `_redis` 供关闭逻辑使用。"""

    instances: list["StubAsyncRedisSaver"] = []

    def __init__(self, **kwargs) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.setup_called = False
        self._redis = _StubAsyncRedis()
        type(self).instances.append(self)

    async def asetup(self) -> None:
        self.setup_called = True


class FailingAsyncRedisSaver(StubAsyncRedisSaver):
    """模拟普通 redis-server 缺少 RedisJSON/RediSearch：asetup 阶段失败。"""

    async def asetup(self) -> None:
        raise RuntimeError("模拟：unknown command 'FT.CREATE'（缺少 RediSearch 模块）")


def _bad_redis_settings(**overrides) -> Settings:
    values = dict(
        llm_provider="mock",
        ai_redis_url="redis://127.0.0.1:6399/1",
        ai_redis_connect_timeout_seconds=0.2,
        ai_redis_socket_timeout_seconds=0.2,
        java_api_base="http://java.test",
        ai_internal_token="test-internal-token",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def _reset_saver_registry():
    StubAsyncRedisSaver.instances.clear()
    reset_graph()
    yield
    reset_graph()


# ----------------------------------------------------------------------
# 工厂：可用 / 不可用
# ----------------------------------------------------------------------
async def test_factory_prefers_redis_saver_when_available(settings) -> None:
    saver, backend = await create_checkpointer(settings, saver_cls=StubAsyncRedisSaver)

    assert backend == "redis"
    assert isinstance(saver, StubAsyncRedisSaver)
    assert saver.setup_called is True
    # key 前缀与会话存储统一使用 ai: 命名空间，避免污染 Spring 业务 key
    assert saver.kwargs["checkpoint_prefix"] == CHECKPOINT_PREFIX
    assert saver.kwargs["checkpoint_write_prefix"] == CHECKPOINT_WRITE_PREFIX
    assert saver.kwargs["ttl"]["default_ttl"] == settings.ai_checkpoint_ttl_minutes
    assert saver.kwargs["redis_url"] == settings.ai_redis_url
    assert saver.kwargs["connection_args"]["socket_connect_timeout"] == settings.ai_redis_connect_timeout_seconds

    await close_checkpointer(saver)
    assert saver._redis.closed is True


async def test_factory_falls_back_to_memory_when_setup_fails(settings) -> None:
    saver, backend = await create_checkpointer(settings, saver_cls=FailingAsyncRedisSaver)

    assert backend == "memory"
    assert isinstance(saver, InMemorySaver)
    # 失败时也要把已建立的 Redis 连接关掉，避免启动阶段泄露连接
    assert StubAsyncRedisSaver.instances[-1]._redis.closed is True


async def test_factory_falls_back_to_memory_when_redis_unreachable() -> None:
    saver, backend = await create_checkpointer(_bad_redis_settings())

    assert backend == "memory"
    assert isinstance(saver, InMemorySaver)


async def test_factory_uses_memory_when_redis_url_missing(settings) -> None:
    saver, backend = await create_checkpointer(settings.model_copy(update={"ai_redis_url": ""}))

    assert backend == "memory"
    assert isinstance(saver, InMemorySaver)


# ----------------------------------------------------------------------
# 图单例：checkpointer 进程级共享，图仍可正常执行
# ----------------------------------------------------------------------
async def test_init_graph_uses_redis_checkpointer_and_graph_still_runs(settings) -> None:
    backend = await init_graph(settings, saver_cls=StubAsyncRedisSaver)
    assert backend == "redis"
    assert get_checkpointer_backend() == "redis"
    assert isinstance(get_graph(), object)

    # 用假 saver（本质是 InMemorySaver）跑一遍真实图执行，确认迁移后 interrupt/事件流未受影响
    run = await run_agent(settings, message="帮我找三体", backend=JavaBackendMock())
    assert run.names[-1] == "done"
    assert run.first("tool_result")["ok"] is True

    stub = StubAsyncRedisSaver.instances[-1]
    await shutdown_graph()
    assert stub._redis.closed is True
    assert get_checkpointer_backend() == "memory"


# ----------------------------------------------------------------------
# 验收场景 4：Redis 不可用 → 服务照常启动，health=memory，mock 问答 done
# ----------------------------------------------------------------------
async def test_app_startup_degrades_gracefully_when_redis_unreachable(settings) -> None:
    app = create_app(settings.model_copy(update={"ai_redis_url": "redis://127.0.0.1:6399/1"}))
    backend = JavaBackendMock()
    app.state.java_transport = backend.transport()

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
            health = (await client.get("/api/ai/health")).json()
            assert health["status"] == "ok"
            assert health["sessionStore"] == "memory"
            assert health["checkpointer"] == "memory"

            async with client.stream(
                "POST", "/api/ai/chat", json={"message": "帮我找三体", "sessionId": "s-degraded"},
                headers=AUTH_HEADERS,
            ) as response:
                events = await collect_sse(response)
            assert events[-1][0] == "done"
            assert events[0][0] == "meta"

            session = (await client.get("/api/ai/sessions/s-degraded")).json()
            assert session["sessionId"] == "s-degraded"
            assert session["messages"]

            # 关键回归：lifespan 里编译出来的图（checkpointer 降级为 InMemorySaver）必须仍能
            # interrupt 暂停 / 恢复——验证 Python 3.10 contextvar 垫片在迁移后继续生效
            async with client.stream(
                "POST", "/api/ai/chat", json={"message": "帮我预约三体", "sessionId": "s-degraded-confirm"},
                headers=AUTH_HEADERS,
            ) as response:
                paused = await collect_sse(response)
            assert paused[-1][0] == "confirm_required"
            confirmation_id = next(data["confirmationId"] for name, data in paused if name == "confirm_required")

            async with client.stream(
                "POST", "/api/ai/chat/confirm",
                json={"sessionId": "s-degraded-confirm", "confirmationId": confirmation_id, "approved": True},
                headers=AUTH_HEADERS,
            ) as response:
                resumed = await collect_sse(response)
            assert resumed[-1][0] == "done"
            tool_call = next(data for name, data in resumed if name == "tool_call")
            assert tool_call["name"] == "reserve_book"
            assert backend.write_calls == [{"tool": "reserve_book", "body": {"bookId": 1}}]
    # lifespan 退出后连接与单例被清理
    assert get_checkpointer_backend() == "memory"
