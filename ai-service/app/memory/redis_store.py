"""Redis 版会话 / 确认状态存储（W2）。

设计目标：
- 与 `app/memory/store.py` 的内存实现**方法签名完全一致**，上层（api/chat.py）零改动；
- 服务重启 / 多实例共享同一份会话历史与待确认记录；
- 所有 key 带统一前缀（默认 `ai:`），并与 LangGraph 检查点前缀（`ai:checkpoint*`）错开，
  避免污染 Spring 侧或同一 Redis 里的其它业务数据；
- value 一律 JSON 序列化（`ensure_ascii=False`，中文可读），便于用 redis-cli 排查；
- 过期完全交给 Redis 原生 TTL（会话 3600s / 确认 300s），无需进程内清理。

注意：这里用**同步** redis 客户端。会话/确认读写都发生在本地 Redis 上（亚毫秒级），
且现有 store 接口是同步 Protocol，保持签名一致优先级更高；若将来 Redis 跨机房部署，
应在 api 层改成 `run_in_threadpool` 或切换 `redis.asyncio`。
"""

from __future__ import annotations

import json
import time
from typing import Any

import redis

from app.memory.store import ConfirmationRecord
from app.obs.logging import get_logger

logger = get_logger("memory.redis_store")

DEFAULT_KEY_PREFIX = "ai"
SESSION_KEY_NAMESPACE = "session"
CONFIRMATION_KEY_NAMESPACE = "confirmation"


def create_redis_client(
    redis_url: str,
    *,
    connect_timeout_seconds: float = 1.0,
    socket_timeout_seconds: float = 1.0,
) -> redis.Redis:
    """按 URL 建同步 Redis 客户端（惰性连接：第一次命令才真正建连）。"""
    return redis.Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=connect_timeout_seconds,
        socket_timeout=socket_timeout_seconds,
    )


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(raw: str | bytes | None) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


class RedisSessionStore:
    """Redis 会话存储：key = `ai:session:{sessionId}`，value = JSON 消息列表。"""

    def __init__(
        self,
        redis_url: str | None = None,
        ttl_seconds: int = 3600,
        max_messages: int = 20,
        *,
        client: redis.Redis | None = None,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        connect_timeout_seconds: float = 1.0,
        socket_timeout_seconds: float = 1.0,
    ) -> None:
        if client is None:
            if not redis_url:
                raise ValueError("RedisSessionStore 需要 redis_url 或 client 二者之一")
            client = create_redis_client(
                redis_url,
                connect_timeout_seconds=connect_timeout_seconds,
                socket_timeout_seconds=socket_timeout_seconds,
            )
        self._client = client
        self._ttl = int(ttl_seconds)
        self._max_messages = int(max_messages)
        self._prefix = key_prefix

    # ---- 内部 ----
    def _key(self, session_id: str) -> str:
        return f"{self._prefix}:{SESSION_KEY_NAMESPACE}:{session_id}"

    @property
    def client(self) -> redis.Redis:
        """暴露底层客户端（生命周期管理 / 测试断言用）。"""
        return self._client

    # ---- SessionStore Protocol ----
    def save_transcript(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        """整份覆盖写，并刷新 TTL（SET key value EX ttl 是原子的）。"""
        if self._ttl <= 0:  # 语义等同内存版 ttl=0：立即过期，不落库
            self._safe_delete(session_id)
            return
        payload = list(messages)[-self._max_messages :]
        try:
            self._client.set(self._key(session_id), _dumps(payload), ex=self._ttl)
        except redis.RedisError as exc:  # 持久化失败不能打断主链路
            logger.warning(
                "写入 Redis 会话历史失败",
                extra={"sessionId": session_id, "code": type(exc).__name__},
            )

    def get_transcript(self, session_id: str) -> list[dict[str, Any]]:
        try:
            raw = self._client.get(self._key(session_id))
        except redis.RedisError as exc:
            logger.warning(
                "读取 Redis 会话历史失败",
                extra={"sessionId": session_id, "code": type(exc).__name__},
            )
            return []
        if raw is None:
            return []
        try:
            data = _loads(raw)
        except ValueError:
            logger.warning("会话历史 JSON 解析失败，按空历史处理", extra={"sessionId": session_id})
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def touch(self, session_id: str) -> None:
        """只续期不重写（例如只读访问也延长会话寿命）。"""
        if self._ttl <= 0:
            return
        try:
            self._client.expire(self._key(session_id), self._ttl)
        except redis.RedisError as exc:
            logger.warning(
                "刷新 Redis 会话 TTL 失败",
                extra={"sessionId": session_id, "code": type(exc).__name__},
            )

    def _safe_delete(self, session_id: str) -> None:
        try:
            self._client.delete(self._key(session_id))
        except redis.RedisError:  # pragma: no cover - 防御式兜底
            pass

    def close(self) -> None:
        """关闭连接池（应用退出时调用；失败不影响退出流程）。"""
        try:
            self._client.close()
        except Exception:  # pragma: no cover - 关闭阶段的异常不优雅
            logger.debug("关闭 Redis 会话存储客户端时出现异常", exc_info=True)


class RedisConfirmationStore:
    """Redis 确认记录存储：key = `ai:confirmation:{confirmationId}`。

    一次性消费语义与内存版一致：
    - `consume()` 校验 sessionId 后删除记录，重复消费返回 None；
    - sessionId 不匹配时不删除记录（避免误伤正确会话的待确认操作）；
    - 过期由 Redis TTL 保证（默认 300s），记录里另外存 `expiresAt` 便于排查。
    """

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        client: redis.Redis | None = None,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        connect_timeout_seconds: float = 1.0,
        socket_timeout_seconds: float = 1.0,
    ) -> None:
        if client is None:
            if not redis_url:
                raise ValueError("RedisConfirmationStore 需要 redis_url 或 client 二者之一")
            client = create_redis_client(
                redis_url,
                connect_timeout_seconds=connect_timeout_seconds,
                socket_timeout_seconds=socket_timeout_seconds,
            )
        self._client = client
        self._prefix = key_prefix

    def _key(self, confirmation_id: str) -> str:
        return f"{self._prefix}:{CONFIRMATION_KEY_NAMESPACE}:{confirmation_id}"

    @property
    def client(self) -> redis.Redis:
        return self._client

    # ---- ConfirmationStore Protocol ----
    def create(
        self, session_id: str, confirmation_id: str, payload: dict[str, Any], ttl_seconds: int
    ) -> None:
        if ttl_seconds <= 0:  # 语义等同内存版 ttl=0：立即可视为失效
            return
        now = time.time()
        record = {
            "confirmationId": confirmation_id,
            "sessionId": session_id,
            "payload": payload,
            "createdAt": now,
            "expiresAt": now + ttl_seconds,
        }
        try:
            self._client.set(
                self._key(confirmation_id), _dumps(record), ex=int(ttl_seconds)
            )
        except redis.RedisError as exc:
            logger.warning(
                "写入 Redis 确认状态失败",
                extra={
                    "sessionId": session_id,
                    "confirmationId": confirmation_id,
                    "code": type(exc).__name__,
                },
            )

    def get(self, confirmation_id: str) -> ConfirmationRecord | None:
        try:
            raw = self._client.get(self._key(confirmation_id))
        except redis.RedisError as exc:
            logger.warning(
                "读取 Redis 确认状态失败",
                extra={"confirmationId": confirmation_id, "code": type(exc).__name__},
            )
            return None
        return self._decode(raw)

    def consume(self, confirmation_id: str, session_id: str) -> ConfirmationRecord | None:
        """取出并删除；sessionId 不匹配视为无效且**不删除**记录。

        先 GET 校验会话、再 GETDEL 原子删除：并发重复点击时只有一个请求能拿到值，
        其余请求 GETDEL 返回 None，从而实现一次性消费。
        """
        key = self._key(confirmation_id)
        try:
            raw = self._client.get(key)
        except redis.RedisError as exc:
            logger.warning(
                "读取 Redis 确认状态失败",
                extra={"confirmationId": confirmation_id, "code": type(exc).__name__},
            )
            return None
        record = self._decode(raw)
        if record is None:
            return None
        if record.session_id != session_id:
            logger.warning(
                "确认 ID 与会话不匹配，拒绝恢复",
                extra={"sessionId": session_id, "confirmationId": confirmation_id},
            )
            return None
        try:
            consumed_raw = self._client.getdel(key)
        except Exception as exc:  # pragma: no cover - 旧版 Redis 无 GETDEL 时兜底
            logger.warning(
                "原子消费确认状态失败，退回 DELETE",
                extra={"confirmationId": confirmation_id, "code": type(exc).__name__},
            )
            consumed_raw = raw
            try:
                self._client.delete(key)
            except redis.RedisError:  # pragma: no cover - 防御式兜底
                return None
        # 并发场景：别的请求已经消费掉，本次视为无效
        return self._decode(consumed_raw)

    @staticmethod
    def _decode(raw: str | bytes | None) -> ConfirmationRecord | None:
        if raw is None:
            return None
        try:
            data = _loads(raw)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        payload = data.get("payload")
        return ConfirmationRecord(
            confirmation_id=str(data.get("confirmationId") or ""),
            session_id=str(data.get("sessionId") or ""),
            payload=dict(payload) if isinstance(payload, dict) else {},
            # 注意：Redis 记录里是 wall-clock epoch 秒；TTL 由 Redis 负责，
            # 这里不要用 ConfirmationRecord.is_expired()（它按 monotonic 比较）。
            expires_at=float(data.get("expiresAt") or 0.0),
            created_at=float(data.get("createdAt") or 0.0),
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover
            logger.debug("关闭 Redis 确认存储客户端时出现异常", exc_info=True)
