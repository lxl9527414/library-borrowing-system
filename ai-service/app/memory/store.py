"""会话与待确认状态存储（W1 内存实现 + W2 Redis 实现，可运行时降级）。

上层（api/chat.py）只依赖 `SessionStore` / `ConfirmationStore` 两个 Protocol：
- SessionStore 保存每个会话的最近消息（调试端点 /sessions/{id} 用），带 TTL；
- ConfirmationStore 保存“写操作待确认”记录（confirmationId -> 工具调用），TTL 5 分钟。

存储后端选择（`init_stores`）：
- 给出 `redis_url` / `redis_client` 且 Redis 可用 → `RedisSessionStore` / `RedisConfirmationStore`；
- Redis 不可用 → log.warn 并降级为进程内实现（服务必须能起来，本地/离线开发不受影响）。

后端状态通过 `get_store_backend()` 暴露给 `/api/ai/health`（`sessionStore: "redis" | "memory"`）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

from app.obs.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，避免内存实现反向依赖 redis 客户端
    import redis

logger = get_logger("memory.store")

StoreBackend = Literal["redis", "memory"]


class SessionStore(Protocol):
    """会话历史存储接口（内存 / Redis 两种实现）。"""

    def save_transcript(self, session_id: str, messages: list[dict[str, Any]]) -> None: ...

    def get_transcript(self, session_id: str) -> list[dict[str, Any]]: ...

    def touch(self, session_id: str) -> None: ...


class ConfirmationStore(Protocol):
    """写操作确认记录存储接口（TTL 通常 5 分钟）。"""

    def create(
        self, session_id: str, confirmation_id: str, payload: dict[str, Any], ttl_seconds: int
    ) -> None: ...

    def get(self, confirmation_id: str) -> "ConfirmationRecord | None": ...

    def get_pending(self, session_id: str) -> "ConfirmationRecord | None":
        """按会话查当前待确认记录（用于会话被锁死时的前端恢复）。"""
        ...

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
    """内存会话存储：单进程、带 TTL，适合本地开发与 Redis 不可用时的降级。"""

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

    def get_pending(self, session_id: str) -> ConfirmationRecord | None:
        self._purge_expired()
        for record in self._data.values():
            if record.session_id == session_id:
                return record
        return None

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


# ----------------------------------------------------------------------
# 进程级单例（main.py lifespan 里初始化；测试可自行构造独立实例）
# ----------------------------------------------------------------------
_session_store: SessionStore | None = None
_confirmation_store: ConfirmationStore | None = None
_redis_client: "redis.Redis | None" = None
_store_backend: StoreBackend = "memory"


def init_stores(
    session_ttl_seconds: int,
    max_messages: int = 20,
    *,
    redis_url: str | None = None,
    redis_client: "redis.Redis | None" = None,
    confirmation_ttl_seconds: int = 300,
    key_prefix: str = "ai",
    connect_timeout_seconds: float = 1.0,
    socket_timeout_seconds: float = 1.0,
) -> StoreBackend:
    """初始化进程级存储单例，返回实际生效的后端。

    - 传入 `redis_client`（测试/嵌入场景）或 `redis_url` 时先 ping 探测；失败自动降级内存；
    - 重复调用会先关闭旧连接，避免 lifespan 反复初始化造成连接泄漏。
    """
    global _session_store, _confirmation_store, _redis_client, _store_backend

    _close_redis_client()
    _session_store = None
    _confirmation_store = None

    if redis_client is not None or redis_url:
        try:
            from app.memory.redis_store import (
                RedisConfirmationStore,
                RedisSessionStore,
                create_redis_client,
            )

            if redis_client is None:
                assert redis_url is not None
                redis_client = create_redis_client(
                    redis_url,
                    connect_timeout_seconds=connect_timeout_seconds,
                    socket_timeout_seconds=socket_timeout_seconds,
                )
            redis_client.ping()
            _session_store = RedisSessionStore(
                ttl_seconds=session_ttl_seconds,
                max_messages=max_messages,
                client=redis_client,
                key_prefix=key_prefix,
            )
            _confirmation_store = RedisConfirmationStore(
                client=redis_client,
                key_prefix=key_prefix,
            )
            _redis_client = redis_client
            _store_backend = "redis"
            return _store_backend
        except Exception as exc:  # 连不上 / 缺依赖 / 权限 / 模块缺失都不允许阻断启动
            logger.warning(
                "Redis 会话存储不可用，降级为进程内存储（重启后会话历史会丢失）",
                extra={"code": type(exc).__name__},
            )
            _close_redis_client()
            if redis_client is not None:
                try:
                    redis_client.close()
                except Exception:  # pragma: no cover - 关闭失败不影响降级
                    pass
            redis_client = None

    _session_store = _MemorySessionStore(session_ttl_seconds, max_messages)
    _confirmation_store = _MemoryConfirmationStore()
    _store_backend = "memory"
    logger.debug("会话存储后端：memory", extra={"code": "STORE_BACKEND"})
    return _store_backend


def get_session_store() -> SessionStore:
    if _session_store is None:
        init_stores(session_ttl_seconds=3600, max_messages=20)
    assert _session_store is not None
    return _session_store


def get_confirmation_store() -> ConfirmationStore:
    if _confirmation_store is None:
        init_stores(session_ttl_seconds=3600, max_messages=20)
    assert _confirmation_store is not None
    return _confirmation_store


def get_store_backend() -> StoreBackend:
    """当前生效的会话存储后端（health 端点展示用）。"""
    return _store_backend


def get_redis_client() -> "redis.Redis | None":
    """当前进程持有的 Redis 客户端（未启用 Redis 时为 None）。"""
    return _redis_client


def _close_redis_client() -> None:
    global _redis_client
    client = _redis_client
    _redis_client = None
    if client is None:
        return
    try:
        client.close()
    except Exception:  # pragma: no cover - 关闭阶段的异常不优雅
        logger.debug("关闭 Redis 客户端时出现异常", exc_info=True)


def close_stores() -> None:
    """应用退出时释放连接并把单例清空（幂等）。"""
    global _session_store, _confirmation_store, _store_backend
    _close_redis_client()
    _session_store = None
    _confirmation_store = None
    _store_backend = "memory"


def reset_stores() -> None:
    """测试辅助：清空进程级存储（含关闭 Redis 连接）。"""
    close_stores()
