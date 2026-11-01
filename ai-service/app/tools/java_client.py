"""Java 内部工具接口客户端（契约 §2）。

硬性要求：
1. 用户身份只来自浏览器带来的 Authorization，原样转发，绝不由 LLM 决定；
2. 额外带 X-Internal-Token，防止公网直接访问内部接口；
3. 所有外部调用都必须有超时（连接 / 读），不允许裸调用；
4. 业务失败（HTTP 200 但 code != 200）与网络失败要区分，前者回灌给模型自我纠正，
   后者按 TOOL_UPSTREAM_ERROR 处理；401 则升级为 UPSTREAM_UNAUTHENTICATED。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.obs.logging import get_logger

logger = get_logger("tools.java_client")

# 契约 §5 错误码
CODE_TOOL_UPSTREAM_ERROR = "TOOL_UPSTREAM_ERROR"
CODE_UPSTREAM_UNAUTHENTICATED = "UPSTREAM_UNAUTHENTICATED"


class JavaClientError(Exception):
    """调用 Java 内部接口失败的基类。"""

    code = CODE_TOOL_UPSTREAM_ERROR

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class UpstreamUnauthenticated(JavaClientError):
    """用户 token 失效（Java 返回 401）——直接透传给前端要求重新登录。"""

    code = CODE_UPSTREAM_UNAUTHENTICATED


class UpstreamForbidden(JavaClientError):
    """越权（Java 返回 403），作为工具失败回灌给模型。"""

    code = CODE_TOOL_UPSTREAM_ERROR


class UpstreamTimeout(JavaClientError):
    """调用 Java 超时。"""

    code = CODE_TOOL_UPSTREAM_ERROR


class UpstreamBusinessError(JavaClientError):
    """HTTP 200 但业务 code != 200（如库存不足、超过借阅上限）。"""

    code = CODE_TOOL_UPSTREAM_ERROR

    def __init__(self, message: str, *, business_code: int | None = None) -> None:
        super().__init__(message, status_code=200)
        self.business_code = business_code


class JavaClient:
    """内部工具接口的异步客户端；一个请求生命周期内复用一个 httpx.AsyncClient。"""

    def __init__(
        self,
        settings: Settings,
        *,
        authorization: str | None,
        trace_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._authorization = authorization
        self._trace_id = trace_id
        timeout = httpx.Timeout(
            settings.java_read_timeout_seconds,
            connect=settings.java_connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=settings.java_api_base.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers=self._base_headers(),
        )

    # ------------------------------------------------------------------
    def _base_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Trace-Id": self._trace_id,
        }
        if self._authorization:
            # 原样转发：包含 "Bearer " 前缀，Java 侧沿用现有 AuthInterceptor
            headers["Authorization"] = self._authorization
        if self._settings.ai_internal_token:
            headers["X-Internal-Token"] = self._settings.ai_internal_token
        return headers

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "JavaClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """发起请求并把 ApiResponse<T> 解包成 data。"""
        request_timeout = None
        if timeout is not None:
            request_timeout = httpx.Timeout(
                timeout,
                connect=self._settings.java_connect_timeout_seconds,
            )
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                timeout=request_timeout,
            )
        except httpx.TimeoutException as exc:
            logger.warning("调用 Java 接口超时", extra={"traceId": self._trace_id, "code": "TIMEOUT"})
            raise UpstreamTimeout(f"业务后端响应超时（{path}），请稍后重试。") from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "调用 Java 接口网络异常",
                extra={"traceId": self._trace_id, "code": "NETWORK_ERROR"},
            )
            raise UpstreamTimeout(f"无法连接业务后端（{path}）：{type(exc).__name__}。") from exc

        status = response.status_code
        if status == 401:
            raise UpstreamUnauthenticated("登录状态已失效，请重新登录后再试。", status_code=status)
        if status == 403:
            raise UpstreamForbidden("没有权限执行该操作。", status_code=status)
        if status == 404:
            raise UpstreamBusinessError("请求的资源不存在。", business_code=404)
        if status >= 500:
            raise UpstreamBusinessError(
                f"业务后端异常（HTTP {status}），请稍后重试。", business_code=status
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise UpstreamBusinessError("业务后端返回了非 JSON 内容。") from exc

        business_code = body.get("code", 200)
        if business_code != 200:
            raise UpstreamBusinessError(
                str(body.get("message") or "业务处理失败。"), business_code=business_code
            )
        return body.get("data")

    # ------------------------------------------------------------------
    # 契约 §2.1 / §2.2：服务端注入的会话上下文（非 LLM 工具）
    # ------------------------------------------------------------------
    async def get_context(self, *, timeout: float | None = None) -> dict[str, Any]:
        return await self._request("GET", "/api/ai/internal/context", timeout=timeout)

    async def get_rules_text(self, *, timeout: float | None = None) -> str:
        data = await self._request("GET", "/api/ai/internal/rules/text", timeout=timeout)
        return str((data or {}).get("text") or "")

    # ------------------------------------------------------------------
    # 契约 §3：6 个 LLM 工具
    # ------------------------------------------------------------------
    async def search_books(
        self,
        *,
        keyword: str | None = None,
        category: str | None = None,
        size: int = 5,
        current: int = 1,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"current": current, "size": min(max(int(size), 1), 20)}
        if keyword:
            params["keyword"] = keyword
        if category:
            params["category"] = category
        return await self._request("GET", "/api/ai/internal/books", params=params)

    async def get_book_detail(self, book_id: int) -> dict[str, Any]:
        return await self._request("GET", f"/api/ai/internal/books/{int(book_id)}")

    async def get_my_borrows(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/ai/internal/borrows/my")
        return data or []

    async def borrow_book(self, book_id: int) -> dict[str, Any]:
        return await self._request("POST", "/api/ai/internal/borrow", json_body={"bookId": int(book_id)})

    async def return_book(self, borrow_id: int) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/ai/internal/return", json_body={"borrowId": int(borrow_id)}
        )

    async def reserve_book(self, book_id: int) -> dict[str, Any]:
        return await self._request("POST", "/api/ai/internal/reserve", json_body={"bookId": int(book_id)})
