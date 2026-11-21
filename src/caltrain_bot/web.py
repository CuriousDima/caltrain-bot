"""Mobile web frontend: one HTML page and one JSON endpoint."""

import base64
import hashlib
import re
import time
from collections import deque
from datetime import date, datetime
from pathlib import Path
from string import capwords
from typing import Any

from aiohttp import web
from loguru import logger

from caltrain_bot.answers import Answer, ScheduleService, answer_question
from caltrain_bot.question_analysis import CALTRAIN_TIMEZONE
from caltrain_bot.schedule import Train
from caltrain_bot.usage import (
    UsageLog,
    classify_device,
    normalize_country,
    normalize_referrer,
    normalize_visitor_id,
)

STATIC_DIR = Path(__file__).parent / "static"

MAX_QUESTION_LENGTH = 300
# Every question costs one paid LLM call, so cap how fast anyone can ask.
QUESTIONS_PER_MINUTE_PER_CLIENT = 12
QUESTIONS_PER_MINUTE_TOTAL = 120
# Every recorded visit is one disk write, so cap how fast anyone can add them.
VISITS_PER_MINUTE_PER_CLIENT = 30
VISITS_PER_MINUTE_TOTAL = 600

EMPTY_QUESTION_MESSAGE = "Type a question first."
QUESTION_TOO_LONG_MESSAGE = "That question is too long. Keep it short."
RATE_LIMITED_MESSAGE = "Too many questions right now. Try again in a minute."
NO_TRAINS_MESSAGE = "No trains leave around that time. Try a different time."
NO_NEXT_TRAINS_MESSAGE = "No trains leave in the next 24 hours."

_STYLESHEET_LINK = '<link rel="stylesheet" href="app.css">'
_INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)


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


def _csp_hash(source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def render_page() -> tuple[str, str]:
    """Return the page with its stylesheet inlined, plus a matching CSP.

    Everything ships in one response: a rider on a weak signal gets the whole
    page in a single round trip.
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    if _STYLESHEET_LINK not in html:
        raise ValueError(f"index.html must contain {_STYLESHEET_LINK}")
    html = html.replace(_STYLESHEET_LINK, f"<style>{css}</style>")
    scripts = " ".join(_csp_hash(script) for script in _INLINE_SCRIPT.findall(html))
    csp = "; ".join(
        (
            "default-src 'none'",
            f"style-src {_csp_hash(css)}",
            f"script-src {scripts}",
            "connect-src 'self'",
            "img-src data:",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        )
    )
    return html, csp


def _as_caltrain_time(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CALTRAIN_TIMEZONE)
    return parsed.astimezone(CALTRAIN_TIMEZONE)


def _format_clock(value: datetime) -> str:
    return f"{value.hour % 12 or 12}:{value:%M} {'AM' if value.hour < 12 else 'PM'}"


def _format_day(day: date, today: date) -> str:
    days_ahead = (day - today).days
    if days_ahead == 0:
        return "Today"
    if days_ahead == 1:
        return "Tomorrow"
    return f"{day:%a, %b} {day.day}"


def _train_to_json(train: Train) -> dict[str, Any]:
    departure = _as_caltrain_time(train.origin_departure_timestamp)
    arrival = _as_caltrain_time(train.destination_arrival_timestamp)
    return {
        "number": train.train_number,
        "service": train.service_pattern.removesuffix(" Weekday").removesuffix(
            " Weekend"
        ),
        "departs": _format_clock(departure),
        "arrives": _format_clock(arrival),
        "minutes": train.travel_minutes,
        # Lets the page count down ("in 7 min") whatever the phone's time zone.
        "departs_at": int(departure.timestamp()),
    }


def answer_to_json(answer: Answer, today: date) -> dict[str, Any]:
    query = None
    # A "next train" question: the trains are the next departures from now.
    is_next = False
    if answer.question is not None:
        is_next = not answer.question.departure_time_stated
        departure_time = _as_caltrain_time(answer.question.departure_time)
        clock = _format_clock(departure_time)
        day = _format_day(departure_time.date(), today)
        query = {
            "from": capwords(answer.question.departure_station),
            "to": capwords(answer.question.arrival_station),
            "when": f"Next trains from {clock}"
            if is_next
            else f"{day} · around {clock}",
            "next": is_next,
        }
    message = answer.message
    if answer.status == "trains" and not answer.trains:
        message = NO_NEXT_TRAINS_MESSAGE if is_next else NO_TRAINS_MESSAGE
    return {
        "status": answer.status,
        "message": message,
        "query": query,
        "trains": [_train_to_json(train) for train in answer.trains],
    }


def _error(message: str, status: int) -> web.Response:
    return web.json_response(
        {"status": "error", "message": message, "query": None, "trains": []},
        status=status,
    )


def _client_key(request: web.Request) -> str:
    # Behind a reverse proxy the socket peer is the proxy; the address it saw
    # is the last X-Forwarded-For entry. A direct client can forge the header,
    # which is why there is a total limit as well.
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    return forwarded_for.rpartition(",")[2].strip() or request.remote or "unknown"


def _usage_status(answer: Answer) -> str:
    if answer.status == "trains" and not answer.trains:
        return "no_trains"
    return answer.status


def build_web_app(
    service: ScheduleService, usage_log: UsageLog | None = None
) -> web.Application:
    html, csp = render_page()
    page_headers = {
        "Content-Security-Policy": csp,
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-cache",
    }
    client_limiter = RateLimiter(QUESTIONS_PER_MINUTE_PER_CLIENT)
    total_limiter = RateLimiter(QUESTIONS_PER_MINUTE_TOTAL)

    async def index(request: web.Request) -> web.Response:
        response = web.Response(
            text=html, content_type="text/html", headers=page_headers
        )
        response.enable_compression()
        return response

    async def ask(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except ValueError:
            return _error(EMPTY_QUESTION_MESSAGE, 400)
        question = payload.get("question") if isinstance(payload, dict) else None
        if not isinstance(question, str) or not question.strip():
            return _error(EMPTY_QUESTION_MESSAGE, 400)
        question = " ".join(question.split())
        if len(question) > MAX_QUESTION_LENGTH:
            return _error(QUESTION_TOO_LONG_MESSAGE, 400)
        if not client_limiter.allow(_client_key(request)) or not total_limiter.allow(
            "all"
        ):
            return _error(RATE_LIMITED_MESSAGE, 429)

        started = time.monotonic()
        answer = await answer_question(question, service)
        if usage_log is not None:
            usage_log.record_question(
                visitor=normalize_visitor_id(request.headers.get("X-Visitor-Id")),
                status=_usage_status(answer),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        today = datetime.now(CALTRAIN_TIMEZONE).date()
        return web.json_response(answer_to_json(answer, today))

    visit_client_limiter = RateLimiter(VISITS_PER_MINUTE_PER_CLIENT)
    visit_total_limiter = RateLimiter(VISITS_PER_MINUTE_TOTAL)

    async def visit(request: web.Request) -> web.Response:
        """Count one page view. Always 204: the page ignores the answer."""
        no_content = web.Response(status=204)
        if (
            usage_log is None
            or not visit_client_limiter.allow(_client_key(request))
            or not visit_total_limiter.allow("all")
        ):
            return no_content
        try:
            payload = await request.json()
        except (ValueError, web.HTTPRequestEntityTooLarge):
            return no_content
        if not isinstance(payload, dict):
            return no_content
        usage_log.record_visit(
            visitor=normalize_visitor_id(request.headers.get("X-Visitor-Id")),
            country=normalize_country(request.headers.get("CF-IPCountry")),
            device=classify_device(request.headers.get("User-Agent")),
            referrer=normalize_referrer(payload.get("referrer"), request.host),
        )
        return no_content

    app = web.Application(client_max_size=8 * 1024)
    app.router.add_get("/", index)
    app.router.add_post("/api/ask", ask)
    app.router.add_post("/api/visit", visit)
    return app


async def start_web_server(app: web.Application, port: int) -> web.AppRunner:
    """Serve ``app`` on the running event loop, next to the Telegram bot."""
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, port=port).start()
    logger.info(f"Web interface listening on port {port}")
    return runner
