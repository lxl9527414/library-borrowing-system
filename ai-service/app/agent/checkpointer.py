"""LangGraph checkpointer 工厂：优先 Redis，不可用自动降级内存（W2）。

约束与背景：
- `app/agent/graph.py` 的图必须是**进程级单例**（interrupt 恢复、多轮对话都依赖同一个
  checkpointer 实例），因此本工厂只在应用启动时调用一次，编译好的图由 graph 模块持有；
- `AsyncRedisSaver` 的 `asetup()` 会创建 RediSearch 索引并做能力探测，必须在**事件循环内**
  await；同时它要求 Redis 具备 RedisJSON + RediSearch 模块（Redis Stack / Redis 8 with
  search），普通 `redis-server` 会在这里失败——此时按契约降级 `InMemorySaver` + log.warn，
  服务照常启动（会话历史仍由 Redis SessionStore 持久化，只是图检查点重启后不保留）；
- 降级结果通过 `get_checkpointer_backend()` 暴露给 `/api/ai/health`。
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from langgraph.checkpoint.memory import InMemorySaver

from app.config import Settings
from app.obs.logging import get_logger

logger = get_logger("agent.checkpointer")

CheckpointerBackend = Literal["redis", "memory"]

# 与 memory.redis_store 的 `ai:` 前缀保持一致，但用独立命名空间，避免和会话 key 撞名
CHECKPOINT_PREFIX = "ai:checkpoint"
CHECKPOINT_WRITE_PREFIX = "ai:checkpoint_write"

# asetup 的兜底超时：连接超时由 redis 客户端参数控制，这里再兜一层，防止启动被拖死
SETUP_TIMEOUT_SECONDS = 5.0


async def _import_async_redis_saver() -> Any:
    """延迟导入：即使 langgraph-checkpoint-redis 损坏/缺失，服务也必须能启动。"""
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver  # noqa: PLC0415 - 延迟导入是降级需要

    return AsyncRedisSaver


async def create_checkpointer(
    settings: Settings,
    *,
    saver_cls: Any = None,
) -> tuple[Any, CheckpointerBackend]:
    """创建 checkpointer，返回 `(saver, backend)`。

    - Redis 可用 → `(AsyncRedisSaver, "redis")`；
    - 任何异常（连不上 / 无 RedisJSON/RediSearch / 依赖缺失）→ `(InMemorySaver, "memory")`，
      不向上抛异常，保证服务可启动。
    """
    redis_url = (settings.ai_redis_url or "").strip()
    if not redis_url:
        logger.info("未配置 AI_REDIS_URL，LangGraph 检查点使用进程内 InMemorySaver", extra={"code": "CHECKPOINTER_MEMORY"})
        return InMemorySaver(), "memory"

    saver: Any = None
    try:
        if saver_cls is None:
            saver_cls = await _import_async_redis_saver()
        ttl_config = (
            {"default_ttl": settings.ai_checkpoint_ttl_minutes, "refresh_on_read": True}
            if settings.ai_checkpoint_ttl_minutes > 0
            else None
        )
        saver = saver_cls(
            redis_url=redis_url,
            connection_args={
                "socket_connect_timeout": settings.ai_redis_connect_timeout_seconds,
                "socket_timeout": settings.ai_redis_socket_timeout_seconds,
            },
            ttl=ttl_config,
            checkpoint_prefix=CHECKPOINT_PREFIX,
            checkpoint_write_prefix=CHECKPOINT_WRITE_PREFIX,
        )
        await asyncio.wait_for(saver.asetup(), timeout=SETUP_TIMEOUT_SECONDS)
        logger.info(
            "LangGraph 检查点已启用 Redis（可重启、可多实例共享）",
            extra={"code": "CHECKPOINTER_REDIS"},
        )
        return saver, "redis"
    except Exception as exc:  # noqa: BLE001 - 降级是设计行为，任何异常都不能阻断启动
        logger.warning(
            "Redis checkpointer 不可用，降级为 InMemorySaver（图执行进度不跨重启保留）："
            f"{type(exc).__name__}: {exc}",
            extra={"code": "CHECKPOINTER_FALLBACK"},
        )
        if saver is not None:
            await close_checkpointer(saver)
        return InMemorySaver(), "memory"


async def close_checkpointer(saver: Any) -> None:
    """关闭 Redis checkpointer 的连接池（InMemorySaver 无需处理，幂等）。"""
    if saver is None:
        return
    try:
        # AsyncRedisSaver 持有异步客户端；InMemorySaver 没有 `_redis`，自然跳过
        redis_client = getattr(saver, "_redis", None)
        if redis_client is not None:
            await redis_client.aclose()
    except Exception:  # pragma: no cover - 关闭阶段异常不影响退出
        logger.debug("关闭 Redis checkpointer 时出现异常", exc_info=True)
