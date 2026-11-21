"""Limits on paid questions, shared by the web page and the Telegram bot."""

import time
from collections import deque
from datetime import date, datetime

from caltrain_bot.question_analysis import CALTRAIN_TIMEZONE


class RateLimiter:
    """Allows ``limit`` events per key in any sliding ``window_seconds``."""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._events: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        if now is None:
            now = time.monotonic()
        cutoff = now - self._window_seconds
        # Forget idle keys so the table cannot grow without bound.
        for stale_key in [
            k for k, v in self._events.items() if not v or v[-1] <= cutoff
        ]:
            del self._events[stale_key]
        events = self._events.setdefault(key, deque())
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= self._limit:
            return False
        events.append(now)
        return True


class DailyLimiter:
    """Allows ``limit`` events per key per calendar day in Caltrain local time."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._day: date | None = None
        self._counts: dict[str, int] = {}

    def allow(self, key: str = "all", now: datetime | None = None) -> bool:
        if now is None:
            now = datetime.now(CALTRAIN_TIMEZONE)
        day = now.astimezone(CALTRAIN_TIMEZONE).date()
        if day != self._day:
            # Yesterday's keys go too, so the table cannot grow without bound.
            self._day = day
            self._counts = {}
        count = self._counts.get(key, 0)
        if count >= self._limit:
            return False
        self._counts[key] = count + 1
        return True


class ConcurrencyLimiter:
    """Admits ``limit`` callers at a time; the rest are refused, not queued."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0

    def try_acquire(self) -> bool:
        if self._active >= self._limit:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        self._active -= 1
