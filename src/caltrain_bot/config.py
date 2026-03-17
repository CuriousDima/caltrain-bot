import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Configuration settings for the Caltrain Bot."""

    telegram_bot_token: str


def load_settings() -> Settings:
    """Load settings from environment variables."""
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required.")
    return Settings(telegram_bot_token=telegram_bot_token)
