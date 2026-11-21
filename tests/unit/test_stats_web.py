import asyncio
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer

from caltrain_bot import stats_web
from caltrain_bot.usage import UsageLog

PACIFIC = ZoneInfo("America/Los_Angeles")
ANA = "a" * 32
BEN = "b" * 32
MALICIOUS = "<script>alert(1)</script>"
# Read the clock once, so a test that runs across midnight stays consistent.
NOW = datetime.now(PACIFIC)


@pytest.fixture
def usage_log(tmp_path):
    log = UsageLog(tmp_path / "usage.db")
    yield log
    log.close()


def _days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


def _request(usage_log, path="/", method="GET", **kwargs):
    """Send one request to the statistics app without opening a real port."""

    async def run():
        app = stats_web.build_stats_app(usage_log)
        async with TestClient(TestServer(app)) as client:
            response = await client.request(method, path, **kwargs)
            is_json = response.content_type == "application/json"
            body = await response.json() if is_json else await response.text()
            return response, body

    return asyncio.run(run())


def _fill(usage_log):
    usage_log.record_visit(ANA, "US", "mobile", "news.example", now=_days_ago(0))
    usage_log.record_visit(ANA, "US", "mobile", None, now=_days_ago(0))
    usage_log.record_visit(BEN, "CA", "desktop", None, now=_days_ago(3))
    usage_log.record_question(ANA, "trains", 1200, now=_days_ago(0))
    usage_log.record_question(BEN, "unsupported", 800, now=_days_ago(40))


def test_page_shows_totals_days_and_breakdowns(usage_log):
    _fill(usage_log)

    response, html = _request(usage_log)

    assert response.status == 200
    assert response.content_type == "text/html"
    today = _days_ago(0).date().isoformat()
    older = _days_ago(3).date().isoformat()
    assert html.index(today) < html.index(older)
    assert _days_ago(40).date().isoformat() not in html
    for expected in (
        "<dt>Visitors, all time</dt><dd>2</dd>",
        "<dt>Visits, all time</dt><dd>3</dd>",
        "<dt>Questions, all time</dt><dd>2</dd>",
        "<dt>Active visitors, 7 days</dt><dd>2</dd>",
        "Visits in the last 30 days",
        '<a href="?days=30" aria-current="page">30 days</a>',
        '<a href="?days=7">7 days</a>',
        "news.example",
        "Top countries",
        "Top referrers",
        "Devices",
        "desktop",
    ):
        assert expected in html
    # The page needs no JavaScript and loads nothing from elsewhere.
    assert "<script" not in html
    assert "http://" not in html and "https://" not in html


def test_page_escapes_values_that_came_from_clients(usage_log):
    # The web page validates these, but the statistics page must not rely on it.
    usage_log.record_visit(ANA, '"><img src=x>', "mobile", MALICIOUS)
    usage_log.record_visit(BEN, "US", "<b>tablet</b>", "a&b.example")  # type: ignore[arg-type]

    response, html = _request(usage_log)

    assert response.status == 200
    assert "<script" not in html
    assert "<img" not in html
    assert "<b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&quot;&gt;&lt;img src=x&gt;" in html
    assert "&lt;b&gt;tablet&lt;/b&gt;" in html
    assert "a&amp;b.example" in html


def test_page_works_on_an_empty_database(usage_log):
    response, html = _request(usage_log)

    assert response.status == 200
    assert "<dt>Visitors, all time</dt><dd>0</dd>" in html
    assert html.count("No visits in these days.") == 3
    assert "Nothing was recorded in these days." in html


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (None, 30),
        ("7", 7),
        ("1", 1),
        ("365", 365),
        ("0", 1),
        ("-5", 1),
        ("366", 365),
        ("99999", 365),
        ("", 30),
        ("abc", 30),
        ("1.5", 30),
        ("7; DROP TABLE web_visits", 30),
        ("9" * 5000, 30),
    ],
)
def test_parse_days_clamps_the_window(query, expected):
    assert stats_web.parse_days(query) == expected


def test_page_and_api_use_the_requested_days(usage_log):
    _fill(usage_log)

    _, html = _request(usage_log, "/?days=2")
    _, clamped = _request(usage_log, "/?days=100000")
    _, body = _request(usage_log, "/api/stats?days=2")
    _, invalid = _request(usage_log, "/api/stats?days=<script>")

    assert "Visits in the last 2 days" in html
    assert _days_ago(3).date().isoformat() not in html
    assert "<dt>Visitors, all time</dt><dd>2</dd>" in html
    assert "Visits in the last 365 days" in clamped
    assert '<a href="?days=365" aria-current="page">1 year</a>' in clamped
    assert body["days"] == 2
    assert [day["day"] for day in body["daily"]] == [_days_ago(0).date().isoformat()]
    assert invalid["days"] == 30


def test_api_returns_the_same_numbers_as_json(usage_log):
    _fill(usage_log)

    response, body = _request(usage_log, "/api/stats")

    assert response.status == 200
    assert set(body) == {
        "days",
        "totals",
        "daily",
        "countries",
        "referrers",
        "devices",
    }
    assert body["days"] == 30
    assert body["totals"] == {
        "visitors": 2,
        "visits": 3,
        "questions": 2,
        "visitors_last_7_days": 2,
        "visitors_last_30_days": 2,
    }
    assert body["daily"][0] == {
        "day": _days_ago(0).date().isoformat(),
        "visits": 2,
        "visitors": 1,
        "new_visitors": 1,
        "questions": 1,
        "with_trains": 1,
        "no_trains": 0,
        "unsupported": 0,
        "failed": 0,
        "average_ms": 1200,
    }
    # Ben first asked 40 days ago, so the visit 3 days ago was not his first.
    assert body["daily"][1]["new_visitors"] == 0
    assert body["daily"][1]["average_ms"] is None
    assert body["countries"] == [
        {"name": "US", "visits": 2, "visitors": 1},
        {"name": "CA", "visits": 1, "visitors": 1},
    ]
    assert body["referrers"] == [
        {"name": None, "visits": 2, "visitors": 2},
        {"name": "news.example", "visits": 1, "visitors": 1},
    ]
    assert body["devices"] == [
        {"name": "mobile", "visits": 2, "visitors": 1},
        {"name": "desktop", "visits": 1, "visitors": 1},
    ]


@pytest.mark.parametrize("path", ["/", "/api/stats", "/missing"])
def test_every_response_carries_the_security_headers(usage_log, path):
    response, body = _request(usage_log, path)

    assert response.status == (404 if path == "/missing" else 200)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Robots-Tag"] == "noindex"
    csp = response.headers["Content-Security-Policy"]
    assert csp.startswith("default-src 'none'; ")
    assert "unsafe-inline" not in csp
    assert "script-src" not in csp
    assert "frame-ancestors 'none'" in csp
    if path == "/":
        style = re.search(r"<style>(.*?)</style>", body, re.DOTALL)
        assert style and body.count("<style") == 1
        assert f"style-src {stats_web._csp_hash(style.group(1))};" in csp
        # A style attribute would be blocked by the policy above.
        assert "style=" not in body


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", ["/", "/api/stats"])
def test_there_are_no_write_endpoints(usage_log, method, path):
    response, _ = _request(usage_log, path, method=method, json={"days": 1})

    assert response.status == 405
    assert response.headers["Cache-Control"] == "no-store"
    assert usage_log.totals().visits == 0


def test_app_serves_nothing_but_the_page_and_the_api(usage_log):
    app = stats_web.build_stats_app(usage_log)

    routes = {
        (route.method, route.resource.canonical)
        for route in app.router.routes()
        if route.resource is not None
    }

    assert routes == {
        ("GET", "/"),
        ("HEAD", "/"),
        ("GET", "/api/stats"),
        ("HEAD", "/api/stats"),
    }


def test_start_stats_server_serves_the_app_and_reports_a_busy_port(usage_log):
    async def run():
        # Port 0 asks the system for any free local port.
        runner = await stats_web.start_stats_server(
            stats_web.build_stats_app(usage_log), 0
        )
        try:
            # With port 0 the IPv4 and IPv6 sockets get different ports.
            port = next(a[1] for a in runner.addresses if a[0] == "0.0.0.0")
            async with ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/api/stats") as reply:
                    body = await reply.json()
            with pytest.raises(OSError):
                await stats_web.start_stats_server(
                    stats_web.build_stats_app(usage_log), port
                )
            return body
        finally:
            await runner.cleanup()

    assert asyncio.run(run())["days"] == 30
