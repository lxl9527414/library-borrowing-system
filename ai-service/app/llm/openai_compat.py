"""OpenAI 兼容协议 provider：DeepSeek / 通义千问（DashScope 兼容模式）/ 本地 Ollama。

三者都实现了 OpenAI 的 /chat/completions，因此统一用 langchain-openai 的 ChatOpenAI。
"""

from __future__ import annotations

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.config import Settings
from app.llm.base import (
    BaseLLMProvider,
    LLMError,
    LLMErrorCode,
    ProviderConfig,
    build_provider_config,
)


class OpenAICompatProvider(BaseLLMProvider):
    """把 ProviderConfig 翻译成 ChatOpenAI 实例。"""

    def __init__(self, config: ProviderConfig, settings: Settings) -> None:
        self.config = config
        self.settings = settings

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.config.name

    @property
    def model_name(self) -> str:
        return self.config.model

    def build_chat_model(self) -> BaseChatModel:
        # 缺 Key 时在“构造阶段”就抛出中文提示，避免把 401 拖到模型调用时才暴露
        if not self.config.api_key:
            raise self.config.missing_key_error
        timeout = httpx.Timeout(
            self.settings.llm_timeout_seconds,
            connect=min(10.0, self.settings.llm_timeout_seconds),
        )
        return ChatOpenAI(
            model=self.config.model,
            base_url=self.config.base_url,
            api_key=SecretStr(self.config.api_key),
            temperature=0.2,
            timeout=timeout,
            max_retries=self.settings.llm_max_retries,
            # 流式返回 token 用量，供 SSE done 事件里的 usage 字段使用
            stream_usage=True,
        )


def build_provider(settings: Settings, name: str) -> BaseLLMProvider:
    """按名称构造 provider（mock 走 MockLLMProvider，其余走 OpenAI 兼容层）。"""
    if name == "mock":
        from app.llm.mock import MockLLMProvider

        return MockLLMProvider()
    if name in {"deepseek", "qwen", "ollama"}:
        return OpenAICompatProvider(build_provider_config(settings, name), settings)
    raise LLMError(
        LLMErrorCode.LLM_AUTH_ERROR,
        f"未知的模型提供方 '{name}'，可选值：mock / deepseek / qwen / ollama。",
        provider=name,
    )


def build_default_providers(settings: Settings) -> dict[str, BaseLLMProvider]:
    """预构造全部 provider；这里不校验 Key，真正的校验发生在 build_chat_model()。"""
    providers: dict[str, BaseLLMProvider] = {}
    for name in ("mock", "deepseek", "qwen", "ollama"):
        try:
            providers[name] = build_provider(settings, name)
        except Exception:  # pragma: no cover - 未知 provider 名称永不为真
            continue
    return providers
