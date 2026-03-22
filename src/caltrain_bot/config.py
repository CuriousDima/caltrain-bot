import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Provider(Enum):
    """Enum for supported language model providers."""

    OLLAMA = "ollama"
    OPENROUTER = "openrouter"


@dataclass(frozen=True)
class OllamaSettings:
    """Settings for the Ollama provider."""

    provider: Provider
    api_base: str
    model: str


@dataclass(frozen=True)
class OpenRouterSettings:
    """Settings for the OpenRouter provider."""

    provider: Provider
    api_key: str
    model: str


LLMSettings = OllamaSettings | OpenRouterSettings


@dataclass(frozen=True)
class Settings:
    """Configuration settings for the Caltrain Bot."""

    telegram_bot_token: str
    gtfs_file_path: Path
    preprocessing_sql_path: Path
    llm: LLMSettings


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} environment variable is required.")
    return value.strip()


def _load_llm_settings() -> LLMSettings:
    provider_raw = _require_env("LLM_PROVIDER").lower()
    try:
        provider = Provider(provider_raw)
    except ValueError as exc:
        supported = ", ".join(provider.value for provider in Provider)
        raise ValueError(
            f"LLM_PROVIDER must be one of: {supported}. Got: {provider_raw}"
        ) from exc

    if provider is Provider.OLLAMA:
        return OllamaSettings(
            provider=provider,
            api_base=_require_env("OLLAMA_API_BASE"),
            model=_require_env("OLLAMA_MODEL"),
        )

    return OpenRouterSettings(
        provider=provider,
        api_key=_require_env("OPENROUTER_API_KEY"),
        model=_require_env("OPENROUTER_MODEL"),
    )


def load_settings() -> Settings:
    """Load settings from environment variables."""
    telegram_bot_token = _require_env("TELEGRAM_BOT_TOKEN")

    gtfs_file_path: Path = (
        Path(__file__).parent.parent.parent / "data" / "caltrain-ca-us.zip"
    )
    if not gtfs_file_path.exists():
        raise FileNotFoundError(f"GTFS file not found at: {gtfs_file_path}")

    preprocessing_sql_path: Path = (
        Path(__file__).parent.parent.parent / "sql" / "train_stop_timeline.sql"
    )
    if not preprocessing_sql_path.exists():
        raise FileNotFoundError(
            f"Preprocessing SQL file not found at: {preprocessing_sql_path}"
        )

    return Settings(
        telegram_bot_token=telegram_bot_token,
        gtfs_file_path=gtfs_file_path,
        preprocessing_sql_path=preprocessing_sql_path,
        llm=_load_llm_settings(),
    )
