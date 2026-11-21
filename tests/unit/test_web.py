import asyncio
import json
import re
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiohttp import web as aiohttp_web
from aiohttp.test_utils import TestClient, TestServer
from loguru import logger
from multidict import CIMultiDict

from caltrain_bot import web
from caltrain_bot.answers import (
    ANALYSIS_FAILED_MESSAGE,
    FEED_EXPIRED_MESSAGE,
    UNSUPPORTED_MESSAGE,
    ScheduleService,
)
from caltrain_bot.question_analysis import (
    CALTRAIN_TIMEZONE,
    ScheduleQuestion,
    UnsupportedQuestion,
)
from caltrain_bot.schedule import Train


def _train(departure: str, arrival: str, service_pattern: str = "Local Weekday"):
    return Train(
        train_number=123,
        service_pattern=service_pattern,
        origin_station_id="palo_alto",
        origin_station_name="Palo Alto Station",
        origin_station_query_name="palo alto",
        # SQLite hands timestamps back as text, not datetime objects.
        origin_departure_timestamp=departure,  # type: ignore[arg-type]
        destination_station_id="22nd_street",
        destination_station_name="22nd Street Station",
        destination_station_query_name="22nd street",
        destination_arrival_timestamp=arrival,  # type: ignore[arg-type]
        travel_minutes=53,
    )


def _service(
    analysis=None,
    trains=(),
    analysis_error: Exception | None = None,
    feed_end_date: date = date(2099, 1, 1),
):
    schedule_manager = MagicMock()
    schedule_manager.get_trains.return_value = list(trains)
    schedule_helper = MagicMock()
    schedule_helper.acall = AsyncMock(
        side_effect=analysis_error,
        return_value=analysis
        or ScheduleQuestion(
            departure_station="palo alto",
            arrival_station="22nd street",
            departure_time=datetime.now(CALTRAIN_TIMEZONE),
        ),
    )
    return ScheduleService(
        schedule_manager=schedule_manager,
        schedule_helper=schedule_helper,
        feed_end_date=feed_end_date,
    )


def _request(service, method="POST", path="/api/ask", usage_log=None, **kwargs):
    """Send one request to the app without opening a real network port."""

    async def run():
        app = web.build_web_app(service, usage_log)
        async with TestClient(TestServer(app)) as client:
            response = await client.request(method, path, **kwargs)
            is_json = response.content_type == "application/json"
            body = await response.json() if is_json else await response.text()
            return response, body

    return asyncio.run(run())


def _ask(service, question="Palo Alto to 22nd Street at 5pm"):
    return _request(service, json={"question": question})


def _session(app, scenario):
    """Run ``scenario(client)`` against one app, so limits carry over between requests."""

    async def run():
        async with TestClient(TestServer(app)) as client:
            return await scenario(client)

    return asyncio.run(run())


def _ask_statuses(service, header_sets):
    """Ask the same question once per set of headers; return the statuses and last body."""

    async def scenario(client):
        statuses = []
        for headers in header_sets:
            response = await client.post(
                "/api/ask", json={"question": "Palo Alto to SF"}, headers=headers
            )
            statuses.append(response.status)
        return statuses, await response.json()

    return _session(web.build_web_app(service), scenario)


def _client_key(headers, remote="10.0.0.2"):
    return web._client_key(
        SimpleNamespace(headers=CIMultiDict(headers), remote=remote)  # type: ignore[arg-type]
    )


def _error_body(message):
    return {"status": "error", "message": message, "query": None, "trains": []}


def test_page_is_served_in_one_response_with_a_matching_csp():
    response, html = _request(_service(), "GET", "/")

    assert response.status == 200
    assert response.content_type == "text/html"
    assert 'rel="stylesheet"' not in html
    assert "<script src" not in html
    style = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
    script = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    assert style and script
    csp = response.headers["Content-Security-Policy"]
    assert web._csp_hash(style.group(1)) in csp
    assert web._csp_hash(script.group(1)) in csp
    assert "unsafe-inline" not in csp


def test_stylesheet_is_rebuilt_after_class_changes():
    """Fails when index.html uses a class that app.css was not built with."""
    html = (web.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    css = (web.STATIC_DIR / "app.css").read_text(encoding="utf-8")
    selectors = css.replace("\\", "")
    used = {
        name for names in re.findall(r'class="([^"]*)"', html) for name in names.split()
    }

    missing = sorted(name for name in used if f".{name}" not in selectors)

    assert used
    assert not missing, f"Run `npm run build:css`; app.css lacks: {missing}"


def test_ask_returns_trains_formatted_for_riders():
    service = _service(
        analysis=ScheduleQuestion(
            departure_station="palo alto",
            arrival_station="22nd street",
            departure_time=datetime(2026, 8, 31, 17, 0, tzinfo=CALTRAIN_TIMEZONE),
        ),
        trains=[
            _train("2026-08-31 12:05:00", "2026-08-31 12:58:00"),
            _train("2026-08-31 23:40:00", "2026-09-01 00:33:00", "Express"),
        ],
    )

    response, body = _ask(service, "  Palo Alto   to 22nd Street at 5pm ")

    assert response.status == 200
    assert body["status"] == "trains"
    assert body["message"] is None
    assert body["query"] == {
        "from": "Palo Alto",
        "to": "22nd Street",
        "when": "Mon, Aug 31 · around 5:00 PM",
        "next": False,
    }
    assert body["trains"] == [
        {
            "number": 123,
            "service": "Local",
            "departs": "12:05 PM",
            "arrives": "12:58 PM",
            "minutes": 53,
            "departs_at": int(
                datetime(2026, 8, 31, 12, 5, tzinfo=CALTRAIN_TIMEZONE).timestamp()
            ),
        },
        {
            "number": 123,
            "service": "Express",
            "departs": "11:40 PM",
            "arrives": "12:33 AM",
            "minutes": 53,
            "departs_at": int(
                datetime(2026, 8, 31, 23, 40, tzinfo=CALTRAIN_TIMEZONE).timestamp()
            ),
        },
    ]
    service.schedule_helper.acall.assert_awaited_once()
    asked = service.schedule_helper.acall.await_args.kwargs["question"]
    assert asked == "Palo Alto to 22nd Street at 5pm"


def test_answer_to_json_labels_the_day_relative_to_today():
    def day_label(departure_day: date) -> str:
        answer = web.Answer(
            status="trains",
            question=ScheduleQuestion(
                departure_station="palo alto",
                arrival_station="san francisco",
                departure_time=datetime(
                    departure_day.year, departure_day.month, departure_day.day, 9
                ),
            ),
        )
        return web.answer_to_json(answer, today=date(2026, 8, 31))["query"]["when"]

    assert day_label(date(2026, 8, 31)) == "Today · around 9:00 AM"
    assert day_label(date(2026, 9, 1)) == "Tomorrow · around 9:00 AM"
    assert day_label(date(2026, 9, 5)) == "Sat, Sep 5 · around 9:00 AM"


def test_ask_answers_a_next_train_question_with_the_next_departures():
    asked_at = datetime(2026, 8, 31, 23, 50, 12, tzinfo=CALTRAIN_TIMEZONE)
    service = _service(
        analysis=ScheduleQuestion(
            departure_station="palo alto",
            arrival_station="22nd street",
            departure_time=asked_at,
            departure_time_stated=False,
        )
    )
    service.schedule_manager.get_next_trains.return_value = [
        _train("2026-09-01 05:01:00", "2026-09-01 05:54:00")
    ]

    _, body = _ask(service, "next train from Palo Alto to 22nd Street")

    assert body["query"]["when"] == "Next trains from 11:50 PM"
    assert body["query"]["next"] is True
    assert [train["departs"] for train in body["trains"]] == ["5:01 AM"]
    service.schedule_manager.get_next_trains.assert_called_once_with(
        departure_station_query_name="palo alto",
        arrival_station_query_name="22nd street",
        after=asked_at,
    )
    service.schedule_manager.get_trains.assert_not_called()


def test_ask_explains_when_no_trains_leave_soon():
    service = _service(
        analysis=ScheduleQuestion(
            departure_station="palo alto",
            arrival_station="22nd street",
            departure_time=datetime.now(CALTRAIN_TIMEZONE),
            departure_time_stated=False,
        )
    )
    service.schedule_manager.get_next_trains.return_value = []

    _, body = _ask(service)

    assert body["trains"] == []
    assert body["message"] == web.NO_NEXT_TRAINS_MESSAGE


def test_ask_explains_when_no_trains_match():
    response, body = _ask(_service(trains=[]))

    assert response.status == 200
    assert body["status"] == "trains"
    assert body["trains"] == []
    assert body["message"] == web.NO_TRAINS_MESSAGE
    assert body["query"]["from"] == "Palo Alto"


def test_ask_reports_unsupported_questions():
    service = _service(analysis=UnsupportedQuestion())

    response, body = _ask(service, "How much is a ticket?")

    assert response.status == 200
    assert body["status"] == "unsupported"
    assert body["message"] == UNSUPPORTED_MESSAGE
    assert body["query"] is None
    service.schedule_manager.get_trains.assert_not_called()


def test_ask_apologizes_for_dates_after_feed_end():
    service = _service(feed_end_date=date(2020, 1, 1))

    _, body = _ask(service)

    assert body["status"] == "feed_expired"
    assert body["message"] == FEED_EXPIRED_MESSAGE
    service.schedule_manager.get_trains.assert_not_called()


def test_ask_reports_question_analysis_failures():
    _, body = _ask(_service(analysis_error=TimeoutError("LLM timed out")))

    assert body["status"] == "error"
    assert body["message"] == ANALYSIS_FAILED_MESSAGE


def test_ask_rejects_bad_input_without_calling_the_llm():
    service = _service()

    as_json = {"Content-Type": "application/json"}
    for kwargs, message in (
        ({"data": b"not json", "headers": as_json}, web.EMPTY_QUESTION_MESSAGE),
        ({"data": b"\xff\xfe", "headers": as_json}, web.EMPTY_QUESTION_MESSAGE),
        ({"data": b"[" * 5000, "headers": as_json}, web.EMPTY_QUESTION_MESSAGE),
        ({"json": ["Palo Alto to SF"]}, web.EMPTY_QUESTION_MESSAGE),
        ({"json": {"question": "   "}}, web.EMPTY_QUESTION_MESSAGE),
        ({"json": {"question": 5}}, web.EMPTY_QUESTION_MESSAGE),
        (
            {"json": {"question": "a" * (web.MAX_QUESTION_LENGTH + 1)}},
            web.QUESTION_TOO_LONG_MESSAGE,
        ),
    ):
        response, body = _request(service, **kwargs)
        assert response.status == 400
        assert body == {
            "status": "error",
            "message": message,
            "query": None,
            "trains": [],
        }
    service.schedule_helper.acall.assert_not_called()


def test_ask_limits_how_fast_one_client_can_ask(monkeypatch):
    monkeypatch.setattr(web, "QUESTIONS_PER_MINUTE_PER_CLIENT", 2)
    service = _service()

    async def run():
        async with TestClient(TestServer(web.build_web_app(service))) as client:
            statuses = []
            for forwarded_for in ("1.1.1.1", "9.9.9.9, 1.1.1.1", "1.1.1.1", "2.2.2.2"):
                response = await client.post(
                    "/api/ask",
                    json={"question": "Palo Alto to SF"},
                    headers={"X-Forwarded-For": forwarded_for},
                )
                statuses.append(response.status)
            return statuses, await response.json()

    statuses, _ = asyncio.run(run())

    assert statuses == [200, 200, 429, 200]
    assert service.schedule_helper.acall.await_count == 3


def test_rate_limiter_allows_events_again_after_the_window():
    limiter = web.RateLimiter(limit=2, window_seconds=60)

    assert limiter.allow("a", now=0)
    assert limiter.allow("a", now=1)
    assert not limiter.allow("a", now=59)
    assert limiter.allow("b", now=59)
    assert limiter.allow("a", now=60.5)
    assert not limiter.allow("a", now=60.9)


def test_client_key_trusts_cloudflare_over_forwarded_for():
    assert (
        _client_key({"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "1.2.3.4"})
        == "203.0.113.7"
    )
    # Without Cloudflare, the proxy's own entry is the last one; the client
    # controls everything before it.
    assert _client_key({"X-Forwarded-For": "1.2.3.4, 198.51.100.4"}) == "198.51.100.4"
    assert _client_key({}) == "10.0.0.2"
    assert _client_key({}, remote=None) == "unknown"


def test_client_key_buckets_ipv6_addresses_by_their_64_prefix():
    first = _client_key({"CF-Connecting-IP": "2001:db8:1:2:aaaa::1"})
    second = _client_key({"CF-Connecting-IP": "2001:DB8:1:2:bbbb:cccc:dddd:2"})
    neighbor = _client_key({"CF-Connecting-IP": "2001:db8:1:3::1"})

    assert first == second == "2001:db8:1:2::/64"
    assert neighbor == "2001:db8:1:3::/64"
    assert _client_key({"CF-Connecting-IP": "::ffff:203.0.113.7"}) == "203.0.113.7"
    assert _client_key({}, remote="fe80::1%eth0") == "fe80::/64"


def test_client_key_falls_back_to_the_socket_peer_for_unparseable_headers():
    for forged in (
        "",
        "unknown",
        "<script>",
        "1.2.3.4:5678",
        "1.2.3.4 evil",
        "a" * 500,
    ):
        assert _client_key({"CF-Connecting-IP": forged}) == "10.0.0.2"
        assert _client_key({"X-Forwarded-For": forged}) == "10.0.0.2"
        assert _client_key({"X-Forwarded-For": f"1.2.3.4, {forged}"}) == "10.0.0.2"
    # A garbled Cloudflare header does not hide a usable proxy entry.
    assert (
        _client_key({"CF-Connecting-IP": "nope", "X-Forwarded-For": "198.51.100.4"})
        == "198.51.100.4"
    )


def test_ask_limits_a_client_whatever_it_puts_in_forwarded_for(monkeypatch):
    monkeypatch.setattr(web, "QUESTIONS_PER_MINUTE_PER_CLIENT", 2)
    service = _service()

    statuses, body = _ask_statuses(
        service,
        [
            {"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "1.1.1.1"},
            {"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "2.2.2.2"},
            {"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "3.3.3.3"},
        ],
    )

    assert statuses == [200, 200, 429]
    assert body == _error_body(web.RATE_LIMITED_MESSAGE)
    assert service.schedule_helper.acall.await_count == 2


def test_ask_limits_an_ipv6_network_not_each_of_its_addresses(monkeypatch):
    monkeypatch.setattr(web, "QUESTIONS_PER_MINUTE_PER_CLIENT", 2)

    statuses, _ = _ask_statuses(
        _service(),
        [
            {"CF-Connecting-IP": "2001:db8:1:2::1"},
            {"CF-Connecting-IP": "2001:db8:1:2:ffff::9"},
            {"CF-Connecting-IP": "2001:db8:1:2:1:2:3:4"},
            {"CF-Connecting-IP": "2001:db8:1:3::1"},
        ],
    )

    assert statuses == [200, 200, 429, 200]


def test_ask_requires_a_json_content_type():
    service = _service()
    question = json.dumps({"question": "Palo Alto to SF"})

    # What a cross-site form or a no-preflight fetch is able to send.
    for content_type in (
        "text/plain",
        "application/x-www-form-urlencoded",
        "multipart/form-data; boundary=x",
        "text/json",
    ):
        response, body = _request(
            service, data=question, headers={"Content-Type": content_type}
        )
        assert response.status == 415
        assert body == _error_body(web.JSON_REQUIRED_MESSAGE)
    response, body = _request(
        service, data=question.encode(), skip_auto_headers=["Content-Type"]
    )
    assert "Content-Type" not in response.request_info.headers
    assert response.status == 415
    service.schedule_helper.acall.assert_not_called()

    response, _ = _request(
        service,
        data=question,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert response.status == 200
    service.schedule_helper.acall.assert_awaited_once()


def test_ask_rejects_requests_sent_by_other_websites():
    service = _service()

    for headers in (
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "CROSS-SITE", "Origin": "https://caltrain.help"},
        {"Origin": "https://evil.example"},
        {"Origin": "https://caltrain.help.evil.example"},
        {"Origin": "https://caltrain.help:8443"},
        {"Origin": "null"},
        {"Origin": ""},
        {"Origin": "https://[caltrain.help"},
    ):
        response, body = _request(
            service,
            json={"question": "Palo Alto to SF"},
            headers={"Host": "caltrain.help", **headers},
        )
        assert response.status == 403, headers
        assert body == _error_body(web.CROSS_SITE_MESSAGE)
    service.schedule_helper.acall.assert_not_called()


def test_ask_accepts_the_page_itself_and_clients_without_browser_headers():
    for headers in (
        {},
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "none"},
        # Cloudflare terminates HTTPS, so the scheme differs but the host matches.
        {"Sec-Fetch-Site": "same-origin", "Origin": "https://caltrain.help"},
        {"Origin": "https://CALTRAIN.help"},
    ):
        response, body = _request(
            _service(),
            json={"question": "Palo Alto to SF"},
            headers={"Host": "caltrain.help", **headers},
        )
        assert response.status == 200, headers
        assert body["status"] == "trains"

    response, _ = _request(
        _service(),
        json={"question": "Palo Alto to SF"},
        headers={"Host": "localhost:8080", "Origin": "http://localhost:8080"},
    )
    assert response.status == 200


def test_cross_site_guard_covers_every_post_route_under_api():
    calls = []

    async def future_route(request):
        calls.append(request.path)
        return aiohttp_web.json_response({"ok": True})

    async def scenario(client):
        async def post(path, **kwargs):
            response = await client.post(path, **kwargs)
            return response.status, response.headers.get("Cache-Control")

        return [
            await post(
                "/api/future", data="{}", headers={"Content-Type": "text/plain"}
            ),
            await post(
                "/api/future", json={}, headers={"Sec-Fetch-Site": "cross-site"}
            ),
            await post(
                "/api/future", json={}, headers={"Origin": "https://evil.example"}
            ),
            await post(
                "/api/missing", data="{}", headers={"Content-Type": "text/plain"}
            ),
            await post(
                "/api/future", json={}, headers={"Sec-Fetch-Site": "same-origin"}
            ),
        ]

    app = web.build_web_app(_service())
    app.router.add_post("/api/future", future_route)

    results = _session(app, scenario)

    assert [status for status, _ in results] == [415, 403, 403, 415, 200]
    assert {cache_control for _, cache_control in results} == {"no-store"}
    assert calls == ["/api/future"]


def test_daily_limiter_starts_over_at_midnight_in_caltrain_time():
    limiter = web.DailyLimiter(limit=2)
    late_evening = datetime(2026, 8, 31, 23, 59, tzinfo=CALTRAIN_TIMEZONE)

    assert limiter.allow(now=late_evening)
    assert limiter.allow(now=late_evening)
    assert not limiter.allow(now=late_evening)
    # Already September 1 in UTC, still August 31 on the platform.
    assert not limiter.allow(now=datetime(2026, 9, 1, 6, 59, tzinfo=timezone.utc))
    assert limiter.allow(now=datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc))
    assert limiter.allow(now=datetime(2026, 9, 1, 23, 0, tzinfo=CALTRAIN_TIMEZONE))
    assert not limiter.allow(now=datetime(2026, 9, 1, 23, 1, tzinfo=CALTRAIN_TIMEZONE))


def test_ask_stops_at_the_daily_limit_counting_only_llm_calls(monkeypatch):
    monkeypatch.setattr(web, "QUESTIONS_PER_DAY_TOTAL", 2)
    monkeypatch.setattr(web, "QUESTIONS_PER_MINUTE_PER_CLIENT", 1)
    service = _service()

    async def scenario(client):
        async def ask(question, client_ip):
            response = await client.post(
                "/api/ask",
                json={"question": question},
                headers={"CF-Connecting-IP": client_ip},
            )
            return response.status, (await response.json())["message"]

        return [
            await ask("   ", "198.51.100.1"),
            await ask("Palo Alto to SF", "198.51.100.1"),
            await ask("Palo Alto to SF", "198.51.100.1"),
            await ask("Palo Alto to SF", "198.51.100.2"),
            await ask("Palo Alto to SF", "198.51.100.3"),
            await ask("Palo Alto to SF", "198.51.100.4"),
        ]

    results = _session(web.build_web_app(service), scenario)

    assert results == [
        (400, web.EMPTY_QUESTION_MESSAGE),
        (200, web.NO_TRAINS_MESSAGE),
        (429, web.RATE_LIMITED_MESSAGE),
        (200, web.NO_TRAINS_MESSAGE),
        (429, web.DAILY_LIMIT_MESSAGE),
        (429, web.DAILY_LIMIT_MESSAGE),
    ]
    assert service.schedule_helper.acall.await_count == 2


def test_ask_stops_one_client_before_it_uses_up_the_whole_day(monkeypatch):
    monkeypatch.setattr(web, "QUESTIONS_PER_DAY_PER_CLIENT", 2)
    service = _service()

    async def scenario(client):
        async def ask(client_ip):
            response = await client.post(
                "/api/ask",
                json={"question": "Palo Alto to SF"},
                headers={"CF-Connecting-IP": client_ip},
            )
            return response.status, (await response.json())["message"]

        return [
            await ask("198.51.100.1"),
            await ask("198.51.100.1"),
            await ask("198.51.100.1"),
            await ask("198.51.100.2"),
        ]

    results = _session(web.build_web_app(service), scenario)

    assert results == [
        (200, web.NO_TRAINS_MESSAGE),
        (200, web.NO_TRAINS_MESSAGE),
        (429, web.CLIENT_DAILY_LIMIT_MESSAGE),
        (200, web.NO_TRAINS_MESSAGE),
    ]
    assert service.schedule_helper.acall.await_count == 3


def test_daily_limiter_counts_each_key_separately_and_forgets_them_overnight():
    limiter = web.DailyLimiter(limit=1)
    monday = datetime(2026, 8, 31, 12, 0, tzinfo=CALTRAIN_TIMEZONE)
    tuesday = datetime(2026, 9, 1, 12, 0, tzinfo=CALTRAIN_TIMEZONE)

    assert limiter.allow("a", now=monday)
    assert not limiter.allow("a", now=monday)
    assert limiter.allow("b", now=monday)
    assert limiter.allow("a", now=tuesday)
    assert limiter._counts == {"a": 1}


def test_visitors_arriving_over_plain_http_are_sent_to_https():
    async def scenario(client):
        async def get(path, headers):
            response = await client.get(path, headers=headers, allow_redirects=False)
            return response.status, response.headers.get("Location")

        plain = {"Host": "caltrain.help", "CF-Visitor": '{"scheme":"http"}'}
        post = await client.post(
            "/api/ask",
            json={"question": "Palo Alto to SF"},
            headers=plain,
            allow_redirects=False,
        )
        _assert_security_headers(post)
        return [
            await get("/?utm=1", plain),
            (post.status, post.headers.get("Location")),
            await get(
                "/", {"Host": "caltrain.help", "CF-Visitor": '{"scheme":"https"}'}
            ),
            # No Cloudflare in front (tailnet, local runs): nothing to upgrade to.
            await get("/", {"Host": "nest:8080"}),
            await get("/", {"Host": "nest:8080", "CF-Visitor": "not json"}),
            await get("/", {"Host": "nest:8080", "CF-Visitor": '"http"'}),
        ]

    service = _service()
    results = _session(web.build_web_app(service), scenario)

    assert results == [
        (308, "https://caltrain.help/?utm=1"),
        (308, "https://caltrain.help/api/ask"),
        (200, None),
        (200, None),
        (200, None),
        (200, None),
    ]
    service.schedule_helper.acall.assert_not_awaited()


def test_ask_turns_questions_away_while_too_many_are_in_flight(monkeypatch):
    monkeypatch.setattr(web, "MAX_CONCURRENT_QUESTIONS", 2)
    service = _service()

    async def scenario(client):
        in_flight = asyncio.Semaphore(0)
        finish = asyncio.Event()
        analysis = service.schedule_helper.acall.return_value

        async def slow_analysis(**kwargs):
            in_flight.release()
            await finish.wait()
            return analysis

        service.schedule_helper.acall.side_effect = slow_analysis

        def ask():
            return client.post("/api/ask", json={"question": "Palo Alto to SF"})

        waiting = [asyncio.create_task(ask()), asyncio.create_task(ask())]
        await in_flight.acquire()
        await in_flight.acquire()
        refused = await asyncio.wait_for(ask(), timeout=5)
        refused_body = await refused.json()
        finish.set()
        answered = [(await task).status for task in waiting]
        afterwards = await ask()
        return refused, refused_body, answered, afterwards.status

    refused, refused_body, answered, afterwards = _session(
        web.build_web_app(service), scenario
    )

    assert refused.status == 503
    assert refused.headers["Retry-After"] == "5"
    assert refused_body == _error_body(web.BUSY_MESSAGE)
    assert answered == [200, 200]
    assert afterwards == 200
    assert service.schedule_helper.acall.await_count == 3


def test_ask_gives_up_on_an_answer_the_page_no_longer_waits_for(monkeypatch):
    monkeypatch.setattr(web, "ANSWER_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(web, "MAX_CONCURRENT_QUESTIONS", 1)
    service = _service()
    usage_log = MagicMock()

    async def stuck_analysis(**kwargs):
        await asyncio.sleep(60)

    async def scenario(client):
        service.schedule_helper.acall.side_effect = stuck_analysis
        first = await client.post("/api/ask", json={"question": "Palo Alto to SF"})
        # The only slot is free again.
        second = await client.post("/api/ask", json={"question": "Palo Alto to SF"})
        return first.status, await first.json(), second.status

    status, body, second_status = _session(
        web.build_web_app(service, usage_log), scenario
    )

    assert status == second_status == 200
    assert body["status"] == "error"
    assert body["message"] == ANALYSIS_FAILED_MESSAGE
    assert usage_log.record_question.call_args.kwargs["status"] == "error"


def _assert_security_headers(response, cache_control="no-store"):
    headers = response.headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    for feature in ("geolocation", "camera", "microphone", "payment", "usb"):
        assert f"{feature}=()" in headers["Permissions-Policy"]
    assert headers["Cache-Control"] == cache_control
    assert "Content-Security-Policy" in headers
    assert "Server" not in headers


def test_page_keeps_its_own_csp_next_to_the_security_headers():
    response, _ = _request(_service(), "GET", "/")

    _assert_security_headers(response, cache_control="no-cache")
    csp = response.headers.getall("Content-Security-Policy")
    assert len(csp) == 1
    assert "script-src 'sha256-" in csp[0]
    assert "frame-ancestors 'none'" in csp[0]


def test_every_api_response_carries_the_security_headers():
    api_csp = "default-src 'none'; frame-ancestors 'none'"
    too_big = {"question": "a" * 10_000}

    for expected_status, kwargs in (
        (200, {"json": {"question": "Palo Alto to SF"}}),
        (400, {"json": {"question": ""}}),
        (403, {"json": {}, "headers": {"Sec-Fetch-Site": "cross-site"}}),
        (415, {"data": "{}", "headers": {"Content-Type": "text/plain"}}),
        (413, {"json": too_big}),
        (405, {"method": "GET"}),
        (405, {"method": "PUT", "json": {}}),
        (404, {"path": "/api/nope", "json": {}}),
    ):
        response, _ = _request(_service(), **kwargs)

        assert response.status == expected_status, kwargs
        _assert_security_headers(response)
        assert response.headers["Content-Security-Policy"] == api_csp


def test_unknown_pages_carry_the_security_headers():
    for method, path, expected_status in (
        ("GET", "/nope", 404),
        ("GET", "/app.css", 404),
        ("POST", "/", 405),
    ):
        response, _ = _request(_service(), method, path)

        assert response.status == expected_status
        _assert_security_headers(response)
        assert (
            response.headers["Content-Security-Policy"]
            == "default-src 'none'; frame-ancestors 'none'"
        )


def test_unreadable_bodies_are_bad_requests_not_server_errors():
    logged = []
    sink = logger.add(logged.append, level="ERROR")
    try:
        response, body = _request(
            _service(),
            data=b"not gzip at all",
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )
    finally:
        logger.remove(sink)

    assert response.status == 400
    assert body == _error_body(web.UNREADABLE_REQUEST_MESSAGE)
    _assert_security_headers(response)
    assert logged == []


def test_unexpected_errors_are_logged_and_never_shown_to_the_client():
    service = _service()
    service.schedule_manager.get_trains.side_effect = RuntimeError(
        "no such table: secret_internal_detail"
    )
    logged = []
    sink = logger.add(logged.append, level="ERROR")
    try:
        response, body = _ask(service)
    finally:
        logger.remove(sink)

    assert response.status == 500
    assert body == _error_body(web.INTERNAL_ERROR_MESSAGE)
    assert "secret_internal_detail" not in json.dumps(body)
    _assert_security_headers(response)
    assert len(logged) == 1
    assert "Unhandled error while serving POST '/api/ask'" in logged[0]
    assert "secret_internal_detail" in logged[0]


def test_ask_records_usage_without_the_question_text():
    usage_log = MagicMock()
    visitor = "0123456789abcdef0123456789abcdef"

    _request(
        _service(trains=[_train("2026-08-31 12:05:00", "2026-08-31 12:58:00")]),
        usage_log=usage_log,
        json={"question": "Palo Alto to 22nd Street"},
        headers={"X-Visitor-Id": visitor},
    )

    usage_log.record_question.assert_called_once()
    recorded = usage_log.record_question.call_args.kwargs
    assert recorded["visitor"] == visitor
    assert recorded["status"] == "trains"
    assert recorded["duration_ms"] >= 0
    assert set(recorded) == {"visitor", "status", "duration_ms"}


def test_ask_records_outcomes_and_ignores_malformed_visitor_ids():
    cases = (
        (_service(trains=[]), "no_trains"),
        (_service(analysis=UnsupportedQuestion()), "unsupported"),
        (_service(analysis_error=TimeoutError()), "error"),
    )
    for service, expected_status in cases:
        usage_log = MagicMock()

        _request(
            service,
            usage_log=usage_log,
            json={"question": "Palo Alto to SF"},
            headers={"X-Visitor-Id": "<script>"},
        )

        recorded = usage_log.record_question.call_args.kwargs
        assert recorded["status"] == expected_status
        assert recorded["visitor"] is None


def test_ask_does_not_record_rejected_requests():
    usage_log = MagicMock()

    _request(_service(), usage_log=usage_log, json={"question": "  "})

    usage_log.record_question.assert_not_called()


VISITOR = "0123456789abcdef0123456789abcdef"
IPHONE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/26.1 Mobile/15E148 Safari/604.1"
)


def _visit(usage_log, service=None, **kwargs):
    return _request(
        service or _service(), path="/api/visit", usage_log=usage_log, **kwargs
    )


def test_page_reports_the_visit_once_per_load():
    _, html = _request(_service(), "GET", "/")

    assert html.count('fetch("api/visit"') == 1
    # One definition and one call, outside any event handler.
    assert html.count("reportVisit()") == 2
    assert "new URL(document.referrer).hostname" in html


def test_visit_is_recorded_with_coarse_details_only():
    usage_log = MagicMock()
    service = _service()

    response, body = _visit(
        usage_log,
        service,
        json={"referrer": "news.ycombinator.com"},
        headers={
            "X-Visitor-Id": VISITOR,
            "CF-IPCountry": "us",
            "User-Agent": IPHONE_USER_AGENT,
            "X-Forwarded-For": "203.0.113.7",
        },
    )

    assert response.status == 204
    assert body == ""
    usage_log.record_visit.assert_called_once_with(
        visitor=VISITOR,
        country="US",
        device="mobile",
        referrer="news.ycombinator.com",
    )
    usage_log.record_question.assert_not_called()
    service.schedule_helper.acall.assert_not_called()


def test_visit_stores_null_for_anything_that_fails_validation():
    cases = (
        ({"referrer": "https://evil.example/<script>"}, {}),
        ({"referrer": "<script>alert(1)</script>"}, {"CF-IPCountry": "XX"}),
        ({"referrer": "News.Example"}, {"CF-IPCountry": "T1"}),
        ({"referrer": "a" * 97 + ".com"}, {"CF-IPCountry": "USA"}),
        ({"referrer": ["news.example"]}, {"CF-IPCountry": "<b"}),
        ({"referrer": ""}, {"X-Visitor-Id": "<script>"}),
        ({}, {"X-Visitor-Id": VISITOR.upper()}),
        # The page's own host is not a referring site.
        ({"referrer": "caltrain.help"}, {"Host": "caltrain.help"}),
        ({"referrer": "caltrain.help"}, {"Host": "Caltrain.Help:8080"}),
    )
    for payload, headers in cases:
        usage_log = MagicMock()

        response, _ = _visit(usage_log, json=payload, headers=headers)

        assert response.status == 204
        # aiohttp's test client names itself, which is not a phone or a desktop.
        usage_log.record_visit.assert_called_once_with(
            visitor=None, country=None, device="other", referrer=None
        )


def test_visit_always_answers_204_and_ignores_malformed_bodies():
    json_type = {"Content-Type": "application/json"}
    cases = (
        {"data": "not json", "headers": json_type},
        {"data": "", "headers": json_type},
        {"data": '["news.example"]', "headers": json_type},
        {"data": '"news.example"', "headers": json_type},
        {"data": b"\xff\xfe", "headers": json_type},
        {"data": '{"referrer": "' + "a" * 10_000 + '"}', "headers": json_type},
    )
    for kwargs in cases:
        usage_log = MagicMock()

        response, body = _visit(usage_log, **kwargs)

        assert (response.status, body) == (204, "")
        usage_log.record_visit.assert_not_called()


def test_visit_answers_204_without_a_usage_log():
    service = _service()

    response, body = _visit(None, service, json={"referrer": "news.example"})

    assert (response.status, body) == (204, "")
    service.schedule_helper.acall.assert_not_called()


def test_visit_reaches_the_usage_database(tmp_path):
    usage_log = web.UsageLog(tmp_path / "usage.db")
    try:
        _visit(
            usage_log,
            json={"referrer": "news.example"},
            headers={"X-Visitor-Id": VISITOR, "CF-IPCountry": "CA"},
        )
        stats = usage_log.stats(days=1)
    finally:
        usage_log.close()

    assert (stats.totals.visits, stats.totals.visitors) == (1, 1)
    assert [(count.name, count.visits) for count in stats.countries] == [("CA", 1)]
    assert [(count.name, count.visits) for count in stats.referrers] == [
        ("news.example", 1)
    ]


def _visit_many(usage_log, forwarded_for):
    async def run():
        app = web.build_web_app(_service(), usage_log)
        async with TestClient(TestServer(app)) as client:
            statuses = []
            for address in forwarded_for:
                response = await client.post(
                    "/api/visit",
                    json={"referrer": ""},
                    headers={"X-Forwarded-For": address},
                )
                statuses.append(response.status)
            return statuses

    return asyncio.run(run())


def test_visit_limits_how_fast_one_client_can_add_rows(monkeypatch):
    monkeypatch.setattr(web, "VISITS_PER_MINUTE_PER_CLIENT", 2)
    usage_log = MagicMock()

    statuses = _visit_many(
        usage_log, ("1.1.1.1", "9.9.9.9, 1.1.1.1", "1.1.1.1", "2.2.2.2", "1.1.1.1")
    )

    # A limited client is not told so; its visits are just not written.
    assert statuses == [204] * 5
    assert usage_log.record_visit.call_count == 3


def test_visit_limits_how_fast_everyone_together_can_add_rows(monkeypatch):
    monkeypatch.setattr(web, "VISITS_PER_MINUTE_TOTAL", 3)
    usage_log = MagicMock()

    statuses = _visit_many(usage_log, [f"10.0.0.{n}" for n in range(1, 7)])

    assert statuses == [204] * 6
    assert usage_log.record_visit.call_count == 3


def test_visit_limit_is_separate_from_the_question_limit(monkeypatch):
    monkeypatch.setattr(web, "VISITS_PER_MINUTE_PER_CLIENT", 1)
    service = _service()

    async def run():
        async with TestClient(TestServer(web.build_web_app(service))) as client:
            for _ in range(3):
                await client.post("/api/visit", json={"referrer": ""})
            response = await client.post(
                "/api/ask", json={"question": "Palo Alto to SF"}
            )
            return response.status

    assert asyncio.run(run()) == 200


def test_public_app_serves_no_statistics(tmp_path):
    usage_log = web.UsageLog(tmp_path / "usage.db")
    usage_log.record_visit(VISITOR, "US", "mobile", "secret-referrer.example")
    try:
        app = web.build_web_app(_service(), usage_log)
        paths = {
            route.resource.canonical
            for route in app.router.routes()
            if route.resource is not None
        }
        assert not [path for path in paths if "stat" in path]

        for path in ("/api/stats", "/stats", "/api/visit", "/api/visits", "/usage"):
            response, body = _request(_service(), "GET", path, usage_log=usage_log)
            assert response.status in (404, 405)
            assert "secret-referrer.example" not in str(body)

        response, html = _request(_service(), "GET", "/?days=7", usage_log=usage_log)
        assert response.status == 200
        assert "secret-referrer.example" not in html
        assert "statistics" not in html.lower()
    finally:
        usage_log.close()
