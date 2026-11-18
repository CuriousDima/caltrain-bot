import asyncio
import re
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer

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

    for kwargs, message in (
        ({"data": b"not json"}, web.EMPTY_QUESTION_MESSAGE),
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
