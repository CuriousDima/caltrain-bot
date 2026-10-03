import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest
from zoneinfo import ZoneInfo
from loguru import logger

from caltrain_bot.schedule import (
    check_feed_expiry,
    get_feed_end_date,
    is_after_feed_end,
)

_GTFS_PATH = Path(__file__).parent.parent.parent / "data" / "caltrain-ca-us.zip"

_CALENDAR = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\n"
    "weekday,1,1,1,1,1,0,0,20260101,20260630\n"
    "weekend,0,0,0,0,0,1,1,20260101,20260615\n"
)
_CALENDAR_DATES = "service_id,date,exception_type\nweekday,20260704,1\n"
_FEED_INFO = (
    "feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date,feed_end_date\n"
    "Test,http://example.com,en,20260101,20260831\n"
)


def _write_feed(tmp_path: Path, files: dict[str, str]) -> Path:
    path = tmp_path / "feed.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


@pytest.fixture
def log_messages():
    messages: list[str] = []
    handler_id = logger.add(
        lambda message: messages.append(message), format="{level}: {message}"
    )
    yield messages
    logger.remove(handler_id)


def test_feed_end_date_prefers_feed_info(tmp_path):
    feed = _write_feed(
        tmp_path,
        {
            "feed_info.txt": _FEED_INFO,
            "calendar.txt": _CALENDAR,
            "calendar_dates.txt": _CALENDAR_DATES,
        },
    )
    assert get_feed_end_date(feed) == date(2026, 8, 31)


def test_feed_end_date_falls_back_to_calendar(tmp_path):
    feed = _write_feed(
        tmp_path,
        {"calendar.txt": _CALENDAR, "calendar_dates.txt": _CALENDAR_DATES},
    )
    assert get_feed_end_date(feed) == date(2026, 7, 4)


def test_feed_end_date_falls_back_when_feed_info_has_no_end_date(tmp_path):
    feed = _write_feed(
        tmp_path,
        {
            "feed_info.txt": "feed_publisher_name,feed_end_date\nTest,\n",
            "calendar.txt": _CALENDAR,
        },
    )
    assert get_feed_end_date(feed) == date(2026, 6, 30)


def test_feed_end_date_raises_without_dates(tmp_path):
    feed = _write_feed(tmp_path, {"agency.txt": "agency_name\nTest\n"})
    with pytest.raises(ValueError):
        get_feed_end_date(feed)


@pytest.mark.parametrize(
    ("today", "expected_level"),
    [
        (date(2026, 8, 1), None),
        (date(2026, 8, 17), "WARNING"),
        (date(2026, 8, 31), "WARNING"),
        (date(2026, 9, 1), "ERROR"),
    ],
)
def test_check_feed_expiry_logs_by_days_left(
    tmp_path, log_messages, today, expected_level
):
    feed = _write_feed(tmp_path, {"feed_info.txt": _FEED_INFO})

    assert check_feed_expiry(feed, today=today) == date(2026, 8, 31)

    levels = [message.split(":", 1)[0] for message in log_messages]
    assert levels == ([expected_level] if expected_level else [])


@pytest.mark.parametrize(
    ("departure_time", "expected"),
    [
        (datetime(2026, 8, 31, 23, 59), False),
        (datetime(2026, 9, 1, 0, 1), True),
        # 2026-09-01 05:00 UTC is still 2026-08-31 in California.
        (datetime.fromisoformat("2026-09-01T05:00:00+00:00"), False),
        (datetime.fromisoformat("2026-09-01T08:00:00+00:00"), True),
    ],
)
def test_is_after_feed_end(departure_time, expected):
    assert is_after_feed_end(departure_time, date(2026, 8, 31)) is expected


def test_bundled_feed_has_not_expired():
    end_date = get_feed_end_date(_GTFS_PATH)
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    assert end_date >= today, (
        f"Bundled GTFS feed {_GTFS_PATH.name} expired on {end_date}. "
        "Download a fresh Caltrain GTFS feed into data/."
    )
