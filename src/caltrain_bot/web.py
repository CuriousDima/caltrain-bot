"""Mobile web frontend: one HTML page and one JSON endpoint."""

import asyncio
import base64
import hashlib
import ipaddress
import re
import time
from collections import deque
from collections.abc import MutableMapping
from datetime import date, datetime
from pathlib import Path
from string import capwords
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web
from aiohttp.typedefs import Handler
from loguru import logger

from caltrain_bot.answers import (
    ANALYSIS_FAILED_MESSAGE,
    Answer,
    ScheduleService,
    answer_question,
)
from caltrain_bot.question_analysis import CALTRAIN_TIMEZONE
from caltrain_bot.schedule import Train
from caltrain_bot.usage import UsageLog, normalize_visitor_id

STATIC_DIR = Path(__file__).parent / "static"

MAX_QUESTION_LENGTH = 300
# Every question costs one paid LLM call, so cap how fast anyone can ask.
QUESTIONS_PER_MINUTE_PER_CLIENT = 12
QUESTIONS_PER_MINUTE_TOTAL = 120
# The bill for a day stays bounded even when the abuse comes from many clients.
QUESTIONS_PER_DAY_TOTAL = 3000
# Questions waiting on the LLM at once; more are turned away, never queued.
MAX_CONCURRENT_QUESTIONS = 8
# The page stops waiting after 30 seconds, so a slower answer helps nobody.
ANSWER_TIMEOUT_SECONDS = 30

EMPTY_QUESTION_MESSAGE = "Type a question first."
QUESTION_TOO_LONG_MESSAGE = "That question is too long. Keep it short."
RATE_LIMITED_MESSAGE = "Too many questions right now. Try again in a minute."
DAILY_LIMIT_MESSAGE = (
    "This page has answered all the questions it can today. Try again tomorrow."
)
BUSY_MESSAGE = "Lots of riders are asking right now. Try again in a few seconds."
JSON_REQUIRED_MESSAGE = "Send the request as application/json."
CROSS_SITE_MESSAGE = "This request must come from the Caltrain page itself."
UNREADABLE_REQUEST_MESSAGE = "That request could not be read."
INTERNAL_ERROR_MESSAGE = "Something went wrong on our side. Please try again."

API_PATH_PREFIX = "/api/"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# What a browser sends for the page's own requests and for typed-in addresses.
_OWN_FETCH_SITES = frozenset({"same-origin", "none"})

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": ", ".join(
        f"{feature}=()"
        for feature in (
            "accelerometer",
            "autoplay",
            "bluetooth",
            "browsing-topics",
            "camera",
            "display-capture",
            "encrypted-media",
            "fullscreen",
            "geolocation",
            "gyroscope",
            "hid",
            "idle-detection",
            "magnetometer",
            "microphone",
            "midi",
            "payment",
            "picture-in-picture",
            "publickey-credentials-get",
            "screen-wake-lock",
            "serial",
            "usb",
            "xr-spatial-tracking",
        )
    ),
    # Browsers honor this only over HTTPS, which the proxy in front provides.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}
# For every response that is not the page: nothing in it may load or run.
_DEFAULT_CSP = "default-src 'none'; frame-ancestors 'none'"
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


class DailyLimiter:
    """Allows ``limit`` events per calendar day in Caltrain local time."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._day: date | None = None
        self._count = 0

    def allow(self, now: datetime | None = None) -> bool:
        if now is None:
            now = datetime.now(CALTRAIN_TIMEZONE)
        day = now.astimezone(CALTRAIN_TIMEZONE).date()
        if day != self._day:
            self._day = day
            self._count = 0
        if self._count >= self._limit:
            return False
        self._count += 1
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


def _error(
    message: str, status: int, headers: dict[str, str] | None = None
) -> web.Response:
    return web.json_response(
        {"status": "error", "message": message, "query": None, "trains": []},
        status=status,
        headers=headers,
    )


def _address_bucket(value: str | None) -> str | None:
    """The rate-limit bucket of an IP address, or None when it is not one."""
    if value is None:
        return None
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        # One home or phone is handed a whole /64, so a limit per address
        # would be bypassed by picking a new address for every request.
        return f"{ipaddress.IPv6Address(int(address) >> 64 << 64)}/64"
    return str(address)


def _client_key(request: web.Request) -> str:
    # In production only the Cloudflare Tunnel can reach this port, so the
    # socket peer is the tunnel and the visitor's address is whatever
    # Cloudflare's edge reports: CF-Connecting-IP, which the edge overwrites
    # on every request, or, behind another reverse proxy, the last
    # X-Forwarded-For entry (the one that proxy appended). Anyone who can
    # reach the port directly can forge both headers; the total limits bound
    # the cost in that case.
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    for candidate in (
        request.headers.get("CF-Connecting-IP"),
        forwarded_for.rpartition(",")[2],
        request.remote,
    ):
        bucket = _address_bucket(candidate)
        if bucket is not None:
            return bucket
    return request.remote or "unknown"


def _is_own_origin(origin: str, host: str) -> bool:
    # Only the host is compared: the proxy in front terminates HTTPS, so the
    # page's origin is https:// while this server sees plain http://.
    try:
        origin_host = urlsplit(origin).netloc
    except ValueError:
        return False
    return bool(host) and origin_host.lower() == host.lower()


def reject_cross_site_request(request: web.Request) -> web.Response | None:
    """Refuse an API call that did not come from the page this server serves.

    Other websites can make a visitor's browser send "simple" POSTs (a form or
    a ``text/plain`` fetch) without asking this server first. Browsers label
    such requests with ``Sec-Fetch-Site`` and ``Origin``, and may only send
    ``application/json`` across sites after a CORS preflight, which this
    server never approves. Clients that send neither header (curl, old
    browsers) are not riding on a visitor's browser and stay allowed.
    """
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site is not None and fetch_site.lower() not in _OWN_FETCH_SITES:
        return _error(CROSS_SITE_MESSAGE, 403)
    origin = request.headers.get("Origin")
    if origin is not None and not _is_own_origin(
        origin, request.headers.get("Host", "")
    ):
        return _error(CROSS_SITE_MESSAGE, 403)
    if request.content_type != "application/json":
        return _error(JSON_REQUIRED_MESSAGE, 415)
    return None


def _set_security_headers(
    request: web.Request, headers: MutableMapping[str, str]
) -> None:
    headers.update(_SECURITY_HEADERS)
    # The page brings its own policy that allows its inline style and script.
    headers.setdefault("Content-Security-Policy", _DEFAULT_CSP)
    if request.path.startswith(API_PATH_PREFIX):
        headers["Cache-Control"] = "no-store"
    else:
        headers.setdefault("Cache-Control", "no-store")


@web.middleware
async def security_headers_middleware(
    request: web.Request, handler: Handler
) -> web.StreamResponse:
    """Put the security headers on every response, error responses included."""
    try:
        response = await handler(request)
    except web.HTTPException as error:
        # aiohttp's own answers, such as 404, 405 and 413.
        _set_security_headers(request, error.headers)
        raise
    except web.RequestPayloadError:
        # The body could not be read, for example broken compression.
        response = _error(UNREADABLE_REQUEST_MESSAGE, 400)
    except Exception:
        # The details go to the log; the client learns nothing about them.
        logger.exception(
            "Unhandled error while serving {} {!r}", request.method, request.path
        )
        response = _error(INTERNAL_ERROR_MESSAGE, 500)
    _set_security_headers(request, response.headers)
    return response


@web.middleware
async def api_guard_middleware(
    request: web.Request, handler: Handler
) -> web.StreamResponse:
    """Apply ``reject_cross_site_request`` to every state-changing API route."""
    if request.method not in _SAFE_METHODS and request.path.startswith(API_PATH_PREFIX):
        rejection = reject_cross_site_request(request)
        if rejection is not None:
            return rejection
    return await handler(request)


# Every route gets these, in this order; a new route needs no code of its own.
MIDDLEWARES = (security_headers_middleware, api_guard_middleware)


async def _remove_server_header(
    request: web.BaseRequest, response: web.StreamResponse
) -> None:
    # aiohttp adds "Server: Python/3.x aiohttp/3.x", a gift to version scanners.
    response.headers.popall("Server", None)


def _usage_status(answer: Answer) -> str:
    if answer.status == "trains" and not answer.trains:
        return "no_trains"
    return answer.status


def build_web_app(
    service: ScheduleService, usage_log: UsageLog | None = None
) -> web.Application:
    html, csp = render_page()
    # security_headers_middleware adds the headers every response shares.
    page_headers = {
        "Content-Security-Policy": csp,
        "Cache-Control": "no-cache",
    }
    client_limiter = RateLimiter(QUESTIONS_PER_MINUTE_PER_CLIENT)
    total_limiter = RateLimiter(QUESTIONS_PER_MINUTE_TOTAL)
    daily_limiter = DailyLimiter(QUESTIONS_PER_DAY_TOTAL)
    llm_slots = ConcurrencyLimiter(MAX_CONCURRENT_QUESTIONS)

    async def index(request: web.Request) -> web.Response:
        response = web.Response(
            text=html, content_type="text/html", headers=page_headers
        )
        response.enable_compression()
        return response

    async def ask(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except (ValueError, RecursionError):
            # RecursionError: JSON nested thousands of levels deep.
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
        if not llm_slots.try_acquire():
            return _error(BUSY_MESSAGE, 503, headers={"Retry-After": "5"})

        started = time.monotonic()
        try:
            # Checked last, so the daily cap counts only questions that
            # really reach the LLM.
            if not daily_limiter.allow():
                return _error(DAILY_LIMIT_MESSAGE, 429)
            async with asyncio.timeout(ANSWER_TIMEOUT_SECONDS):
                answer = await answer_question(question, service)
        except TimeoutError:
            logger.warning("Answering a web question timed out.")
            answer = Answer(status="error", message=ANALYSIS_FAILED_MESSAGE)
        finally:
            llm_slots.release()
        if usage_log is not None:
            usage_log.record_question(
                visitor=normalize_visitor_id(request.headers.get("X-Visitor-Id")),
                status=_usage_status(answer),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        today = datetime.now(CALTRAIN_TIMEZONE).date()
        return web.json_response(answer_to_json(answer, today))

    app = web.Application(client_max_size=8 * 1024, middlewares=MIDDLEWARES)
    app.on_response_prepare.append(_remove_server_header)
    app.router.add_get("/", index)
    app.router.add_post("/api/ask", ask)
    return app


async def start_web_server(app: web.Application, port: int) -> web.AppRunner:
    """Serve ``app`` on the running event loop, next to the Telegram bot."""
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, port=port).start()
    logger.info(f"Web interface listening on port {port}")
    return runner
