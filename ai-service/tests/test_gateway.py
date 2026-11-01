"""LLM 网关：mock 路由、缺 Key 中文报错、超时降级、限流重试、统计。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult

from app.config import Settings
from app.llm.base import BaseLLMProvider, LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.mock import MockLLMProvider


class _AsyncOnlyChatModel(BaseChatModel):
    """测试用基类：只实现异步流式，_generate 用占位实现满足抽象接口。"""

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[])


class _SlowChatModel(_AsyncOnlyChatModel):
    """永远很慢的模型：用于验证超时后降级。"""

    model_name: str = "slow"

    @property
    def _llm_type(self) -> str:
        return "slow"

    async def _astream(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        await asyncio.sleep(1.0)
        yield ChatGenerationChunk(message=AIMessageChunk(content="迟到的回答"))


class _PartialThenTimeoutChatModel(_AsyncOnlyChatModel):
    """先吐一段文本再超时：此时不允许降级，避免前端文本重复。"""

    model_name: str = "partial"

    @property
    def _llm_type(self) -> str:
        return "partial"

    async def _astream(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content="部分"))
        raise asyncio.TimeoutError()


class _RateLimitOnceChatModel(_AsyncOnlyChatModel):
    """第一次调用触发限流，第二次成功。"""

    model_name: str = "rate-limit"
    attempts: int = 0

    @property
    def _llm_type(self) -> str:
        return "rate-limit"

    async def _astream(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        self.attempts += 1
        if self.attempts == 1:
            request = httpx.Request("POST", "http://test/v1/chat")
            raise openai.RateLimitError(
                "rate limited", response=httpx.Response(429, request=request), body=None
            )
        yield ChatGenerationChunk(message=AIMessageChunk(content="限流后重试成功"))


class _StaticProvider(BaseLLMProvider):
    """返回固定模型实例的 provider。"""

    def __init__(self, name: str, model: BaseChatModel) -> None:
        self.name = name
        self.model_name = name
        self._model = model

    def build_chat_model(self) -> BaseChatModel:
        return self._model


async def test_mock_provider_streams_tokens_and_usage(settings: Settings) -> None:
    gateway = LLMGateway(settings)
    tokens: list[str] = []
    result = await gateway.astream_chat(
        [HumanMessage(content="你好")], on_token=tokens.append
    )
    assert result.provider == "mock"
    assert result.message.content
    assert "".join(tokens) == result.message.content
    assert result.prompt_tokens == 24
    assert result.completion_tokens == 12


async def test_missing_api_key_raises_auth_error_with_chinese_hint() -> None:
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="",
        llm_fallback_chain="",
        llm_timeout_seconds=5,
    )
    gateway = LLMGateway(settings)
    with pytest.raises(LLMError) as exc_info:
        await gateway.astream_chat([HumanMessage(content="你好")])
    error = exc_info.value
    assert error.code == LLMErrorCode.LLM_AUTH_ERROR
    assert "DEEPSEEK_API_KEY" in error.message
    assert "mock" in error.message


async def test_missing_primary_key_falls_back_to_mock() -> None:
    settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="",
        llm_fallback_chain="mock",
        llm_timeout_seconds=5,
    )
    gateway = LLMGateway(settings)
    result = await gateway.astream_chat([HumanMessage(content="帮我找三体")])
    assert result.provider == "mock"
    assert result.message.tool_calls[0]["name"] == "search_books"


async def test_timeout_falls_back_to_next_provider() -> None:
    settings = Settings(
        llm_provider="mock",
        llm_fallback_chain="ollama",
        llm_timeout_seconds=0.05,
    )
    gateway = LLMGateway(
        settings,
        providers={
            "mock": _StaticProvider("mock", _SlowChatModel()),
            "ollama": MockLLMProvider(),
        },
    )
    tokens: list[str] = []
    result = await gateway.astream_chat([HumanMessage(content="你好")], on_token=tokens.append)
    # 主模型超时（无 token 产出）后自动降级到 ollama，并由后者输出内容
    assert result.provider == "ollama"
    assert tokens and "".join(tokens) == result.message.content


async def test_partial_output_then_timeout_does_not_fallback() -> None:
    settings = Settings(
        llm_provider="mock",
        llm_fallback_chain="ollama",
        llm_timeout_seconds=5,
    )
    gateway = LLMGateway(
        settings,
        providers={
            "mock": _StaticProvider("mock", _PartialThenTimeoutChatModel()),
            "ollama": MockLLMProvider(),
        },
    )
    tokens: list[str] = []
    with pytest.raises(LLMError) as exc_info:
        await gateway.astream_chat([HumanMessage(content="你好")], on_token=tokens.append)
    assert exc_info.value.code == LLMErrorCode.LLM_TIMEOUT
    assert tokens == ["部分"]  # 已经吐出的 token 不再重放


async def test_rate_limit_retries_once_then_succeeds() -> None:
    settings = Settings(llm_provider="mock", llm_fallback_chain="", llm_timeout_seconds=5)
    model = _RateLimitOnceChatModel()
    gateway = LLMGateway(settings, providers={"mock": _StaticProvider("mock", model)})
    result = await gateway.astream_chat([HumanMessage(content="你好")])
    assert model.attempts == 2
    assert result.message.content == "限流后重试成功"


async def test_unknown_provider_name_raises_auth_error() -> None:
    settings = Settings(llm_provider="mock", llm_fallback_chain="", llm_timeout_seconds=5)
    gateway = LLMGateway(settings, providers={})
    with pytest.raises(LLMError) as exc_info:
        await gateway.astream_chat([HumanMessage(content="你好")])
    assert exc_info.value.code == LLMErrorCode.LLM_AUTH_ERROR
    assert "未知的模型提供方" in exc_info.value.message


async def test_provider_chain_exposes_primary_and_fallbacks() -> None:
    settings = Settings(
        llm_provider="deepseek",
        llm_fallback_chain="qwen, mock,deepseek",
        llm_timeout_seconds=5,
    )
    gateway = LLMGateway(settings)
    assert gateway.provider_chain == ["deepseek", "qwen", "mock"]
    assert gateway.describe()["llmProvider"] == "deepseek"


async def test_tool_binding_matches_provider_interface() -> None:
    """ChatOpenAI / mock 都必须支持 bind_tools，否则真实模型链路会断。"""
    settings = Settings(llm_provider="mock", llm_timeout_seconds=5)
    gateway = LLMGateway(settings)
    result = await gateway.astream_chat(
        [HumanMessage(content="帮我找三体")],
        tools=[{"type": "function", "function": {"name": "search_books", "parameters": {"type": "object"}}}],
    )
    assert isinstance(result.message, AIMessage)
    assert result.message.tool_calls[0]["name"] == "search_books"
