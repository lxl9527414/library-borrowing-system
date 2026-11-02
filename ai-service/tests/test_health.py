"""健康检查 / 基础 HTTP 行为。"""

from __future__ import annotations

import fakeredis

from app.memory.store import init_stores
from tests.conftest import AUTH_HEADERS


async def test_health_returns_provider_and_version(api) -> None:
    response = await api.get("/api/ai/health")
    assert response.status_code == 200
    body = response.json()
    # W2：health 新增 sessionStore（会话/确认状态后端）与 checkpointer（图检查点后端）。
    # 单元测试不跑 lifespan，因此两个后端都应是降级后的 memory。
    assert body == {
        "status": "ok",
        "llmProvider": "mock",
        "version": "w1",
        "sessionStore": "memory",
        "checkpointer": "memory",
    }


async def test_health_reports_redis_session_store(api) -> None:
    """会话存储切到 Redis 后，health 的 sessionStore 必须变成 redis（前端据此展示）。"""
    client = fakeredis.FakeRedis(decode_responses=True)
    backend = init_stores(3600, max_messages=20, redis_client=client)
    assert backend == "redis"

    body = (await api.get("/api/ai/health")).json()
    assert body["sessionStore"] == "redis"
    # 单元测试未启动 lifespan，检查点仍是内存实现
    assert body["checkpointer"] == "memory"


async def test_chat_without_authorization_returns_401(api) -> None:
    response = await api.post("/api/ai/chat", json={"message": "帮我找三体"})
    assert response.status_code == 401
    assert "Authorization" in response.json()["message"]


async def test_chat_with_empty_message_returns_422(api) -> None:
    response = await api.post("/api/ai/chat", json={"message": ""}, headers=AUTH_HEADERS)
    assert response.status_code == 422
    assert response.json()["code"] == 422


async def test_unknown_session_returns_empty_history(api) -> None:
    response = await api.get("/api/ai/sessions/s-not-exist")
    assert response.status_code == 200
    assert response.json() == {"sessionId": "s-not-exist", "messages": []}
