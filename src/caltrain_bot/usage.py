"""Usage statistics for the web page: visits and questions, per day.

Stores one row per page visit and one per answered question in a small SQLite
file. It holds no question text, no IP address, no full URL, and no User-Agent
string; a visitor is a random ID the page generates and keeps in the browser.
"""

import argparse
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from loguru import logger

from caltrain_bot.config import get_usage_db_path

_CALTRAIN_TIMEZONE = ZoneInfo("America/Los_Angeles")
_VISITOR_ID = re.compile(r"[0-9a-f]{32}")
_COUNTRY = re.compile(r"[A-Za-z]{2}")
# Cloudflare sends XX when it does not know the country.
_UNKNOWN_COUNTRIES = frozenset({"XX"})
_HOSTNAME_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"{_HOSTNAME_LABEL}(?:\.{_HOSTNAME_LABEL})+")
_PORT = re.compile(r":\d+$")
MAX_REFERRER_LENGTH = 100
# Only the start of a User-Agent is looked at, so a huge one costs nothing.
_USER_AGENT_PREFIX = 400
# Real browsers do not put a URL in their User-Agent; crawlers usually do.
_AUTOMATED_AGENT = re.compile(r"bot|crawler|spider|headless|https?:", re.IGNORECASE)
_MOBILE_AGENT = re.compile(r"Mobi|Android|iPhone|iPad|iPod")
_DESKTOP_AGENT = re.compile(r"Windows NT|Macintosh|X11|CrOS|Linux")

Device = Literal["mobile", "desktop", "other"]

# Tables are only ever added, with IF NOT EXISTS, so a database written by an
# older version keeps working and keeps its rows.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_questions (
    asked_at INTEGER NOT NULL,  -- Unix seconds
    day TEXT NOT NULL,          -- calendar date in Caltrain local time
    visitor TEXT,               -- random browser ID; NULL when the page sent none
    status TEXT NOT NULL,       -- trains, no_trains, unsupported, feed_expired, error
    duration_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_questions_day ON web_questions (day);
CREATE TABLE IF NOT EXISTS web_visits (
    visited_at INTEGER NOT NULL,  -- Unix seconds
    day TEXT NOT NULL,            -- calendar date in Caltrain local time
    visitor TEXT,                 -- random browser ID; NULL when the page sent none
    country TEXT,                 -- two-letter code from Cloudflare; NULL if unknown
    device TEXT NOT NULL,         -- mobile, desktop, other
    referrer TEXT                 -- host name of the referring site; NULL if none
);
CREATE INDEX IF NOT EXISTS idx_web_visits_day ON web_visits (day);
"""


@dataclass(frozen=True)
class DailyUsage:
    day: str
    visits: int
    # Distinct visitor IDs that opened the page or asked a question that day.
    visitors: int
    # Visitors never seen on an earlier day, in visits or in questions.
    new_visitors: int
    questions: int
    with_trains: int
    no_trains: int
    unsupported: int
    failed: int
    # None on a day without questions.
    average_ms: int | None


@dataclass(frozen=True)
class UsageTotals:
    visitors: int
    visits: int
    questions: int
    visitors_last_7_days: int
    visitors_last_30_days: int


@dataclass(frozen=True)
class VisitCount:
    """Visits that share one country, referring site, or kind of device."""

    # None: the country is unknown, or no other site referred the visitor.
    name: str | None
    visits: int
    visitors: int


@dataclass(frozen=True)
class UsageStats:
    """Everything the report and the statistics page show for one window."""

    days: int
    totals: UsageTotals
    daily: list[DailyUsage]
    countries: list[VisitCount]
    referrers: list[VisitCount]
    devices: list[VisitCount]


def normalize_visitor_id(value: str | None) -> str | None:
    """Accept only the ID format the page generates; anything else is anonymous."""
    if value is not None and _VISITOR_ID.fullmatch(value):
        return value
    return None


def normalize_country(value: str | None) -> str | None:
    """Accept a two-letter country code, as Cloudflare's CF-IPCountry sends it."""
    if value is None or not _COUNTRY.fullmatch(value):
        return None
    country = value.upper()
    return None if country in _UNKNOWN_COUNTRIES else country


def normalize_referrer(value: object, own_host: str | None = None) -> str | None:
    """Accept a lowercase host name; the page's own host is not a referrer."""
    if (
        not isinstance(value, str)
        or len(value) > MAX_REFERRER_LENGTH
        or not _HOSTNAME.fullmatch(value)
    ):
        return None
    if own_host is not None and value == _PORT.sub("", own_host.lower()):
        return None
    return value


def classify_device(user_agent: str | None) -> Device:
    """Sort a User-Agent into a coarse class; the string itself is not kept."""
    agent = (user_agent or "")[:_USER_AGENT_PREFIX]
    if not agent or _AUTOMATED_AGENT.search(agent):
        return "other"
    if _MOBILE_AGENT.search(agent):
        return "mobile"
    if _DESKTOP_AGENT.search(agent):
        return "desktop"
    return "other"


def _local(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(_CALTRAIN_TIMEZONE)
    return now.astimezone(_CALTRAIN_TIMEZONE)


def _window_start(days: int, now: datetime | None) -> str:
    """First day of the ``days`` calendar days that end today."""
    return (_local(now).date() - timedelta(days=days - 1)).isoformat()


class UsageLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        # WAL lets the report command read while the server writes, and with
        # synchronous=NORMAL a write does not wait for the disk.
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def record_question(
        self,
        visitor: str | None,
        status: str,
        duration_ms: int,
        now: datetime | None = None,
    ) -> None:
        """Record one answered question. Never raises: statistics must not break answers."""
        now = _local(now)
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO web_questions VALUES (?, ?, ?, ?, ?)",
                    (
                        int(now.timestamp()),
                        now.date().isoformat(),
                        visitor,
                        status,
                        duration_ms,
                    ),
                )
        except sqlite3.Error:
            logger.exception("Could not record web usage.")

    def record_visit(
        self,
        visitor: str | None,
        country: str | None,
        device: Device,
        referrer: str | None,
        now: datetime | None = None,
    ) -> None:
        """Record one page view. Never raises: statistics must not break the page."""
        now = _local(now)
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO web_visits VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        int(now.timestamp()),
                        now.date().isoformat(),
                        visitor,
                        country,
                        device,
                        referrer,
                    ),
                )
        except sqlite3.Error:
            logger.exception("Could not record a web visit.")

    def daily(self, days: int, now: datetime | None = None) -> list[DailyUsage]:
        """Each of the last ``days`` calendar days that had any use, newest first."""
        rows = self._connection.execute(
            """
            WITH seen AS (
                -- UNION leaves one row per visitor and day, whichever table
                -- they appear in: viewing and asking count the same.
                SELECT day, visitor FROM web_visits WHERE visitor IS NOT NULL
                UNION
                SELECT day, visitor FROM web_questions WHERE visitor IS NOT NULL
            ),
            visitor_days AS (
                SELECT day, COUNT(*) AS visitors FROM seen GROUP BY day
            ),
            new_days AS (
                SELECT first_day AS day, COUNT(*) AS new_visitors
                FROM (SELECT MIN(day) AS first_day FROM seen GROUP BY visitor)
                GROUP BY first_day
            ),
            visit_days AS (
                SELECT day, COUNT(*) AS visits FROM web_visits GROUP BY day
            ),
            question_days AS (
                SELECT
                    day,
                    COUNT(*) AS questions,
                    SUM(status = 'trains') AS with_trains,
                    SUM(status = 'no_trains') AS no_trains,
                    SUM(status = 'unsupported') AS unsupported,
                    SUM(status IN ('error', 'feed_expired')) AS failed,
                    CAST(ROUND(AVG(duration_ms)) AS INTEGER) AS average_ms
                FROM web_questions
                GROUP BY day
            ),
            days AS (
                SELECT day FROM visit_days UNION SELECT day FROM question_days
            )
            SELECT
                days.day,
                COALESCE(visits, 0),
                COALESCE(visitors, 0),
                COALESCE(new_visitors, 0),
                COALESCE(questions, 0),
                COALESCE(with_trains, 0),
                COALESCE(no_trains, 0),
                COALESCE(unsupported, 0),
                COALESCE(failed, 0),
                average_ms
            FROM days
            LEFT JOIN visit_days ON visit_days.day = days.day
            LEFT JOIN visitor_days ON visitor_days.day = days.day
            LEFT JOIN new_days ON new_days.day = days.day
            LEFT JOIN question_days ON question_days.day = days.day
            WHERE days.day >= ?
            ORDER BY days.day DESC
            """,
            (_window_start(days, now),),
        ).fetchall()
        return [DailyUsage(*row) for row in rows]

    def totals(self, now: datetime | None = None) -> UsageTotals:
        now_seconds = int(_local(now).timestamp())
        row = self._connection.execute(
            """
            WITH seen AS (
                SELECT visited_at AS seen_at, visitor
                FROM web_visits
                WHERE visitor IS NOT NULL
                UNION ALL
                SELECT asked_at, visitor FROM web_questions WHERE visitor IS NOT NULL
            )
            SELECT
                (SELECT COUNT(DISTINCT visitor) FROM seen),
                (SELECT COUNT(*) FROM web_visits),
                (SELECT COUNT(*) FROM web_questions),
                (SELECT COUNT(DISTINCT visitor) FROM seen WHERE seen_at > ?),
                (SELECT COUNT(DISTINCT visitor) FROM seen WHERE seen_at > ?)
            """,
            (now_seconds - 7 * 86400, now_seconds - 30 * 86400),
        ).fetchone()
        return UsageTotals(*row)

    def _visit_counts(
        self,
        column: Literal["country", "referrer", "device"],
        days: int,
        limit: int,
        now: datetime | None,
    ) -> list[VisitCount]:
        # ``column`` is one of the fixed names above, never request data.
        rows = self._connection.execute(
            f"""
            SELECT {column}, COUNT(*), COUNT(DISTINCT visitor)
            FROM web_visits
            WHERE day >= ?
            GROUP BY {column}
            ORDER BY COUNT(*) DESC, {column}
            LIMIT ?
            """,
            (_window_start(days, now), limit),
        ).fetchall()
        return [VisitCount(*row) for row in rows]

    def top_countries(
        self, days: int, limit: int = 10, now: datetime | None = None
    ) -> list[VisitCount]:
        """Countries with the most visits in the last ``days`` days."""
        return self._visit_counts("country", days, limit, now)

    def top_referrers(
        self, days: int, limit: int = 10, now: datetime | None = None
    ) -> list[VisitCount]:
        """Referring sites with the most visits in the last ``days`` days."""
        return self._visit_counts("referrer", days, limit, now)

    def devices(self, days: int, now: datetime | None = None) -> list[VisitCount]:
        """Visits in the last ``days`` days by kind of device."""
        return self._visit_counts("device", days, 10, now)

    def stats(self, days: int, now: datetime | None = None) -> UsageStats:
        return UsageStats(
            days=days,
            totals=self.totals(now),
            daily=self.daily(days, now),
            countries=self.top_countries(days, now=now),
            referrers=self.top_referrers(days, now=now),
            devices=self.devices(days, now),
        )


def open_usage_log(path: Path) -> UsageLog | None:
    """Open the usage log, or return ``None`` when the file cannot be used."""
    try:
        return UsageLog(path)
    except (OSError, sqlite3.Error):
        logger.exception(f"Web usage is not being recorded: cannot open {path}")
        return None


def _format_table(header: tuple[str, ...], rows: list[tuple[object, ...]]) -> list[str]:
    table = [header, *(tuple(str(value) for value in row) for row in rows)]
    widths = [max(len(row[column]) for row in table) for column in range(len(header))]
    return [
        "  ".join(
            cell.ljust(width) if column == 0 else cell.rjust(width)
            for column, (cell, width) in enumerate(zip(row, widths))
        )
        for row in table
    ]


def _format_visit_counts(label: str, counts: list[VisitCount], unnamed: str) -> str:
    listed = ", ".join(f"{count.name or unnamed} {count.visits}" for count in counts)
    return f"{label}: {listed or 'no visits'}"


def format_report(stats: UsageStats) -> str:
    totals = stats.totals
    if not totals.visits and not totals.questions:
        return "No visits or questions have been recorded yet."

    daily = _format_table(
        (
            "Day",
            "Visits",
            "Visitors",
            "New",
            "Questions",
            "Trains",
            "No trains",
            "Off-topic",
            "Failed",
            "Avg ms",
        ),
        [
            (
                usage.day,
                usage.visits,
                usage.visitors,
                usage.new_visitors,
                usage.questions,
                usage.with_trains,
                usage.no_trains,
                usage.unsupported,
                usage.failed,
                "-" if usage.average_ms is None else usage.average_ms,
            )
            for usage in stats.daily
        ],
    )
    return "\n".join(
        (
            f"Web page usage, last {stats.days} days (days in Caltrain local time)",
            "",
            *(daily if stats.daily else ("Nothing in these days.",)),
            "",
            _format_visit_counts("Visits by country", stats.countries, "unknown"),
            _format_visit_counts("Visits by referrer", stats.referrers, "none"),
            _format_visit_counts("Visits by device", stats.devices, "other"),
            "",
            f"All time: {totals.visitors} visitors, {totals.visits} visits, "
            f"{totals.questions} questions",
            f"Active visitors: {totals.visitors_last_7_days} in the last 7 days, "
            f"{totals.visitors_last_30_days} in the last 30 days",
        )
    )


def main() -> None:
    """Print the usage report: ``caltrain-bot-usage [--days N]``."""
    parser = argparse.ArgumentParser(description="Show how the web page is used.")
    parser.add_argument(
        "--days", type=int, default=14, help="how many days to list (default: 14)"
    )
    args = parser.parse_args()

    _ = load_dotenv()
    path = get_usage_db_path()
    if not path.exists():
        print(f"No usage recorded yet: {path} does not exist.")
        return
    usage_log = UsageLog(path)
    try:
        print(format_report(usage_log.stats(max(args.days, 1))))
    finally:
        usage_log.close()


if __name__ == "__main__":
    main()
