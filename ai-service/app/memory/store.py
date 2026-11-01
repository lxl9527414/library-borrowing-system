"""会话与待确认状态存储（W1：进程内内存实现，预留 Redis 实现）。

W1 只求单实例可用：
- SessionStore 保存每个会话的最近消息（调试端点 /sessions/{id} 用），带 TTL；
- ConfirmationStore 保存“写操作待确认”记录（confirmationId -> 工具调用），TTL 5 分钟。

W2 接入 Redis 时：
- 把 `_MemorySessionStore` / `_MemoryConfirmationStore` 换成对应 Redis 实现即可，
  上层只依赖 `SessionStore` / `ConfirmationStore` 两个抽象；
- 注意多 worker 部署时 InMemorySaver 也必须一起换成持久化 checkpointer。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.obs.logging import get_logger

logger = get_logger("memory.store")


class SessionStore(Protocol):
    """会话历史存储接口（W2 用 Redis 实现同一接口）。"""

    def save_transcript(self, session_id: str, messages: list[dict[str, Any]]) -> None: ...

    def get_transcript(self, session_id: str) -> list[dict[str, Any]]: ...

    def touch(self, session_id: str) -> None: ...


class ConfirmationStore(Protocol):
    """写操作确认记录存储接口（TTL 通常 5 分钟）。"""

    def create(
        self, session_id: str, confirmation_id: str, payload: dict[str, Any], ttl_seconds: int
    ) -> None: ...

    def get(self, confirmation_id: str) -> "ConfirmationRecord | None": ...

    def consume(self, confirmation_id: str, session_id: str) -> "ConfirmationRecord | None": ...


@dataclass
class ConfirmationRecord:
    confirmation_id: str
    session_id: str
    payload: dict[str, Any]
    expires_at: float
    created_at: float = field(default_factory=time.monotonic)

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) > self.expires_at


class _MemorySessionStore:
    """内存会话存储：单进程、带 TTL，适合 W1 演示与本地开发。"""

    def __init__(self, ttl_seconds: int, max_messages: int = 20) -> None:
        self._ttl = ttl_seconds
        self._max_messages = max_messages
        self._data: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [key for key, (expire_at, _) in self._data.items() if expire_at <= now]
        for key in expired:
            self._data.pop(key, None)

    def save_transcript(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        self._purge_expired()
        self._data[session_id] = (time.monotonic() + self._ttl, messages[-self._max_messages :])

    def get_transcript(self, session_id: str) -> list[dict[str, Any]]:
        self._purge_expired()
        entry = self._data.get(session_id)
        return list(entry[1]) if entry else []

    def touch(self, session_id: str) -> None:
        self._purge_expired()
        entry = self._data.get(session_id)
        if entry:
            self._data[session_id] = (time.monotonic() + self._ttl, entry[1])


class _MemoryConfirmationStore:
    """内存确认状态存储：confirmationId -> 待执行的写工具调用。"""

    def __init__(self) -> None:
        self._data: dict[str, ConfirmationRecord] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [key for key, record in self._data.items() if record.is_expired(now)]
        for key in expired:
            self._data.pop(key, None)

    def create(
        self, session_id: str, confirmation_id: str, payload: dict[str, Any], ttl_seconds: int
    ) -> None:
        self._purge_expired()
        self._data[confirmation_id] = ConfirmationRecord(
            confirmation_id=confirmation_id,
            session_id=session_id,
            payload=payload,
            expires_at=time.monotonic() + ttl_seconds,
        )

    def get(self, confirmation_id: str) -> ConfirmationRecord | None:
        self._purge_expired()
        return self._data.get(confirmation_id)

    def consume(self, confirmation_id: str, session_id: str) -> ConfirmationRecord | None:
        """取出并删除；sessionId 不匹配或已过期都视为无效。"""
        self._purge_expired()
        record = self._data.get(confirmation_id)
        if record is None:
            return None
        if record.session_id != session_id:
            logger.warning(
                "确认 ID 与会话不匹配，拒绝恢复",
                extra={"sessionId": session_id, "confirmationId": confirmation_id},
            )
            return None
        self._data.pop(confirmation_id, None)
        return record


class RedisSessionStore:  # pragma: no cover - W2 占位，不参与 W1 测试
    """W2 预留：基于 Redis 的会话存储。

    实现建议：
    - key = f"ai:session:{session_id}"，value = JSON 列表，EXPIRE = AI_SESSION_TTL_SECONDS；
    - 与 Spring 侧复用同一个 Redis（不同 db），避免再引入一套基础设施。
    """

    def __init__(self, redis_url: str, ttl_seconds: int) -> None:  # noqa: ARG002 - 占位
        raise NotImplementedError("W2 实现：会话持久化迁移到 Redis（AI_REDIS_URL）")


class RedisConfirmationStore:  # pragma: no cover - W2 占位，不参与 W1 测试
    """W2 预留：基于 Redis 的确认状态存储（key 带 TTL 自动过期）。"""

    def __init__(self, redis_url: str) -> None:  # noqa: ARG002 - 占位
        raise NotImplementedError("W2 实现：确认状态迁移到 Redis（AI_REDIS_URL）")


# ----------------------------------------------------------------------
# 进程级单例（main.py 启动时初始化；测试可自行构造独立实例）
# ----------------------------------------------------------------------
_session_store: _MemorySessionStore | None = None
_confirmation_store: _MemoryConfirmationStore | None = None


def init_stores(session_ttl_seconds: int, max_messages: int = 20) -> None:
    global _session_store, _confirmation_store
    _session_store = _MemorySessionStore(session_ttl_seconds, max_messages)
    _confirmation_store = _MemoryConfirmationStore()


def get_session_store() -> _MemorySessionStore:
    if _session_store is None:
        init_stores(session_ttl_seconds=3600, max_messages=20)
    assert _session_store is not None
    return _session_store


def get_confirmation_store() -> _MemoryConfirmationStore:
    if _confirmation_store is None:
        init_stores(session_ttl_seconds=3600, max_messages=20)
    assert _confirmation_store is not None
    return _confirmation_store


def reset_stores() -> None:
    """测试辅助：清空进程级存储。"""
    global _session_store, _confirmation_store
    _session_store = None
    _confirmation_store = None
