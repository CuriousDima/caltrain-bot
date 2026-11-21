import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from caltrain_bot import usage
from caltrain_bot.usage import DailyUsage, UsageLog, UsageTotals, VisitCount

PACIFIC = ZoneInfo("America/Los_Angeles")
ANA = "a" * 32
BEN = "b" * 32
CAT = "c" * 32

IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/26.1 Mobile/15E148 Safari/604.1"
)
ANDROID = (
    "Mozilla/5.0 (Linux; Android 16; Pixel 10) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Mobile Safari/537.36"
)
MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/26.1 Safari/605.1.15"
)
WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)
LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0"
GOOGLEBOT = (
    "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Mobile Safari/537.36 "
    "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
)


@pytest.fixture
def usage_log(tmp_path):
    log = UsageLog(tmp_path / "nested" / "usage.db")
    yield log
    log.close()


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=PACIFIC)


def _visit(log, visitor, day, country="US", device="mobile", referrer=None):
    log.record_visit(visitor, country, device, referrer, now=_at(day))


def _day(day: str, **counts) -> DailyUsage:
    """A day with the given counts; everything not named is zero."""
    values = {
        "visits": 0,
        "visitors": 0,
        "new_visitors": 0,
        "questions": 0,
        "with_trains": 0,
        "no_trains": 0,
        "unsupported": 0,
        "failed": 0,
        "average_ms": None,
    }
    return DailyUsage(day=day, **{**values, **counts})


def test_daily_counts_visitors_questions_and_outcomes(usage_log):
    usage_log.record_question(ANA, "trains", 900, now=_at(1))
    usage_log.record_question(ANA, "no_trains", 1100, now=_at(1, 18))
    usage_log.record_question(ANA, "trains", 1000, now=_at(2))
    usage_log.record_question(BEN, "unsupported", 1200, now=_at(2))
    usage_log.record_question(None, "error", 20000, now=_at(2))
    usage_log.record_question(BEN, "feed_expired", 800, now=_at(2))

    assert usage_log.daily(days=14, now=_at(2)) == [
        DailyUsage(
            day="2026-08-02",
            visits=0,
            visitors=2,
            new_visitors=1,
            questions=4,
            with_trains=1,
            no_trains=0,
            unsupported=1,
            failed=2,
            average_ms=5750,
        ),
        DailyUsage(
            day="2026-08-01",
            visits=0,
            visitors=1,
            new_visitors=1,
            questions=2,
            with_trains=1,
            no_trains=1,
            unsupported=0,
            failed=0,
            average_ms=1000,
        ),
    ]


def test_daily_lists_only_the_last_days(usage_log):
    _visit(usage_log, ANA, day=1)
    usage_log.record_question(ANA, "trains", 900, now=_at(2))
    _visit(usage_log, ANA, day=5)

    days = [usage.day for usage in usage_log.daily(days=4, now=_at(5, 23))]

    # Four calendar days ending today: August 2 to 5. Day 3 and 4 had no use.
    assert days == ["2026-08-05", "2026-08-02"]
    assert [usage.day for usage in usage_log.daily(days=1, now=_at(5))] == [
        "2026-08-05"
    ]
    assert usage_log.daily(days=3, now=_at(20)) == []


def test_days_follow_caltrain_local_time(usage_log):
    late_evening_pacific = datetime(2026, 8, 2, 6, 30, tzinfo=ZoneInfo("UTC"))

    usage_log.record_question(ANA, "trains", 900, now=late_evening_pacific)
    usage_log.record_visit(ANA, "US", "mobile", None, now=late_evening_pacific)

    assert usage_log.daily(days=1, now=late_evening_pacific) == [
        _day(
            "2026-08-01",
            visits=1,
            visitors=1,
            new_visitors=1,
            questions=1,
            with_trains=1,
            average_ms=900,
        )
    ]


def test_daily_counts_visits_on_days_without_questions(usage_log):
    _visit(usage_log, ANA, day=1)
    _visit(usage_log, ANA, day=1)
    _visit(usage_log, BEN, day=1)
    _visit(usage_log, None, day=1)

    assert usage_log.daily(days=7, now=_at(1)) == [
        _day("2026-08-01", visits=4, visitors=2, new_visitors=2)
    ]


def test_viewing_and_asking_count_as_the_same_visitor(usage_log):
    # Ana only opened the page, Ben only asked, Cat did both.
    _visit(usage_log, ANA, day=1)
    usage_log.record_question(BEN, "trains", 900, now=_at(1))
    _visit(usage_log, CAT, day=1)
    usage_log.record_question(CAT, "trains", 900, now=_at(1))

    assert usage_log.daily(days=7, now=_at(1)) == [
        _day(
            "2026-08-01",
            visits=2,
            visitors=3,
            new_visitors=3,
            questions=2,
            with_trains=2,
            average_ms=900,
        )
    ]
    assert usage_log.totals(now=_at(1)).visitors == 3


def test_a_visitor_is_new_only_on_the_first_day_seen_anywhere(usage_log):
    # Ana first asked on day 1, before visits were recorded at all.
    usage_log.record_question(ANA, "trains", 900, now=_at(1))
    _visit(usage_log, ANA, day=2)
    _visit(usage_log, BEN, day=2)
    usage_log.record_question(BEN, "trains", 900, now=_at(3))
    usage_log.record_question(CAT, "trains", 900, now=_at(3))
    _visit(usage_log, None, day=3)

    new_visitors = {
        usage.day: (usage.visitors, usage.new_visitors)
        for usage in usage_log.daily(days=7, now=_at(3))
    }

    assert new_visitors == {
        "2026-08-01": (1, 1),
        "2026-08-02": (2, 1),
        "2026-08-03": (2, 1),
    }


def test_a_visitor_seen_before_the_listed_days_is_not_new(usage_log):
    _visit(usage_log, ANA, day=1)
    _visit(usage_log, ANA, day=20)

    assert usage_log.daily(days=2, now=_at(20)) == [
        _day("2026-08-20", visits=1, visitors=1, new_visitors=0)
    ]


def test_totals_count_recently_active_visitors(usage_log):
    usage_log.record_question(ANA, "trains", 900, now=_at(2))
    usage_log.record_question(BEN, "trains", 900, now=_at(20))
    usage_log.record_question(BEN, "trains", 900, now=_at(30))
    usage_log.record_question(None, "trains", 900, now=_at(30))

    assert usage_log.totals(now=_at(31)) == UsageTotals(
        visitors=2,
        visits=0,
        questions=4,
        visitors_last_7_days=1,
        visitors_last_30_days=2,
    )
    assert usage_log.totals(now=_at(31).replace(month=12)) == UsageTotals(
        visitors=2,
        visits=0,
        questions=4,
        visitors_last_7_days=0,
        visitors_last_30_days=0,
    )


def test_totals_join_visits_and_questions(usage_log):
    usage_log.record_question(ANA, "trains", 900, now=_at(2))
    _visit(usage_log, ANA, day=29)
    _visit(usage_log, BEN, day=20)
    _visit(usage_log, CAT, day=30)
    _visit(usage_log, None, day=30)

    assert usage_log.totals(now=_at(31)) == UsageTotals(
        visitors=3,
        visits=4,
        questions=1,
        visitors_last_7_days=2,
        visitors_last_30_days=3,
    )


def test_top_countries_and_referrers_rank_visits_in_the_window(usage_log):
    _visit(usage_log, ANA, day=1, country="FR", referrer="old.example")
    _visit(usage_log, ANA, day=10, country="US", referrer="news.example")
    _visit(usage_log, ANA, day=10, country="US", referrer="news.example")
    _visit(usage_log, BEN, day=11, country="US", referrer=None)
    _visit(usage_log, BEN, day=11, country="CA", referrer=None)
    _visit(usage_log, CAT, day=12, country=None, referrer="blog.example")
    _visit(usage_log, None, day=12, country="CA", referrer=None)

    assert usage_log.top_countries(days=7, now=_at(12)) == [
        VisitCount("US", visits=3, visitors=2),
        VisitCount("CA", visits=2, visitors=1),
        VisitCount(None, visits=1, visitors=1),
    ]
    assert usage_log.top_referrers(days=7, now=_at(12)) == [
        VisitCount(None, visits=3, visitors=1),
        VisitCount("news.example", visits=2, visitors=1),
        VisitCount("blog.example", visits=1, visitors=1),
    ]
    assert usage_log.top_countries(days=7, limit=1, now=_at(12)) == [
        VisitCount("US", visits=3, visitors=2)
    ]
    assert usage_log.top_countries(days=30, now=_at(12))[-1] == VisitCount(
        "FR", visits=1, visitors=1
    )


def test_devices_split_visits_in_the_window(usage_log):
    _visit(usage_log, ANA, day=1, device="other")
    _visit(usage_log, ANA, day=10, device="mobile")
    _visit(usage_log, BEN, day=10, device="mobile")
    _visit(usage_log, BEN, day=11, device="desktop")

    assert usage_log.devices(days=7, now=_at(12)) == [
        VisitCount("mobile", visits=2, visitors=2),
        VisitCount("desktop", visits=1, visitors=1),
    ]


def test_stats_gather_everything_for_one_window(usage_log):
    _visit(usage_log, ANA, day=10, referrer="news.example")
    usage_log.record_question(ANA, "trains", 900, now=_at(10))

    stats = usage_log.stats(days=7, now=_at(12))

    assert stats.days == 7
    assert stats.totals == usage_log.totals(now=_at(12))
    assert stats.daily == usage_log.daily(7, now=_at(12))
    assert stats.countries == [VisitCount("US", visits=1, visitors=1)]
    assert stats.referrers == [VisitCount("news.example", visits=1, visitors=1)]
    assert stats.devices == [VisitCount("mobile", visits=1, visitors=1)]


def test_an_empty_log_reports_zeros(usage_log):
    stats = usage_log.stats(days=30)

    assert stats.totals == UsageTotals(0, 0, 0, 0, 0)
    assert (stats.daily, stats.countries, stats.referrers, stats.devices) == (
        [],
        [],
        [],
        [],
    )


def test_usage_survives_reopening_the_file(tmp_path):
    path = tmp_path / "usage.db"
    first = UsageLog(path)
    first.record_question(ANA, "trains", 900, now=_at(1))
    first.record_visit(ANA, "US", "mobile", None, now=_at(1))
    first.close()

    second = UsageLog(path)
    try:
        totals = second.totals(now=_at(2))
        assert (totals.questions, totals.visits) == (1, 1)
    finally:
        second.close()


def test_a_database_from_before_visits_keeps_working(tmp_path):
    path = tmp_path / "usage.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE web_questions (
            asked_at INTEGER NOT NULL,
            day TEXT NOT NULL,
            visitor TEXT,
            status TEXT NOT NULL,
            duration_ms INTEGER NOT NULL
        );
        CREATE INDEX idx_web_questions_day ON web_questions (day);
        """
    )
    old.execute(
        "INSERT INTO web_questions VALUES (?, '2026-08-01', ?, 'trains', 900)",
        (int(_at(1).timestamp()), ANA),
    )
    old.commit()
    old.close()

    log = UsageLog(path)
    try:
        assert log.daily(days=7, now=_at(2)) == [
            _day(
                "2026-08-01",
                visitors=1,
                new_visitors=1,
                questions=1,
                with_trains=1,
                average_ms=900,
            )
        ]
        log.record_visit(ANA, "US", "mobile", None, now=_at(2))
        log.record_visit(BEN, "US", "mobile", None, now=_at(2))
        today = log.daily(days=7, now=_at(2))[0]
        assert (today.visits, today.visitors, today.new_visitors) == (2, 2, 1)
        assert log.totals(now=_at(2)).questions == 1
    finally:
        log.close()


def test_recording_never_raises(usage_log):
    usage_log.close()

    usage_log.record_question(ANA, "trains", 900)
    usage_log.record_visit(ANA, "US", "mobile", None)


def test_stored_rows_hold_no_question_text_address_or_user_agent(usage_log, tmp_path):
    usage_log.record_question(ANA, "trains", 900, now=_at(1))
    usage_log.record_visit(ANA, "US", "mobile", "news.example", now=_at(1))
    connection = sqlite3.connect(tmp_path / "nested" / "usage.db")

    def columns(table):
        return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]

    assert columns("web_questions") == [
        "asked_at",
        "day",
        "visitor",
        "status",
        "duration_ms",
    ]
    assert columns("web_visits") == [
        "visited_at",
        "day",
        "visitor",
        "country",
        "device",
        "referrer",
    ]
    assert connection.execute("SELECT * FROM web_visits").fetchall() == [
        (int(_at(1).timestamp()), "2026-08-01", ANA, "US", "mobile", "news.example")
    ]
    connection.close()


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("US", "US"),
        ("de", "DE"),
        ("XX", None),
        ("xx", None),
        ("T1", None),
        (None, None),
        ("", None),
        ("USA", None),
        ("U", None),
        ("U1", None),
        ("US\n", None),
        (" US", None),
        ("ÅÄ", None),
        ("<b", None),
    ],
)
def test_normalize_country_accepts_only_two_letter_codes(value, expected):
    assert usage.normalize_country(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("news.ycombinator.com", "news.ycombinator.com"),
        ("t.co", "t.co"),
        ("xn--bcher-kva.example", "xn--bcher-kva.example"),
        ("com.google.android.gm", "com.google.android.gm"),
        ("", None),
        (None, None),
        (5, None),
        (["news.example"], None),
        ("localhost", None),
        ("News.Example", None),
        ("https://news.example/story", None),
        ("news.example/story", None),
        ("news.example:8080", None),
        ("news.example\n", None),
        ("news..example", None),
        ("-news.example", None),
        ("news.example.", None),
        ("<script>alert(1)</script>", None),
        ("a b.example", None),
        ("a" * 60 + "." + "b" * 39, "a" * 60 + "." + "b" * 39),
        ("a" * 60 + "." + "b" * 40, None),
        ("a" * 64 + ".example", None),
    ],
)
def test_normalize_referrer_accepts_only_lowercase_host_names(value, expected):
    assert usage.normalize_referrer(value) == expected


@pytest.mark.parametrize(
    ("value", "own_host", "expected"),
    [
        ("caltrain.help", "caltrain.help", None),
        ("caltrain.help", "Caltrain.Help:8080", None),
        ("news.example", "caltrain.help", "news.example"),
        ("news.example", "", "news.example"),
        ("news.example", None, "news.example"),
    ],
)
def test_normalize_referrer_drops_the_pages_own_host(value, own_host, expected):
    assert usage.normalize_referrer(value, own_host) == expected


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        (IPHONE, "mobile"),
        (ANDROID, "mobile"),
        (MAC, "desktop"),
        (WINDOWS, "desktop"),
        (LINUX, "desktop"),
        (GOOGLEBOT, "other"),
        ("curl/8.7.1", "other"),
        ("Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/142.0.0.0", "other"),
        ("", "other"),
        (None, "other"),
        ("x" * 5000 + IPHONE, "other"),
    ],
)
def test_classify_device_keeps_only_a_coarse_class(user_agent, expected):
    assert usage.classify_device(user_agent) == expected


def test_open_usage_log_returns_none_when_the_file_cannot_be_used(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")

    assert usage.open_usage_log(blocker / "usage.db") is None


def test_format_report_lists_days_breakdowns_and_totals(usage_log):
    usage_log.record_question(ANA, "trains", 900, now=_at(1))
    usage_log.record_question(BEN, "unsupported", 1100, now=_at(1))
    _visit(usage_log, ANA, day=1, country="US", referrer="news.example")
    _visit(usage_log, CAT, day=1, country=None, device="desktop")
    _visit(usage_log, CAT, day=2, country="US", device="desktop")

    report = usage.format_report(usage_log.stats(14, now=_at(2)))

    lines = report.splitlines()
    assert lines[0] == "Web page usage, last 14 days (days in Caltrain local time)"
    assert lines[2].split() == [
        "Day",
        "Visits",
        "Visitors",
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
    assert lines[3].split() == [
        "2026-08-02",
        "1",
        "1",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "-",
    ]
    assert lines[4].split() == [
        "2026-08-01",
        "2",
        "3",
        "3",
        "2",
        "1",
        "0",
        "1",
        "0",
        "1000",
    ]
    assert "Visits by country: US 2, unknown 1" in lines
    assert "Visits by referrer: none 2, news.example 1" in lines
    assert "Visits by device: desktop 2, mobile 1" in lines
    assert "All time: 3 visitors, 3 visits, 2 questions" in lines
    assert "Active visitors: 3 in the last 7 days, 3 in the last 30 days" in lines


def test_format_report_says_when_the_listed_days_are_empty(usage_log):
    usage_log.record_question(ANA, "trains", 900, now=_at(1))

    report = usage.format_report(usage_log.stats(3, now=_at(20)))

    assert "Nothing in these days." in report
    assert "Visits by country: no visits" in report
    assert "All time: 1 visitors, 0 visits, 1 questions" in report


def test_format_report_says_when_nothing_was_recorded(usage_log):
    report = usage.format_report(usage_log.stats(14))

    assert report == "No visits or questions have been recorded yet."


def test_main_prints_the_report(tmp_path, monkeypatch, capsys):
    path = tmp_path / "usage.db"
    log = UsageLog(path)
    log.record_question(ANA, "trains", 900)
    log.record_visit(ANA, "US", "mobile", None)
    log.record_visit(BEN, "US", "mobile", None)
    log.close()
    monkeypatch.setattr(usage, "load_dotenv", lambda: False)
    monkeypatch.setenv("USAGE_DB_PATH", str(path))
    monkeypatch.setattr("sys.argv", ["caltrain-bot-usage", "--days", "3"])

    usage.main()

    output = capsys.readouterr().out
    assert "Web page usage, last 3 days" in output
    assert "All time: 2 visitors, 2 visits, 1 questions" in output


def test_main_explains_a_missing_usage_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(usage, "load_dotenv", lambda: False)
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setattr("sys.argv", ["caltrain-bot-usage"])

    usage.main()

    assert "No usage recorded yet" in capsys.readouterr().out
    assert not (tmp_path / "missing.db").exists()
