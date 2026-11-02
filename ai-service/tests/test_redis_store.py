"""Redis 版会话 / 确认状态存储（用 fakeredis 离线验证）。

覆盖 task-4 要求：
- 会话 JSON 序列化往返、只保留最近 N 条、key 前缀隔离、TTL 生效；
- 确认记录一次性消费、重复消费返回 None、会话不匹配不误删、TTL 过期；
- `init_stores` 在 Redis 可用时切到 Redis、不可用时降级内存且服务可用。
"""

from __future__ import annotations

import time

import fakeredis
import pytest

from app.memory.redis_store import (
    RedisConfirmationStore,
    RedisSessionStore,
    create_redis_client,
)
from app.memory.store import (
    _MemoryConfirmationStore,
    _MemorySessionStore,
    get_confirmation_store,
    get_session_store,
    get_store_backend,
    init_stores,
    reset_stores,
)


@pytest.fixture
def fake_client():
    """离线 Redis 替身（decode_responses 与真实客户端配置一致）。"""
    client = fakeredis.FakeRedis(decode_responses=True)
    yield client
    client.close()


@pytest.fixture(autouse=True)
def _clean_singletons():
    reset_stores()
    yield
    reset_stores()


# ----------------------------------------------------------------------
# 会话历史
# ----------------------------------------------------------------------
def test_session_roundtrip_uses_json_and_prefixed_key(fake_client) -> None:
    store = RedisSessionStore(ttl_seconds=3600, client=fake_client)
    messages = [{"role": "user", "content": "帮我找三体"}, {"role": "assistant", "content": "《三体》暂无库存"}]

    store.save_transcript("s-1", messages)

    assert store.get_transcript("s-1") == messages
    assert store.get_transcript("s-2") == []

    key = "ai:session:s-1"
    raw = fake_client.get(key)
    # JSON 序列化且中文可读，便于 redis-cli 排查
    assert '"role": "user"' in raw
    assert "帮我找三体" in raw
    ttl = fake_client.ttl(key)
    assert 0 < ttl <= 3600


def test_session_keeps_only_recent_messages(fake_client) -> None:
    store = RedisSessionStore(ttl_seconds=3600, max_messages=3, client=fake_client)
    messages = [{"role": "user", "content": str(index)} for index in range(10)]

    store.save_transcript("s-1", messages)

    assert [item["content"] for item in store.get_transcript("s-1")] == ["7", "8", "9"]


def test_session_expires_by_redis_ttl(fake_client) -> None:
    store = RedisSessionStore(ttl_seconds=3600, client=fake_client)
    store.save_transcript("s-1", [{"role": "user", "content": "hi"}])
    assert store.get_transcript("s-1")

    # 把 TTL 压缩到 1ms，等待 Redis 原生过期（不用 sleep 整秒）
    fake_client.pexpire("ai:session:s-1", 1)
    time.sleep(0.02)
    assert store.get_transcript("s-1") == []
    assert fake_client.exists("ai:session:s-1") == 0


def test_session_ttl_zero_is_immediately_expired(fake_client) -> None:
    store = RedisSessionStore(ttl_seconds=0, client=fake_client)
    store.save_transcript("s-1", [{"role": "user", "content": "hi"}])
    assert store.get_transcript("s-1") == []


def test_session_touch_refreshes_ttl(fake_client) -> None:
    store = RedisSessionStore(ttl_seconds=3600, client=fake_client)
    store.save_transcript("s-1", [{"role": "user", "content": "hi"}])
    fake_client.expire("ai:session:s-1", 5)

    store.touch("s-1")
    assert fake_client.ttl("ai:session:s-1") > 5
    # 不存在的会话 touch 不应创建 key
    store.touch("s-missing")
    assert fake_client.exists("ai:session:s-missing") == 0


# ----------------------------------------------------------------------
# 确认记录
# ----------------------------------------------------------------------
def test_confirmation_is_one_time_consumable_in_redis(fake_client) -> None:
    store = RedisConfirmationStore(client=fake_client)
    payload = {"confirmationId": "cf-1", "tool": "reserve_book", "args": {"bookId": 1}}
    store.create("s-1", "cf-1", payload, ttl_seconds=300)

    key = "ai:confirmation:cf-1"
    assert 0 < fake_client.ttl(key) <= 300
    assert store.get("cf-1") is not None

    record = store.consume("cf-1", "s-1")
    assert record is not None
    assert record.session_id == "s-1"
    assert record.payload == payload
    # 已消费：key 被删除，重复消费（例如用户重复点击）必须返回 None
    assert fake_client.exists(key) == 0
    assert store.consume("cf-1", "s-1") is None


def test_confirmation_session_mismatch_is_rejected_without_deleting(fake_client) -> None:
    store = RedisConfirmationStore(client=fake_client)
    store.create("s-1", "cf-1", {"confirmationId": "cf-1"}, ttl_seconds=300)

    assert store.consume("cf-1", "s-2") is None
    # 会话不匹配不能把正确会话的待确认记录删掉
    assert store.get("cf-1") is not None
    assert store.consume("cf-1", "s-1") is not None


def test_confirmation_expires_by_redis_ttl(fake_client) -> None:
    store = RedisConfirmationStore(client=fake_client)
    store.create("s-1", "cf-1", {"confirmationId": "cf-1"}, ttl_seconds=300)
    fake_client.pexpire("ai:confirmation:cf-1", 1)
    time.sleep(0.02)

    assert store.get("cf-1") is None
    assert store.consume("cf-1", "s-1") is None


def test_confirmation_ttl_zero_is_immediately_expired(fake_client) -> None:
    store = RedisConfirmationStore(client=fake_client)
    store.create("s-1", "cf-1", {"confirmationId": "cf-1"}, ttl_seconds=0)
    assert store.get("cf-1") is None


def test_confirmation_corrupted_json_is_treated_as_missing(fake_client) -> None:
    store = RedisConfirmationStore(client=fake_client)
    fake_client.set("ai:confirmation:cf-bad", "not-json", ex=300)
    assert store.get("cf-bad") is None
    assert store.consume("cf-bad", "s-1") is None


# ----------------------------------------------------------------------
# 工厂 / 后端切换 / 降级
# ----------------------------------------------------------------------
def test_create_redis_client_targets_configured_db() -> None:
    client = create_redis_client("redis://127.0.0.1:6399/1", connect_timeout_seconds=0.2)
    # 只验证参数解析（未真正连接），避免测试依赖本机 Redis
    assert client.connection_pool.connection_kwargs["db"] == 1
    client.close()


def test_init_stores_switches_to_redis_with_injected_client(fake_client) -> None:
    backend = init_stores(3600, max_messages=20, redis_client=fake_client, confirmation_ttl_seconds=300)

    assert backend == "redis"
    assert get_store_backend() == "redis"
    assert isinstance(get_session_store(), RedisSessionStore)
    assert isinstance(get_confirmation_store(), RedisConfirmationStore)

    get_session_store().save_transcript("s-1", [{"role": "user", "content": "hi"}])
    get_confirmation_store().create("s-1", "cf-1", {"confirmationId": "cf-1"}, ttl_seconds=300)
    assert get_session_store().get_transcript("s-1") == [{"role": "user", "content": "hi"}]
    assert get_confirmation_store().consume("cf-1", "s-1") is not None


def test_init_stores_falls_back_to_memory_when_redis_unreachable() -> None:
    """连不上 Redis 不能抛异常，必须降级内存保证服务可启动。"""
    backend = init_stores(
        3600,
        max_messages=20,
        redis_url="redis://127.0.0.1:6399/1",
        connect_timeout_seconds=0.2,
        socket_timeout_seconds=0.2,
    )

    assert backend == "memory"
    assert get_store_backend() == "memory"
    assert isinstance(get_session_store(), _MemorySessionStore)
    assert isinstance(get_confirmation_store(), _MemoryConfirmationStore)

    # 降级后基本功能仍然可用
    get_session_store().save_transcript("s-1", [{"role": "user", "content": "hi"}])
    assert get_session_store().get_transcript("s-1")
