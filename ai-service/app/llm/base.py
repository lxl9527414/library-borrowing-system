"""LLM 抽象层：错误码、provider 描述、provider 基类。

设计目标：
1. 上层（gateway / graph）只依赖 `BaseChatModel`（LangChain 标准接口），
   不关心背后是 DeepSeek、通义还是本地 Ollama。
2. 所有 provider 的构造失败、超时、限流、鉴权错误统一收敛为 `LLMError`，
   携带契约 §5 的错误码，方便 SSE 出口直接透传。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.config import Settings


class LLMErrorCode:
    """契约 §5 错误码（字符串常量，便于直接进 SSE data）。"""

    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_AUTH_ERROR = "LLM_AUTH_ERROR"
    LLM_RATE_LIMIT = "LLM_RATE_LIMIT"
    # 契约表之外的补充：模型侧非超时/非鉴权异常（如 5xx、连接失败）
    LLM_UPSTREAM_ERROR = "LLM_UPSTREAM_ERROR"


class LLMError(Exception):
    """LLM 调用异常：code 直接映射为 SSE error 事件中的 code 字段。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider = provider
        self.retryable = retryable

    def __str__(self) -> str:  # pragma: no cover - 日志用
        return f"[{self.code}] {self.message}"


@dataclass(frozen=True)
class ProviderConfig:
    """单个模型提供方的静态配置。"""

    name: str
    model: str
    base_url: str
    api_key: str
    api_key_env: str  # 缺 Key 时用于中文报错提示

    @property
    def missing_key_error(self) -> LLMError:
        return LLMError(
            LLMErrorCode.LLM_AUTH_ERROR,
            f"未配置 {self.api_key_env}，无法调用 {self.name} 模型。"
            f"请在仓库根目录 .env 中填写 {self.api_key_env}，或将 LLM_PROVIDER 设为 mock 使用离线模式。",
            provider=self.name,
        )


class BaseLLMProvider(abc.ABC):
    """模型提供方抽象：负责把配置翻译成 LangChain ChatModel。"""

    name: str = "base"
    model_name: str = ""

    @abc.abstractmethod
    def build_chat_model(self) -> "BaseChatModel":
        """构造 LangChain ChatModel；配置缺失时抛 LLMError(LLM_AUTH_ERROR)。"""


def build_provider_config(settings: "Settings", name: str) -> ProviderConfig:
    """按 provider 名称取出配置（唯一映射来源，避免到处写 if-else）。"""
    mapping: dict[str, ProviderConfig] = {
        "mock": ProviderConfig(
            name="mock",
            model="mock-chat",
            base_url="",
            api_key="",
            api_key_env="",
        ),
        "deepseek": ProviderConfig(
            name="deepseek",
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            api_key_env="DEEPSEEK_API_KEY",
        ),
        "qwen": ProviderConfig(
            name="qwen",
            model=settings.qwen_model,
            base_url=settings.qwen_base_url,
            api_key=settings.dashscope_api_key,
            api_key_env="DASHSCOPE_API_KEY",
        ),
        "ollama": ProviderConfig(
            name="ollama",
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            # Ollama 是本地服务，OpenAI 兼容层允许任意非空占位 Key
            api_key="ollama",
            api_key_env="OLLAMA_BASE_URL",
        ),
    }
    if name not in mapping:
        raise LLMError(
            LLMErrorCode.LLM_AUTH_ERROR,
            f"未知的模型提供方 '{name}'，可选值：mock / deepseek / qwen / ollama。",
            provider=name,
        )
    return mapping[name]
