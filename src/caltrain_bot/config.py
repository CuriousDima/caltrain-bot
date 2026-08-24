import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OpenRouterSettings:
    """Settings for the OpenRouter provider."""

    api_key: str
    model: str


@dataclass(frozen=True)
class Settings:
    """Configuration settings for the Caltrain Bot."""

    telegram_bot_token: str
    gtfs_file_path: Path
    preprocessing_sql_path: Path
    llm: OpenRouterSettings


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} environment variable is required.")
    return value.strip()


def _load_llm_settings() -> OpenRouterSettings:
    return OpenRouterSettings(
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
