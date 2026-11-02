"""会话存储与确认状态存储：TTL、一次性消费、会话隔离。"""

from __future__ import annotations

from app.memory import store as store_module


def test_session_transcript_roundtrip() -> None:
    sessions = store_module._MemorySessionStore(ttl_seconds=60, max_messages=20)
    sessions.save_transcript("s-1", [{"role": "user", "content": "帮我找三体"}])
    assert sessions.get_transcript("s-1") == [{"role": "user", "content": "帮我找三体"}]
    assert sessions.get_transcript("s-2") == []


def test_session_transcript_keeps_only_recent_messages() -> None:
    sessions = store_module._MemorySessionStore(ttl_seconds=60, max_messages=3)
    messages = [{"role": "user", "content": str(index)} for index in range(10)]
    sessions.save_transcript("s-1", messages)
    transcript = sessions.get_transcript("s-1")
    assert [item["content"] for item in transcript] == ["7", "8", "9"]


def test_session_expires_by_ttl() -> None:
    sessions = store_module._MemorySessionStore(ttl_seconds=0, max_messages=20)
    sessions.save_transcript("s-1", [{"role": "user", "content": "hi"}])
    assert sessions.get_transcript("s-1") == []


def test_confirmation_is_one_time_consumable() -> None:
    confirmations = store_module._MemoryConfirmationStore()
    payload = {"confirmationId": "cf-1", "tool": "reserve_book"}
    confirmations.create("s-1", "cf-1", payload, ttl_seconds=300)

    assert confirmations.get("cf-1") is not None
    record = confirmations.consume("cf-1", "s-1")
    assert record is not None
    assert record.payload["tool"] == "reserve_book"
    # 第二次消费（例如用户重复点击）必须返回 None，避免重复执行写操作
    assert confirmations.consume("cf-1", "s-1") is None


def test_confirmation_session_mismatch_is_rejected() -> None:
    confirmations = store_module._MemoryConfirmationStore()
    confirmations.create("s-1", "cf-1", {"confirmationId": "cf-1"}, ttl_seconds=300)
    assert confirmations.consume("cf-1", "s-2") is None
    # 会话不匹配不能把记录删掉
    assert confirmations.get("cf-1") is not None


def test_confirmation_expires_by_ttl() -> None:
    confirmations = store_module._MemoryConfirmationStore()
    confirmations.create("s-1", "cf-1", {"confirmationId": "cf-1"}, ttl_seconds=0)
    assert confirmations.consume("cf-1", "s-1") is None


def test_process_singletons_and_reset() -> None:
    store_module.reset_stores()
    first = store_module.get_session_store()
    assert first is store_module.get_session_store()
    first.save_transcript("s-1", [{"role": "user", "content": "hi"}])
    assert store_module.get_session_store().get_transcript("s-1")

    store_module.reset_stores()
    assert store_module.get_session_store().get_transcript("s-1") == []
    assert store_module.get_confirmation_store() is not None
