import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OpenRouterSettings:
    """Settings for the OpenRouter provider."""

    api_key: str
    model: str
    fallback_model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class Settings:
    """Configuration settings for the Caltrain Bot."""

    gtfs_file_path: Path
    preprocessing_sql_path: Path
    llm: OpenRouterSettings
    # Each frontend is switched on by its own variable; at least one is set.
    telegram_bot_token: str | None = None
    web_port: int | None = None


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} environment variable is required.")
    return value.strip()


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _load_llm_settings() -> OpenRouterSettings:
    return OpenRouterSettings(
        api_key=_require_env("OPENROUTER_API_KEY"),
        model=_require_env("OPENROUTER_MODEL"),
        fallback_model=_optional_env("OPENROUTER_FALLBACK_MODEL"),
        reasoning_effort=_optional_env("OPENROUTER_REASONING_EFFORT"),
    )


def _load_web_port() -> int | None:
    value = _optional_env("WEB_PORT")
    if value is None:
        return None
    if not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise ValueError("WEB_PORT must be a port number between 1 and 65535.")
    return int(value)


def load_settings() -> Settings:
    """Load settings from environment variables."""
    telegram_bot_token = _optional_env("TELEGRAM_BOT_TOKEN")
    web_port = _load_web_port()
    if telegram_bot_token is None and web_port is None:
        raise ValueError(
            "Set TELEGRAM_BOT_TOKEN to run the Telegram bot, WEB_PORT to serve "
            "the web page, or both."
        )

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
        gtfs_file_path=gtfs_file_path,
        preprocessing_sql_path=preprocessing_sql_path,
        llm=_load_llm_settings(),
        telegram_bot_token=telegram_bot_token,
        web_port=web_port,
    )
