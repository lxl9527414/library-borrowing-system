"""健康检查 / 基础 HTTP 行为。"""

from __future__ import annotations

from tests.conftest import AUTH_HEADERS


async def test_health_returns_provider_and_version(api) -> None:
    response = await api.get("/api/ai/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "llmProvider": "mock", "version": "w1"}


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
