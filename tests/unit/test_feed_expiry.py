import zipfile
from datetime import date
from pathlib import Path

import pytest
from loguru import logger

from caltrain_bot.schedule import check_feed_expiry, get_feed_end_date

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
    ("today", "expected_days_left", "expected_level"),
    [
        (date(2026, 8, 1), 30, None),
        (date(2026, 8, 17), 14, "WARNING"),
        (date(2026, 8, 31), 0, "WARNING"),
        (date(2026, 9, 1), -1, "ERROR"),
    ],
)
def test_check_feed_expiry_logs_by_days_left(
    tmp_path, log_messages, today, expected_days_left, expected_level
):
    feed = _write_feed(tmp_path, {"feed_info.txt": _FEED_INFO})

    assert check_feed_expiry(feed, today=today) == expected_days_left

    levels = [message.split(":", 1)[0] for message in log_messages]
    assert levels == ([expected_level] if expected_level else [])


def test_bundled_feed_has_not_expired():
    end_date = get_feed_end_date(_GTFS_PATH)
    assert check_feed_expiry(_GTFS_PATH) >= 0, (
        f"Bundled GTFS feed {_GTFS_PATH.name} expired on {end_date}. "
        "Download a fresh Caltrain GTFS feed into data/."
    )
