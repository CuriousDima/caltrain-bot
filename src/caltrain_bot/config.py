import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Configuration settings for the Caltrain Bot."""

    telegram_bot_token: str
    gtfs_file_path: Path
    preprocessing_sql_path: Path


def load_settings() -> Settings:
    """Load settings from environment variables."""
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required.")

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
    )
