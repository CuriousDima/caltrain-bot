import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from caltrain_bot import usage
from caltrain_bot.usage import DailyUsage, UsageLog, UsageTotals

PACIFIC = ZoneInfo("America/Los_Angeles")
ANA = "a" * 32
BEN = "b" * 32


@pytest.fixture
def usage_log(tmp_path):
    log = UsageLog(tmp_path / "nested" / "usage.db")
    yield log
    log.close()


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=PACIFIC)


def test_daily_counts_users_questions_and_outcomes(usage_log):
    usage_log.record_question(ANA, "trains", 900, now=_at(1))
    usage_log.record_question(ANA, "no_trains", 1100, now=_at(1, 18))
    usage_log.record_question(ANA, "trains", 1000, now=_at(2))
    usage_log.record_question(BEN, "unsupported", 1200, now=_at(2))
    usage_log.record_question(None, "error", 20000, now=_at(2))
    usage_log.record_question(BEN, "feed_expired", 800, now=_at(2))

    assert usage_log.daily(days=14) == [
        DailyUsage(
            day="2026-08-02",
            users=2,
            new_users=1,
            questions=4,
            with_trains=1,
            no_trains=0,
            unsupported=1,
            failed=2,
            average_ms=5750,
        ),
        DailyUsage(
            day="2026-08-01",
            users=1,
            new_users=1,
            questions=2,
            with_trains=1,
            no_trains=1,
            unsupported=0,
            failed=0,
            average_ms=1000,
        ),
    ]
    assert usage_log.daily(days=1)[0].day == "2026-08-02"


def test_days_follow_caltrain_local_time(usage_log):
    late_evening_pacific = datetime(2026, 8, 2, 6, 30, tzinfo=ZoneInfo("UTC"))

    usage_log.record_question(ANA, "trains", 900, now=late_evening_pacific)

    assert usage_log.daily(days=1)[0].day == "2026-08-01"


def test_totals_count_recently_active_users(usage_log):
    usage_log.record_question(ANA, "trains", 900, now=_at(2))
    usage_log.record_question(BEN, "trains", 900, now=_at(20))
    usage_log.record_question(BEN, "trains", 900, now=_at(30))
    usage_log.record_question(None, "trains", 900, now=_at(30))

    assert usage_log.totals(now=_at(31)) == UsageTotals(
        users=2, questions=4, users_last_7_days=1, users_last_30_days=2
    )
    assert usage_log.totals(now=_at(31).replace(month=12)) == UsageTotals(
        users=2, questions=4, users_last_7_days=0, users_last_30_days=0
    )


def test_usage_survives_reopening_the_file(tmp_path):
    path = tmp_path / "usage.db"
    first = UsageLog(path)
    first.record_question(ANA, "trains", 900, now=_at(1))
    first.close()

    second = UsageLog(path)
    try:
        assert second.totals(now=_at(2)).questions == 1
    finally:
        second.close()


def test_record_question_never_raises(usage_log):
    usage_log.close()

    usage_log.record_question(ANA, "trains", 900)


def test_stored_rows_hold_no_question_text_or_address(usage_log, tmp_path):
    usage_log.record_question(ANA, "trains", 900, now=_at(1))

    columns = [
        row[1]
        for row in sqlite3.connect(tmp_path / "nested" / "usage.db").execute(
            "PRAGMA table_info(web_questions)"
        )
    ]

    assert columns == ["asked_at", "day", "visitor", "status", "duration_ms"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (ANA, ANA),
        (None, None),
        ("", None),
        ("A" * 32, None),
        ("a" * 31, None),
        ("a" * 33, None),
        ("a" * 31 + "\n", None),
        ("'; DROP TABLE web_questions; --", None),
    ],
)
def test_normalize_visitor_id_accepts_only_the_page_format(value, expected):
    assert usage.normalize_visitor_id(value) == expected


def test_open_usage_log_returns_none_when_the_file_cannot_be_used(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")

    assert usage.open_usage_log(blocker / "usage.db") is None


def test_format_report_lists_days_and_totals(usage_log):
    usage_log.record_question(ANA, "trains", 900, now=_at(1))
    usage_log.record_question(BEN, "unsupported", 1100, now=_at(1))

    report = usage.format_report(usage_log.daily(14), usage_log.totals(now=_at(2)))

    lines = report.splitlines()
    assert lines[2].split() == [
        "Day",
        "Users",
        "New",
        "Questions",
        "Trains",
        "No",
        "trains",
        "Off-topic",
        "Failed",
        "Avg",
        "ms",
    ]
    assert lines[3].split() == ["2026-08-01", "2", "2", "2", "1", "0", "1", "0", "1000"]
    assert "All time: 2 users, 2 questions" in report
    assert "2 in the last 7 days, 2 in the last 30 days" in report


def test_format_report_says_when_nothing_was_asked(usage_log):
    report = usage.format_report(usage_log.daily(14), usage_log.totals())

    assert report == "No questions have been asked on the web page yet."


def test_main_prints_the_report(tmp_path, monkeypatch, capsys):
    path = tmp_path / "usage.db"
    log = UsageLog(path)
    log.record_question(ANA, "trains", 900)
    log.close()
    monkeypatch.setattr(usage, "load_dotenv", lambda: False)
    monkeypatch.setenv("USAGE_DB_PATH", str(path))
    monkeypatch.setattr("sys.argv", ["caltrain-bot-usage", "--days", "3"])

    usage.main()

    assert "All time: 1 users, 1 questions" in capsys.readouterr().out


def test_main_explains_a_missing_usage_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(usage, "load_dotenv", lambda: False)
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setattr("sys.argv", ["caltrain-bot-usage"])

    usage.main()

    assert "No usage recorded yet" in capsys.readouterr().out
    assert not (tmp_path / "missing.db").exists()
