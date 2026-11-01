"""Java 内部接口客户端：请求头透传、错误分类、现网响应差异（宽松解析）。"""

from __future__ import annotations

import pytest

from app.tools.java_client import (
    UpstreamBusinessError,
    UpstreamTimeout,
    UpstreamUnauthenticated,
)
from tests.conftest import build_java_client
from tests.fakes import JavaBackendMock


async def test_authorization_and_internal_token_are_forwarded(settings, backend: JavaBackendMock) -> None:
    client = build_java_client(settings, backend)
    try:
        await client.get_context()
    finally:
        await client.aclose()

    request = backend.requests[0]
    assert request.headers["authorization"] == "Bearer test-token"  # 原样转发
    assert request.headers["x-internal-token"] == "test-internal-token"
    assert request.headers["x-trace-id"] == "t-test"


async def test_get_rules_text(settings, backend: JavaBackendMock) -> None:
    client = build_java_client(settings, backend)
    try:
        text = await client.get_rules_text()
    finally:
        await client.aclose()
    assert "借期 30 天" in text


async def test_business_error_is_raised_with_chinese_message(settings, backend: JavaBackendMock) -> None:
    client = build_java_client(settings, backend)
    try:
        # 三体当前可借 0 本
        with pytest.raises(UpstreamBusinessError) as exc_info:
            await client.borrow_book(1)
    finally:
        await client.aclose()
    assert exc_info.value.business_code == 400
    assert "库存" in exc_info.value.message


async def test_unauthorized_maps_to_upstream_unauthenticated(settings, backend: JavaBackendMock) -> None:
    backend.unauthorized = True
    client = build_java_client(settings, backend)
    try:
        with pytest.raises(UpstreamUnauthenticated) as exc_info:
            await client.get_context()
    finally:
        await client.aclose()
    assert exc_info.value.code == "UPSTREAM_UNAUTHENTICATED"
    assert "重新登录" in exc_info.value.message


async def test_http_500_maps_to_upstream_error(settings, backend: JavaBackendMock) -> None:
    backend.internal_server_error = True
    client = build_java_client(settings, backend)
    try:
        with pytest.raises(UpstreamBusinessError) as exc_info:
            await client.search_books(keyword="三体")
    finally:
        await client.aclose()
    assert "HTTP 500" in exc_info.value.message


async def test_connection_error_maps_to_timeout_error(settings, backend: JavaBackendMock) -> None:
    backend.connection_error = True
    client = build_java_client(settings, backend)
    try:
        with pytest.raises(UpstreamTimeout) as exc_info:
            await client.search_books(keyword="三体")
    finally:
        await client.aclose()
    assert "无法连接业务后端" in exc_info.value.message


async def test_read_timeout_maps_to_timeout_error(settings, backend: JavaBackendMock) -> None:
    backend.read_timeout = True
    client = build_java_client(settings, backend)
    try:
        with pytest.raises(UpstreamTimeout) as exc_info:
            await client.get_my_borrows()
    finally:
        await client.aclose()
    assert "超时" in exc_info.value.message


async def test_book_detail_tolerates_omitted_my_reservation(settings, backend: JavaBackendMock) -> None:
    """现网实测：myReservation 为 null 时字段被 Jackson 省略。"""
    backend.my_reservation = None
    client = build_java_client(settings, backend)
    try:
        data = await client.get_book_detail(1)
    finally:
        await client.aclose()
    assert "myReservation" not in data
    assert data["book"]["title"] == "三体"
    assert data["waitingCount"] == 2


async def test_book_detail_returns_my_reservation_when_present(settings, backend: JavaBackendMock) -> None:
    backend.my_reservation = {"id": 9, "status": "READY", "expireAt": "2026-11-03 12:00:00"}
    client = build_java_client(settings, backend)
    try:
        data = await client.get_book_detail(1)
    finally:
        await client.aclose()
    assert data["myReservation"]["status"] == "READY"


async def test_search_size_is_capped_to_20(settings, backend: JavaBackendMock) -> None:
    client = build_java_client(settings, backend)
    try:
        await client.search_books(keyword="三体", size=999)
    finally:
        await client.aclose()
    request = backend.requests[-1]
    assert request.url.params["size"] == "20"


async def test_null_data_is_tolerated(settings, backend: JavaBackendMock) -> None:
    """契约说明 data 可能为 null，客户端与上层都要能处理。"""
    backend.borrows = []
    client = build_java_client(settings, backend)
    try:
        data = await client.get_my_borrows()
    finally:
        await client.aclose()
    assert data == []
