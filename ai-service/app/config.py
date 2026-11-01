"""服务配置：全部来自环境变量 / .env，禁止硬编码任何密钥。

优先级：进程环境变量 > ai-service/.env > 仓库根目录 .env（与 Java 侧共用同一份模板）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ai-service/app/config.py -> ai-service/
SERVICE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVICE_ROOT.parent

LLMProviderName = Literal["mock", "deepseek", "qwen", "ollama"]


class Settings(BaseSettings):
    """AI 服务运行期配置。

    字段名与 .env.example 中的大写下划线变量一一对应（大小写不敏感）。
    """

    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), str(SERVICE_ROOT / ".env")),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 服务基础 ----
    service_name: str = "library-ai-service"
    service_version: str = "w1"
    log_level: str = "INFO"

    # ---- LLM ----
    llm_provider: LLMProviderName = "mock"
    llm_fallback_chain: str = ""
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=1, ge=0)

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"

    dashscope_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"

    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_model: str = "qwen2.5:7b"

    # ---- Java 内部工具接口 ----
    java_api_base: str = "http://127.0.0.1:18080"
    ai_internal_token: str = ""
    java_connect_timeout_seconds: float = Field(default=3.0, gt=0)
    java_read_timeout_seconds: float = Field(default=10.0, gt=0)
    # 会话上下文（/context、/rules/text）预取超时，失败时可降级继续对话
    java_context_timeout_seconds: float = Field(default=3.0, gt=0)

    # ---- 会话 / 确认状态 ----
    ai_session_ttl_seconds: int = Field(default=3600, gt=0)
    ai_max_history_turns: int = Field(default=8, gt=0)
    # 写操作确认卡片有效期（契约 §6：TTL 5 分钟）
    confirmation_ttl_seconds: int = Field(default=300, gt=0)
    # Agent 最大循环步数（契约 §5：默认 8）
    agent_max_steps: int = Field(default=8, gt=0)

    # ---- Redis（W2：会话/确认状态与 LangGraph 检查点持久化）----
    # 与 Spring 侧复用同一个 Redis（不同 db，默认 db=1）；连不上时自动降级为进程内存储
    ai_redis_url: str = "redis://127.0.0.1:6379/1"
    # 统一 key 前缀，避免与 Spring 业务 key 互相污染
    ai_redis_key_prefix: str = "ai"
    ai_redis_connect_timeout_seconds: float = Field(default=1.0, gt=0)
    ai_redis_socket_timeout_seconds: float = Field(default=1.0, gt=0)
    # LangGraph 检查点 TTL（分钟，0 = 永不过期）
    ai_checkpoint_ttl_minutes: int = Field(default=1440, ge=0)

    # ---- Web ----
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- 可观测（W4 启用，W1 仅占位）----
    langfuse_enabled: bool = False

    # ------------------------------------------------------------------
    # 派生属性
    # ------------------------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def fallback_providers(self) -> list[str]:
        """解析 LLM_FALLBACK_CHAIN，过滤空值与主 provider 自身。"""
        result: list[str] = []
        for raw in self.llm_fallback_chain.split(","):
            name = raw.strip().lower()
            if name and name not in result and name != self.llm_provider:
                result.append(name)
        return result

    @property
    def provider_chain(self) -> list[str]:
        """实际尝试顺序：主模型 + 降级链。"""
        return [self.llm_provider, *self.fallback_providers]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程级单例配置；测试中修改环境变量后可调用 clear_settings_cache()。"""
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
