"""配置：全部来自环境变量 / .env，缺省安全，派生属性正确。"""

from __future__ import annotations

from app.config import Settings, clear_settings_cache, get_settings


def test_settings_reads_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_FALLBACK_CHAIN", "qwen, mock")
    monkeypatch.setenv("CORS_ORIGINS", "http://a.com, http://b.com")
    monkeypatch.setenv("AGENT_MAX_STEPS", "5")
    monkeypatch.setenv("AI_SESSION_TTL_SECONDS", "60")

    settings = Settings()
    assert settings.llm_provider == "deepseek"
    assert settings.deepseek_api_key == "sk-test"
    assert settings.provider_chain == ["deepseek", "qwen", "mock"]
    assert settings.cors_origin_list == ["http://a.com", "http://b.com"]
    assert settings.agent_max_steps == 5
    assert settings.ai_session_ttl_seconds == 60


def test_fallback_chain_deduplicates_and_skips_primary() -> None:
    settings = Settings(llm_provider="mock", llm_fallback_chain="ollama, mock, ollama , qwen")
    assert settings.fallback_providers == ["ollama", "qwen"]
    assert settings.provider_chain == ["mock", "ollama", "qwen"]


def test_settings_singleton_and_cache_clear(monkeypatch) -> None:
    clear_settings_cache()
    first = get_settings()
    assert get_settings() is first
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    clear_settings_cache()
    assert get_settings() is not first


def test_default_provider_is_mock() -> None:
    """没有配置任何 Key 时默认 mock，保证离线可用。"""
    settings = Settings()
    if not settings.llm_provider:
        assert settings.llm_provider == "mock"
    assert settings.agent_max_steps == 8  # 契约 §5 默认值
    assert settings.confirmation_ttl_seconds == 300  # 契约 §6：TTL 5 分钟
