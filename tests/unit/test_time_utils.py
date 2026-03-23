from datetime import datetime
from zoneinfo import ZoneInfo

from caltrain_bot.time_utils import format_datetime_for_sql, normalize_to_server_timezone


def test_normalize_to_server_timezone_converts_aware_datetime(monkeypatch):
    monkeypatch.setattr(
        "caltrain_bot.time_utils.get_server_timezone",
        lambda: ZoneInfo("America/Los_Angeles"),
    )

    value = datetime.fromisoformat("2026-03-23T02:25:23+00:00")

    assert normalize_to_server_timezone(value) == datetime(2026, 3, 22, 19, 25, 23)


def test_format_datetime_for_sql_uses_server_local_wall_time(monkeypatch):
    monkeypatch.setattr(
        "caltrain_bot.time_utils.get_server_timezone",
        lambda: ZoneInfo("America/Los_Angeles"),
    )

    value = datetime.fromisoformat("2026-03-23T02:25:23+00:00")

    assert format_datetime_for_sql(value) == "2026-03-22 19:25:23"


def test_format_datetime_for_sql_keeps_naive_server_local_values(monkeypatch):
    monkeypatch.setattr(
        "caltrain_bot.time_utils.get_server_timezone",
        lambda: ZoneInfo("America/Los_Angeles"),
    )

    value = datetime(2026, 3, 22, 19, 25, 23)

    assert format_datetime_for_sql(value) == "2026-03-22 19:25:23"
