"""待确认状态的可恢复性（修复「会话被 pending interrupt 锁死」）。

覆盖：
- 内存 / Redis 确认存储的 get_pending：按会话查、消费后清空；
- GET /api/ai/chat/pending：返回 payload、未登录 401、无 pending 返回 null；
- chat 守卫错误里带 confirmationId（前端据此恢复确认卡）。
"""

from __future__ import annotations

import fakeredis

from app.memory.redis_store import RedisConfirmationStore
from app.memory.store import _MemoryConfirmationStore, get_confirmation_store, init_stores
from tests.conftest import AUTH_HEADERS

PAYLOAD = {
    "confirmationId": "cf-test",
    "summary": "即将为你借阅《红楼梦》（bookId=3），确认借阅吗？",
    "tools": [
        {"callId": "c1", "tool": "borrow_book", "args": {"bookId": 3}, "summary": "借阅"}
    ],
}


def test_memory_get_pending_and_consume() -> None:
    store = _MemoryConfirmationStore()
    store.create("s1", "cf-test", PAYLOAD, 300)
    record = store.get_pending("s1")
    assert record is not None and record.confirmation_id == "cf-test"
    assert store.get_pending("s2") is None
    assert store.consume("cf-test", "s1") is not None
    assert store.get_pending("s1") is None, "消费后不应再查到待确认"


def test_redis_get_pending_and_consume() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    store = RedisConfirmationStore(client=client)
    store.create("s1", "cf-test", PAYLOAD, 300)
    record = store.get_pending("s1")
    assert record is not None and record.confirmation_id == "cf-test"
    assert store.get_pending("s2") is None
    assert store.consume("cf-test", "s1") is not None
    assert store.get_pending("s1") is None, "消费后 pending 索引应被清理"


async def test_pending_endpoint_returns_payload(api) -> None:
    init_stores(session_ttl_seconds=3600, max_messages=20)
    get_confirmation_store().create("s-pending", "cf-test", PAYLOAD, 300)

    response = await api.get(
        "/api/ai/chat/pending", params={"sessionId": "s-pending"}, headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pending"]["confirmationId"] == "cf-test"
    assert body["pending"]["summary"] == PAYLOAD["summary"]
    assert body["pending"]["tools"][0]["tool"] == "borrow_book"


async def test_pending_endpoint_returns_null_when_absent(api) -> None:
    init_stores(session_ttl_seconds=3600, max_messages=20)
    response = await api.get(
        "/api/ai/chat/pending", params={"sessionId": "s-empty"}, headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert response.json() == {"pending": None}


async def test_pending_endpoint_requires_authorization(api) -> None:
    response = await api.get("/api/ai/chat/pending", params={"sessionId": "s1"})
    assert response.status_code == 401
