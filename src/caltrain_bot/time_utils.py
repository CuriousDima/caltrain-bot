import os
from datetime import datetime, tzinfo
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger


def _extract_zone_name(localtime_path: Path) -> str | None:
    parts = localtime_path.parts
    for index, part in enumerate(parts):
        if part.startswith("zoneinfo") and index + 1 < len(parts):
            return "/".join(parts[index + 1 :])
    return None


@lru_cache(maxsize=1)
def get_server_timezone() -> tzinfo:
    """Return the server's configured local timezone."""
    tz_name = os.getenv("TZ")
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            logger.warning(
                "Ignoring invalid TZ environment variable while resolving local timezone: {}",
                tz_name,
            )

    localtime_path = Path("/etc/localtime")
    if localtime_path.exists():
        zone_name = _extract_zone_name(localtime_path.resolve())
        if zone_name:
            try:
                return ZoneInfo(zone_name)
            except ZoneInfoNotFoundError:
                logger.warning(
                    "Could not load timezone from /etc/localtime target: {}",
                    zone_name,
                )

    fallback_timezone = datetime.now().astimezone().tzinfo
    if fallback_timezone is None:
        raise RuntimeError("Unable to determine the server's local timezone.")
    return fallback_timezone


def get_current_server_datetime() -> datetime:
    """Return the current datetime in the server's local timezone."""
    return datetime.now(get_server_timezone())


def normalize_to_server_timezone(value: datetime) -> datetime:
    """Convert timezone-aware values into server-local wall time.

    Naive values are treated as already being in server-local time.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(get_server_timezone()).replace(tzinfo=None)


def format_datetime_for_sql(value: datetime) -> str:
    """Serialize datetimes for SQLite using server-local wall time."""
    normalized_value = normalize_to_server_timezone(value)
    return normalized_value.strftime("%Y-%m-%d %H:%M:%S")
