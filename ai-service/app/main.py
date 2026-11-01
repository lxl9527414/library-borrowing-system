"""FastAPI 入口：CORS、路由注册、异常兜底、启动日志。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.graph import init_graph, shutdown_graph
from app.api.chat import router as chat_router
from app.config import Settings, get_settings
from app.memory.store import close_stores, init_stores
from app.obs.logging import get_logger, setup_logging

logger = get_logger("main")


def create_app(settings: Settings | None = None) -> FastAPI:
    """构造 FastAPI 应用（工厂函数，便于测试注入配置）。"""
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    # 先初始化进程内存储：ASGI 单元测试不跑 lifespan，也必须能拿到 store 单例；
    # 真实服务启动时由 lifespan 重新按 Redis 可用性初始化（可用则替换为 Redis 实现）
    init_stores(settings.ai_session_ttl_seconds, max_messages=20)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # W2：会话/确认状态优先 Redis（不可用自动降级内存），并在启动日志里显式暴露后端
        store_backend = init_stores(
            settings.ai_session_ttl_seconds,
            max_messages=20,
            redis_url=settings.ai_redis_url,
            confirmation_ttl_seconds=settings.confirmation_ttl_seconds,
            key_prefix=settings.ai_redis_key_prefix,
            connect_timeout_seconds=settings.ai_redis_connect_timeout_seconds,
            socket_timeout_seconds=settings.ai_redis_socket_timeout_seconds,
        )
        # W2：LangGraph 检查点优先 AsyncRedisSaver（不可用降级 InMemorySaver，绝不阻断启动）
        checkpointer_backend = await init_graph(settings)
        logger.info(
            "AI 服务启动",
            extra={"code": "STARTUP"},
        )
        logger.info(
            f"会话存储后端={store_backend}，LangGraph 检查点后端={checkpointer_backend}",
            extra={"code": "STORE_BACKEND"},
        )
        logger.info(
            "当前模型配置",
            extra={"provider": settings.llm_provider, "code": "CONFIG"},
        )
        try:
            yield
        finally:
            # 优雅关闭：先停图（关 Redis 检查点连接池），再关会话存储连接
            await shutdown_graph()
            close_stores()
            logger.info("AI 服务退出", extra={"code": "SHUTDOWN"})

    app = FastAPI(
        title="云阁图书馆 AI 服务",
        description="W1：Agent 主链路 + SSE + 写操作确认（mock 模式可离线运行）",
        version=settings.service_version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 测试钩子：注入 httpx.MockTransport 后，Java 工具调用不再走真实网络
    app.state.java_transport = None
    app.state.settings = settings

    app.include_router(chat_router, prefix="/api/ai")

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": exc.detail},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors: list[dict[str, Any]] = exc.errors()
        first = errors[0] if errors else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "message": f"请求参数不合法：{location} {first.get('msg', '')}".strip(),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理的异常", extra={"code": "INTERNAL_ERROR"})
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": f"AI 服务内部错误（{type(exc).__name__}），请稍后重试。"},
        )

    return app


app = create_app()
