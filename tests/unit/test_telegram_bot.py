import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from telegram.constants import ChatAction

from caltrain_bot import telegram_bot
from caltrain_bot.answers import ScheduleService
from caltrain_bot.limits import ConcurrencyLimiter, DailyLimiter, RateLimiter
from caltrain_bot.question_analysis import ScheduleQuestion
from caltrain_bot.telegram_bot import (
    QuestionLimits,
    format_info_message,
    format_start_message,
    get_trains_info,
)


def test_format_start_message_includes_examples_and_info_prompt():
    message = format_start_message("Dima & Friends")

    assert "Hello Dima &amp; Friends!" in message
    assert "San Francisco to Palo Alto after 7pm" in message
    assert "Next train from Mountain View to San Jose Diridon" in message
    assert "Need a train from Millbrae to 22nd Street around 8:30" in message
    assert "Send <code>/info</code>" in message


def test_format_info_message_includes_capabilities_and_contributing():
    message = format_info_message()

    assert "scheduled Caltrain trips between stations" in message
    assert "What is the next train from Sunnyvale to Millbrae?" in message
    assert "not live delay or service alert feeds" in message
    assert "Contributing: https://github.com/CuriousDima/caltrain-bot" in message


def _update(message, user_id: int | None = 1, chat_id: int | None = 100):
    return SimpleNamespace(
        message=message,
        effective_user=None if user_id is None else SimpleNamespace(id=user_id),
        effective_chat=None if chat_id is None else SimpleNamespace(id=chat_id),
    )


def _run_get_trains_info(
    departure_time: datetime | None = None,
    feed_end_date: date = date(2026, 8, 31),
    analysis_error: Exception | None = None,
    typing_error: Exception | None = None,
):
    message = SimpleNamespace(
        text="Palo Alto to SF",
        reply_text=AsyncMock(),
        reply_chat_action=AsyncMock(side_effect=typing_error),
    )
    update = _update(message)
    schedule_manager = MagicMock()
    schedule_manager.get_trains.return_value = []
    schedule_helper = MagicMock()
    schedule_helper.acall = AsyncMock(
        side_effect=analysis_error,
        return_value=ScheduleQuestion(
            departure_station="palo alto",
            arrival_station="san francisco",
            departure_time=departure_time or datetime(2026, 8, 31, 8, 0),
        ),
    )
    asyncio.run(
        get_trains_info(
            update,  # type: ignore[arg-type]
            MagicMock(),
            service=ScheduleService(
                schedule_manager=schedule_manager,
                schedule_helper=schedule_helper,
                feed_end_date=feed_end_date,
            ),
            limits=QuestionLimits(),
        )
    )
    replies = [call.args[0] for call in message.reply_text.call_args_list]
    return replies, schedule_manager, message


def test_get_trains_info_apologizes_for_dates_after_feed_end():
    replies, schedule_manager, _ = _run_get_trains_info(
        datetime(2026, 9, 1, 8, 0), feed_end_date=date(2026, 8, 31)
    )

    assert "sincerely apologize" in replies[-1]
    schedule_manager.get_trains.assert_not_called()


def test_get_trains_info_queries_schedule_within_feed_dates():
    replies, schedule_manager, _ = _run_get_trains_info(
        datetime(2026, 8, 31, 8, 0), feed_end_date=date(2026, 8, 31)
    )

    assert replies == ["No matching trains found."]
    schedule_manager.get_trains.assert_called_once()


def test_get_trains_info_looks_up_the_next_trains_when_no_time_is_stated():
    asked_at = datetime(2026, 8, 31, 8, 0)
    message = SimpleNamespace(
        text="Next train from Palo Alto to SF",
        reply_text=AsyncMock(),
        reply_chat_action=AsyncMock(),
    )
    schedule_manager = MagicMock()
    schedule_manager.get_next_trains.return_value = []
    schedule_helper = MagicMock()
    schedule_helper.acall = AsyncMock(
        return_value=ScheduleQuestion(
            departure_station="palo alto",
            arrival_station="san francisco",
            departure_time=asked_at,
            departure_time_stated=False,
        )
    )

    asyncio.run(
        get_trains_info(
            _update(message),  # type: ignore[arg-type]
            MagicMock(),
            service=ScheduleService(
                schedule_manager=schedule_manager,
                schedule_helper=schedule_helper,
                feed_end_date=date(2026, 8, 31),
            ),
            limits=QuestionLimits(),
        )
    )

    schedule_manager.get_next_trains.assert_called_once_with(
        departure_station_query_name="palo alto",
        arrival_station_query_name="san francisco",
        after=asked_at,
    )
    schedule_manager.get_trains.assert_not_called()
    message.reply_text.assert_awaited_once_with(
        "No matching trains found.", parse_mode="HTML"
    )


def test_get_trains_info_shows_typing_instead_of_a_waiting_message():
    replies, _, message = _run_get_trains_info()

    assert replies == ["No matching trains found."]
    message.reply_chat_action.assert_awaited_once_with(ChatAction.TYPING)


def test_get_trains_info_answers_even_if_the_typing_indicator_fails():
    replies, _, _ = _run_get_trains_info(typing_error=RuntimeError("telegram down"))

    assert replies == ["No matching trains found."]


def test_get_trains_info_replies_when_question_analysis_fails():
    replies, schedule_manager, _ = _run_get_trains_info(
        analysis_error=TimeoutError("LLM timed out")
    )

    assert len(replies) == 1
    assert "could not process your question" in replies[0]
    schedule_manager.get_trains.assert_not_called()


def _ask(limits: QuestionLimits, text: str = "Palo Alto to SF", **sender) -> tuple:
    """Send one question through the handler; return (reply, LLM call count).

    The reply is None when the bot stayed silent.
    """
    message = SimpleNamespace(
        text=text, reply_text=AsyncMock(), reply_chat_action=AsyncMock()
    )
    schedule_manager = MagicMock()
    schedule_manager.get_trains.return_value = []
    schedule_helper = MagicMock()
    schedule_helper.acall = AsyncMock(
        return_value=ScheduleQuestion(
            departure_station="palo alto",
            arrival_station="san francisco",
            departure_time=datetime(2026, 8, 31, 8, 0),
        )
    )
    asyncio.run(
        get_trains_info(
            _update(message, **sender),  # type: ignore[arg-type]
            MagicMock(),
            service=ScheduleService(
                schedule_manager=schedule_manager,
                schedule_helper=schedule_helper,
                feed_end_date=date(2026, 8, 31),
            ),
            limits=limits,
        )
    )
    assert message.reply_text.await_count <= 1
    reply = message.reply_text.call_args.args[0] if message.reply_text.called else None
    return reply, schedule_helper.acall.await_count


def test_get_trains_info_refuses_an_overlong_question_without_an_llm_call():
    limits = QuestionLimits()
    too_long = "x" * (telegram_bot.MAX_QUESTION_LENGTH + 1)

    assert _ask(limits, too_long) == (telegram_bot.QUESTION_TOO_LONG_MESSAGE, 0)
    # Runs of whitespace do not count toward the length.
    assert _ask(limits, "Palo Alto to SF" + " " * 500 + "at 8am") == (
        "No matching trains found.",
        1,
    )


def test_get_trains_info_limits_how_fast_one_user_can_ask():
    limits = QuestionLimits(user_minute=RateLimiter(2))

    assert _ask(limits, user_id=1) == ("No matching trains found.", 1)
    assert _ask(limits, user_id=1) == ("No matching trains found.", 1)
    assert _ask(limits, user_id=1) == (telegram_bot.RATE_LIMITED_MESSAGE, 0)
    # Another rider is not held back by the first one.
    assert _ask(limits, user_id=2) == ("No matching trains found.", 1)


def test_get_trains_info_limits_how_fast_everyone_together_can_ask():
    limits = QuestionLimits(total_minute=RateLimiter(2))

    assert _ask(limits, user_id=1)[1] == 1
    assert _ask(limits, user_id=2)[1] == 1
    assert _ask(limits, user_id=3) == (telegram_bot.RATE_LIMITED_MESSAGE, 0)


def test_get_trains_info_stops_replying_to_a_flood_of_refused_messages():
    limits = QuestionLimits(user_minute=RateLimiter(1), refusal_replies=RateLimiter(2))
    too_long = "x" * (telegram_bot.MAX_QUESTION_LENGTH + 1)

    assert _ask(limits, user_id=1)[1] == 1
    assert _ask(limits, user_id=1) == (telegram_bot.RATE_LIMITED_MESSAGE, 0)
    assert _ask(limits, too_long, user_id=1) == (
        telegram_bot.QUESTION_TOO_LONG_MESSAGE,
        0,
    )
    assert _ask(limits, user_id=1) == (None, 0)
    assert _ask(limits, too_long, user_id=1) == (None, 0)
    # Another rider still hears why their message was turned away.
    assert _ask(limits, too_long, user_id=2) == (
        telegram_bot.QUESTION_TOO_LONG_MESSAGE,
        0,
    )


def test_get_trains_info_keeps_a_flood_from_many_accounts_out_of_the_user_table():
    limits = QuestionLimits(total_minute=RateLimiter(2))

    for user_id in range(1, 31):
        _ask(limits, user_id=user_id)

    assert len(limits.user_minute._events) == 2


def test_get_trains_info_limits_one_users_questions_per_day():
    limits = QuestionLimits(user_day=DailyLimiter(1))

    assert _ask(limits, user_id=1)[1] == 1
    assert _ask(limits, user_id=1) == (telegram_bot.USER_DAILY_LIMIT_MESSAGE, 0)
    assert _ask(limits, user_id=2)[1] == 1


def test_get_trains_info_limits_all_questions_per_day():
    limits = QuestionLimits(total_day=DailyLimiter(1))

    assert _ask(limits, user_id=1)[1] == 1
    assert _ask(limits, user_id=2) == (telegram_bot.DAILY_LIMIT_MESSAGE, 0)


def test_get_trains_info_turns_questions_away_when_every_llm_slot_is_taken():
    limits = QuestionLimits(llm_slots=ConcurrencyLimiter(1))
    assert limits.llm_slots.try_acquire()

    assert _ask(limits) == (telegram_bot.BUSY_MESSAGE, 0)
    # A question turned away for lack of a slot does not use up the day's cap.
    limits.llm_slots.release()
    limits.user_day = DailyLimiter(1)
    assert _ask(limits)[1] == 1


def test_get_trains_info_frees_its_llm_slot_after_answering_and_after_refusing():
    limits = QuestionLimits(user_day=DailyLimiter(1), llm_slots=ConcurrencyLimiter(1))

    assert _ask(limits, user_id=1)[1] == 1
    assert _ask(limits, user_id=1) == (telegram_bot.USER_DAILY_LIMIT_MESSAGE, 0)
    assert _ask(limits, user_id=2)[1] == 1


def test_get_trains_info_limits_a_senderless_post_by_its_chat():
    limits = QuestionLimits(user_minute=RateLimiter(1))

    assert _ask(limits, user_id=None, chat_id=7)[1] == 1
    assert _ask(limits, user_id=None, chat_id=7) == (
        telegram_bot.RATE_LIMITED_MESSAGE,
        0,
    )
    assert _ask(limits, user_id=None, chat_id=8)[1] == 1


def test_build_app_gives_the_question_handler_its_own_limits():
    app = telegram_bot.build_app("123:abc", MagicMock())

    handlers = [h for group in app.handlers.values() for h in group]
    question_handler = handlers[-1]
    assert isinstance(question_handler.callback.keywords["limits"], QuestionLimits)
