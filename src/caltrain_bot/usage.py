"""Usage statistics for the web page: who asked how many questions, per day.

Stores one row per answered question in a small SQLite file. It holds no
question text and no IP address; a visitor is a random ID the page generates
and keeps in the browser.
"""

import argparse
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from loguru import logger

from caltrain_bot.config import get_usage_db_path

_CALTRAIN_TIMEZONE = ZoneInfo("America/Los_Angeles")
_VISITOR_ID = re.compile(r"[0-9a-f]{32}")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_questions (
    asked_at INTEGER NOT NULL,  -- Unix seconds
    day TEXT NOT NULL,          -- calendar date in Caltrain local time
    visitor TEXT,               -- random browser ID; NULL when the page sent none
    status TEXT NOT NULL,       -- trains, no_trains, unsupported, feed_expired, error
    duration_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_web_questions_day ON web_questions (day);
"""


@dataclass(frozen=True)
class DailyUsage:
    day: str
    users: int
    new_users: int
    questions: int
    with_trains: int
    no_trains: int
    unsupported: int
    failed: int
    average_ms: int


@dataclass(frozen=True)
class UsageTotals:
    users: int
    questions: int
    users_last_7_days: int
    users_last_30_days: int


def normalize_visitor_id(value: str | None) -> str | None:
    """Accept only the ID format the page generates; anything else is anonymous."""
    if value is not None and _VISITOR_ID.fullmatch(value):
        return value
    return None


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
        if now is None:
            now = datetime.now(_CALTRAIN_TIMEZONE)
        day = now.astimezone(_CALTRAIN_TIMEZONE).date().isoformat()
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO web_questions VALUES (?, ?, ?, ?, ?)",
                    (int(now.timestamp()), day, visitor, status, duration_ms),
                )
        except sqlite3.Error:
            logger.exception("Could not record web usage.")

    def daily(self, days: int) -> list[DailyUsage]:
        """Usage for the most recent ``days`` days that had any, newest first."""
        rows = self._connection.execute(
            """
            WITH first_days AS (
                SELECT visitor, MIN(day) AS first_day
                FROM web_questions
                WHERE visitor IS NOT NULL
                GROUP BY visitor
            )
            SELECT
                day,
                COUNT(DISTINCT visitor),
                (SELECT COUNT(*) FROM first_days WHERE first_day = day),
                COUNT(*),
                SUM(status = 'trains'),
                SUM(status = 'no_trains'),
                SUM(status = 'unsupported'),
                SUM(status IN ('error', 'feed_expired')),
                CAST(ROUND(AVG(duration_ms)) AS INTEGER)
            FROM web_questions
            GROUP BY day
            ORDER BY day DESC
            LIMIT ?
            """,
            (days,),
        ).fetchall()
        return [DailyUsage(*row) for row in rows]

    def totals(self, now: datetime | None = None) -> UsageTotals:
        if now is None:
            now = datetime.now(_CALTRAIN_TIMEZONE)
        now_seconds = int(now.timestamp())
        row = self._connection.execute(
            """
            SELECT
                COUNT(DISTINCT visitor),
                COUNT(*),
                COUNT(DISTINCT CASE WHEN asked_at > ? THEN visitor END),
                COUNT(DISTINCT CASE WHEN asked_at > ? THEN visitor END)
            FROM web_questions
            """,
            (now_seconds - 7 * 86400, now_seconds - 30 * 86400),
        ).fetchone()
        return UsageTotals(*row)


def open_usage_log(path: Path) -> UsageLog | None:
    """Open the usage log, or return ``None`` when the file cannot be used."""
    try:
        return UsageLog(path)
    except (OSError, sqlite3.Error):
        logger.exception(f"Web usage is not being recorded: cannot open {path}")
        return None


def format_report(daily: list[DailyUsage], totals: UsageTotals) -> str:
    if not totals.questions:
        return "No questions have been asked on the web page yet."

    header = (
        "Day",
        "Users",
        "New",
        "Questions",
        "Trains",
        "No trains",
        "Off-topic",
        "Failed",
        "Avg ms",
    )
    rows = [
        (
            usage.day,
            usage.users,
            usage.new_users,
            usage.questions,
            usage.with_trains,
            usage.no_trains,
            usage.unsupported,
            usage.failed,
            usage.average_ms,
        )
        for usage in daily
    ]
    table = [header, *(tuple(str(value) for value in row) for row in rows)]
    widths = [max(len(row[column]) for row in table) for column in range(len(header))]
    lines = [
        "  ".join(
            cell.ljust(width) if column == 0 else cell.rjust(width)
            for column, (cell, width) in enumerate(zip(row, widths))
        )
        for row in table
    ]
    return "\n".join(
        (
            "Web page usage (days in Caltrain local time)",
            "",
            *lines,
            "",
            f"All time: {totals.users} users, {totals.questions} questions",
            f"Active users: {totals.users_last_7_days} in the last 7 days, "
            f"{totals.users_last_30_days} in the last 30 days",
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
        print(format_report(usage_log.daily(args.days), usage_log.totals()))
    finally:
        usage_log.close()


if __name__ == "__main__":
    main()
