"""LLM 网关：模型路由、超时控制、限流重试、降级链、token 统计。

契约要求（§5）：
- LLM_TIMEOUT  -> 自动降级到 fallback 链
- LLM_AUTH_ERROR -> 立即报错（提示配置 .env）
- LLM_RATE_LIMIT -> 退避重试 1 次后降级

约定：一旦已经向 SSE 吐出过 token，就不再降级重试，否则前端会看到重复/断裂的文本。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from app.config import Settings
from app.llm.base import BaseLLMProvider, LLMError, LLMErrorCode
from app.obs.logging import get_logger

logger = get_logger("llm.gateway")

TokenCallback = Callable[[str], None]


@dataclass
class LLMResult:
    """一次模型调用的产出（含降级后的实际 provider 与用量）。"""

    message: AIMessage
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


def _content_to_text(content: Any) -> str:
    """兼容 str 与多模态内容块两种返回格式。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _classify_exception(exc: BaseException, provider: str) -> LLMError:
    """把各方 SDK 异常统一映射为契约 §5 的错误码。"""
    try:  # openai 是 langchain-openai 的传递依赖，这里做防御式导入
        import openai

        if isinstance(exc, (openai.APITimeoutError, asyncio.TimeoutError, TimeoutError)):
            return LLMError(
                LLMErrorCode.LLM_TIMEOUT,
                f"{provider} 模型响应超时，已尝试切换备用模型。",
                provider=provider,
                retryable=True,
            )
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return LLMError(
                LLMErrorCode.LLM_AUTH_ERROR,
                f"{provider} 模型鉴权失败：请检查 .env 中的 API Key 是否正确、是否有该模型权限。",
                provider=provider,
            )
        if isinstance(exc, openai.RateLimitError):
            return LLMError(
                LLMErrorCode.LLM_RATE_LIMIT,
                f"{provider} 模型触发限流，请稍后重试。",
                provider=provider,
                retryable=True,
            )
        if isinstance(exc, openai.APIConnectionError):
            return LLMError(
                LLMErrorCode.LLM_UPSTREAM_ERROR,
                f"{provider} 模型服务连接失败：{exc}",
                provider=provider,
                retryable=True,
            )
        if isinstance(exc, openai.APIStatusError):
            status = getattr(exc, "status_code", None)
            code = LLMErrorCode.LLM_UPSTREAM_ERROR
            if status in (401, 403):
                code = LLMErrorCode.LLM_AUTH_ERROR
            elif status == 429:
                code = LLMErrorCode.LLM_RATE_LIMIT
            return LLMError(code, f"{provider} 模型服务返回异常（HTTP {status}）。", provider=provider)
    except ImportError:  # pragma: no cover - openai 一定存在
        pass

    return LLMError(
        LLMErrorCode.LLM_UPSTREAM_ERROR,
        f"{provider} 模型调用失败：{type(exc).__name__}: {exc}",
        provider=provider,
    )


@dataclass
class LLMGateway:
    """模型网关：对外只暴露 astream_chat 一个入口。"""

    settings: Settings
    # 允许测试注入自定义 provider（默认按 settings 构造）
    providers: dict[str, BaseLLMProvider] | None = None
    _provider_cache: dict[str, BaseLLMProvider] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.providers is None:
            from app.llm.openai_compat import build_default_providers

            self.providers = build_default_providers(self.settings)

    # ------------------------------------------------------------------
    @property
    def provider_chain(self) -> list[str]:
        return self.settings.provider_chain

    def describe(self) -> dict[str, Any]:
        """健康检查/日志用：当前主模型与降级链。"""
        return {
            "llmProvider": self.settings.llm_provider,
            "fallbackChain": self.settings.fallback_providers,
        }

    # ------------------------------------------------------------------
    async def astream_chat(
        self,
        messages: list[BaseMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        on_token: TokenCallback | None = None,
    ) -> LLMResult:
        """流式调用模型，失败时按降级链切换，返回聚合后的 AIMessage。"""
        errors: list[LLMError] = []
        chain = self.provider_chain

        for provider_name in chain:
            provider = self.providers.get(provider_name) if self.providers else None
            if provider is None:
                errors.append(
                    LLMError(
                        LLMErrorCode.LLM_AUTH_ERROR,
                        f"未知的模型提供方 '{provider_name}'，请检查 LLM_PROVIDER / LLM_FALLBACK_CHAIN。",
                        provider=provider_name,
                    )
                )
                continue

            try:
                model = provider.build_chat_model()
            except LLMError as exc:
                # 缺 Key 之类的配置错误：记录后尝试下一个 provider（若配置了降级链）
                logger.warning(
                    "模型配置不可用，尝试降级",
                    extra={"provider": provider_name, "code": exc.code},
                )
                errors.append(exc)
                continue
            except Exception as exc:  # pragma: no cover - 防御式兜底
                errors.append(_classify_exception(exc, provider_name))
                continue

            bound = self._bind_tools(model, tools)
            emitted: list[str] = []
            rate_limit_retried = False
            while True:
                try:
                    return await self._attempt(
                        provider=provider,
                        provider_name=provider_name,
                        bound_model=bound,
                        messages=messages,
                        on_token=on_token,
                        emitted=emitted,
                    )
                except LLMError as exc:
                    if emitted:
                        # 已经吐过 token，降级会导致前端文本重复，直接报错结束
                        logger.warning(
                            "模型输出部分内容后失败，不再降级",
                            extra={"provider": provider_name, "code": exc.code},
                        )
                        raise
                    # 契约 §5：限流退避重试 1 次，仍失败再降级
                    if exc.code == LLMErrorCode.LLM_RATE_LIMIT and not rate_limit_retried:
                        rate_limit_retried = True
                        logger.warning(
                            "模型限流，退避重试一次",
                            extra={"provider": provider_name, "code": exc.code},
                        )
                        await asyncio.sleep(0.2)
                        continue
                    errors.append(exc)
                    break
            continue

        if not errors:  # pragma: no cover - chain 至少包含主 provider
            raise LLMError(LLMErrorCode.LLM_AUTH_ERROR, "没有可用的模型提供方，请检查 .env 配置。")
        last = errors[-1]
        if len(errors) == 1:
            raise last
        tried = " -> ".join(chain)
        raise LLMError(
            last.code,
            f"模型调用失败（已尝试 {tried}）：{last.message}",
            provider=last.provider,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _bind_tools(
        model: BaseChatModel, tools: list[dict[str, Any]] | None
    ) -> BaseChatModel:
        """把工具 schema 绑定到模型；个别模型未实现 bind_tools 时降级为不绑定。"""
        if not tools:
            return model
        try:
            return model.bind_tools(tools)  # type: ignore[return-value]
        except NotImplementedError:  # pragma: no cover - 自定义模型可能不支持
            logger.warning("模型未实现 bind_tools，将以无工具模式调用", extra={"code": "NO_BIND_TOOLS"})
            return model

    # ------------------------------------------------------------------
    async def _attempt(
        self,
        *,
        provider: BaseLLMProvider,
        provider_name: str,
        bound_model: BaseChatModel,
        messages: list[BaseMessage],
        on_token: TokenCallback | None,
        emitted: list[str],
    ) -> LLMResult:
        """单次模型调用：外层 wait_for 保证整体超时，内部聚合 chunk。"""
        started = time.perf_counter()
        timeout = self.settings.llm_timeout_seconds
        aggregate: Any = None

        async def _consume() -> None:
            nonlocal aggregate
            async for chunk in bound_model.astream(messages):
                text = _content_to_text(chunk.content)
                if text:
                    emitted.append(text)
                    if on_token is not None:
                        on_token(text)
                aggregate = chunk if aggregate is None else aggregate + chunk

        try:
            await asyncio.wait_for(_consume(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise LLMError(
                LLMErrorCode.LLM_TIMEOUT,
                f"{provider_name} 模型响应超时（>{timeout:.0f}s）。",
                provider=provider_name,
                retryable=True,
            ) from exc
        except LLMError:
            raise
        except Exception as exc:
            raise _classify_exception(exc, provider_name) from exc

        if aggregate is None:
            raise LLMError(
                LLMErrorCode.LLM_UPSTREAM_ERROR,
                f"{provider_name} 模型没有返回任何内容。",
                provider=provider_name,
            )

        tool_calls = []
        for call in getattr(aggregate, "tool_calls", []) or []:
            tool_calls.append(
                {
                    "name": call.get("name"),
                    "args": call.get("args") or {},
                    "id": call.get("id") or "",
                    "type": "tool_call",
                }
            )
        usage = getattr(aggregate, "usage_metadata", None) or {}
        result = LLMResult(
            message=AIMessage(content=_content_to_text(aggregate.content), tool_calls=tool_calls),
            provider=provider_name,
            model=provider.model_name,
            prompt_tokens=int(usage.get("input_tokens", 0) or 0),
            completion_tokens=int(usage.get("output_tokens", 0) or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        logger.info(
            "模型调用完成",
            extra={
                "provider": result.provider,
                "durationMs": result.latency_ms,
                "code": "OK",
            },
        )
        return result
