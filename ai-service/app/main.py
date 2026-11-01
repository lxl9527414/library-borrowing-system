"""FastAPI 入口：CORS、路由注册、异常兜底、启动日志。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.chat import router as chat_router
from app.config import Settings, get_settings
from app.memory.store import init_stores
from app.obs.logging import get_logger, setup_logging

logger = get_logger("main")


def create_app(settings: Settings | None = None) -> FastAPI:
    """构造 FastAPI 应用（工厂函数，便于测试注入配置）。"""
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    # 会话/确认状态存储必须在应用创建时初始化（uvicorn 与 ASGI 测试客户端行为一致）
    init_stores(settings.ai_session_ttl_seconds, max_messages=20)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "AI 服务启动",
            extra={"code": "STARTUP"},
        )
        logger.info(
            "当前模型配置",
            extra={"provider": settings.llm_provider, "code": "CONFIG"},
        )
        yield
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
