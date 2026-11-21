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
    usage_db_path: Path
    # Each frontend is switched on by its own variable; at least one is set.
    telegram_bot_token: str | None = None
    web_port: int | None = None
    # The private statistics page; served only next to the web page.
    stats_port: int | None = None


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


def get_usage_db_path() -> Path:
    """Where web usage statistics are stored; ``USAGE_DB_PATH`` overrides it."""
    value = _optional_env("USAGE_DB_PATH")
    if value is not None:
        return Path(value)
    return Path(__file__).parent.parent.parent / "data" / "usage.db"


def _load_port(name: str) -> int | None:
    value = _optional_env(name)
    if value is None:
        return None
    if not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise ValueError(f"{name} must be a port number between 1 and 65535.")
    return int(value)


def _load_stats_port(web_port: int | None) -> int | None:
    stats_port = _load_port("STATS_PORT")
    if stats_port is None:
        return None
    if web_port is None:
        raise ValueError(
            "STATS_PORT is set but WEB_PORT is not: the statistics page reports "
            "on the web page. Set WEB_PORT as well, or remove STATS_PORT."
        )
    if stats_port == web_port:
        raise ValueError(
            "STATS_PORT must differ from WEB_PORT: the statistics page is "
            "private and must not share the public page's port."
        )
    return stats_port


def load_settings() -> Settings:
    """Load settings from environment variables."""
    telegram_bot_token = _optional_env("TELEGRAM_BOT_TOKEN")
    web_port = _load_port("WEB_PORT")
    stats_port = _load_stats_port(web_port)
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
        usage_db_path=get_usage_db_path(),
        telegram_bot_token=telegram_bot_token,
        web_port=web_port,
        stats_port=stats_port,
    )
