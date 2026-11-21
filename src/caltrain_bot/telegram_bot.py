import asyncio
import functools
from dataclasses import dataclass, field
from datetime import datetime
from html import escape

from loguru import logger
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from caltrain_bot.answers import ScheduleService, answer_question
from caltrain_bot.limits import (
    ConcurrencyLimiter,
    DailyLimiter,
    RateLimiter,
    allow_within_total,
)
from caltrain_bot.schedule import Train


MAX_QUESTION_LENGTH = 300
# Every question costs one paid LLM call, so cap how fast anyone can ask.
QUESTIONS_PER_MINUTE_PER_USER = 12
QUESTIONS_PER_MINUTE_TOTAL = 120
QUESTIONS_PER_DAY_PER_USER = 300
QUESTIONS_PER_DAY_TOTAL = 30_000
# Questions waiting on the LLM at once; more are turned away, never queued.
MAX_CONCURRENT_QUESTIONS = 8
# Replies that turn a message away, per user. Further refused messages get
# silence: Telegram limits how fast a bot may send, and a script that floods
# the bot would use up what other riders' answers need.
REFUSALS_PER_MINUTE_PER_USER = 3

TEXT_ONLY_MESSAGE = "Sorry, I can only process text messages."
QUESTION_TOO_LONG_MESSAGE = "That question is too long. Please keep it short."
RATE_LIMITED_MESSAGE = "Too many questions right now. Please try again in a minute."
USER_DAILY_LIMIT_MESSAGE = (
    "You have asked a lot of questions today. Please try again tomorrow."
)
DAILY_LIMIT_MESSAGE = (
    "I have answered all the questions I can today. Please try again tomorrow."
)
BUSY_MESSAGE = "Lots of riders are asking right now. Please try again in a few seconds."


@dataclass
class QuestionLimits:
    """What a Telegram question has to pass before it may cost an LLM call."""

    user_minute: RateLimiter = field(
        default_factory=lambda: RateLimiter(QUESTIONS_PER_MINUTE_PER_USER)
    )
    total_minute: RateLimiter = field(
        default_factory=lambda: RateLimiter(QUESTIONS_PER_MINUTE_TOTAL)
    )
    user_day: DailyLimiter = field(
        default_factory=lambda: DailyLimiter(QUESTIONS_PER_DAY_PER_USER)
    )
    total_day: DailyLimiter = field(
        default_factory=lambda: DailyLimiter(QUESTIONS_PER_DAY_TOTAL)
    )
    llm_slots: ConcurrencyLimiter = field(
        default_factory=lambda: ConcurrencyLimiter(MAX_CONCURRENT_QUESTIONS)
    )
    refusal_replies: RateLimiter = field(
        default_factory=lambda: RateLimiter(REFUSALS_PER_MINUTE_PER_USER)
    )

    def refusal(self, user: str) -> str | None:
        """Why this question is not answered, or None after reserving a slot.

        The caller must release ``llm_slots`` when None is returned.
        """
        if not allow_within_total(self.user_minute, self.total_minute, user):
            return RATE_LIMITED_MESSAGE
        if not self.llm_slots.try_acquire():
            return BUSY_MESSAGE
        # Checked last, so the daily caps count only questions that really
        # reach the LLM.
        refusal = None
        if not self.user_day.allow(user):
            refusal = USER_DAILY_LIMIT_MESSAGE
        elif not self.total_day.allow():
            refusal = DAILY_LIMIT_MESSAGE
        if refusal is not None:
            self.llm_slots.release()
        return refusal


def _user_key(update: Update) -> str:
    # Telegram vouches for the sender's ID, so it cannot be forged the way a
    # web header can. Channel posts have no sender; the chat stands in.
    if update.effective_user is not None:
        return f"user:{update.effective_user.id}"
    if update.effective_chat is not None:
        return f"chat:{update.effective_chat.id}"
    return "unknown"


def _format_timestamp(value: datetime | str) -> str:
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M")
    except ValueError:
        return str(value)


def format_trains_message(trains: list[Train]) -> str:
    if not trains:
        return "No matching trains found."

    parts = [f"Found <b>{len(trains)}</b> matching trains 🚂:"]
    for train in trains:
        origin = escape(train.origin_station_name.removesuffix(" Station"))
        destination = escape(train.destination_station_name.removesuffix(" Station"))
        departure = escape(_format_timestamp(train.origin_departure_timestamp))
        arrival = escape(_format_timestamp(train.destination_arrival_timestamp))
        service_pattern = escape(train.service_pattern)
        parts.append(
            "\n".join(
                (
                    f"<b>{origin}</b> {departure} → <b>{destination}</b> {arrival}",
                    f"{train.travel_minutes} min | Train {train.train_number} | {service_pattern}",
                )
            )
        )
    return "\n\n".join(parts)


def format_start_message(name: str) -> str:
    safe_name = escape(name)
    return "\n".join(
        (
            f"Hello {safe_name}! I can help you find Caltrain trips between stations.",
            "",
            "Ask in plain English. For example:",
            "<code>San Francisco to Palo Alto after 7pm</code>",
            "<code>Next train from Mountain View to San Jose Diridon</code>",
            "<code>Need a train from Millbrae to 22nd Street around 8:30</code>",
            "",
            "Include your departure station, destination, and optionally a time.",
            "Send <code>/info</code> for more about the bot, the maintainer, and contributing.",
        )
    )


def format_info_message() -> str:
    return "\n".join(
        (
            "Caltrain Bot helps with scheduled Caltrain trips between stations.",
            "",
            "What I can do:",
            "- Find matching trains between Caltrain stations",
            "- Understand natural-language questions about routes and times",
            "- Show departure, arrival, duration, train number, and service type",
            "",
            "Example questions:",
            "<code>Belmont to San Francisco tomorrow at 9am</code>",
            "<code>What is the next train from Sunnyvale to Millbrae?</code>",
            "<code>Trains from Palo Alto to San Jose Diridon after 6:15pm</code>",
            "",
            "Good to know:",
            "- This bot works from timetable data, not live delay or service alert feeds",
            "- It does not handle ticketing, fares, or bookings",
            "",
            "Contributing: https://github.com/CuriousDima/caltrain-bot",
        )
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    name = update.effective_user.first_name if update.effective_user else "there"
    _ = await update.message.reply_text(format_start_message(name), parse_mode="HTML")


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    _ = await update.message.reply_text(format_info_message(), parse_mode="HTML")


async def _show_typing(update: Update) -> None:
    """Show the "typing…" indicator; it is cosmetic, so failures are ignored."""
    if not update.message:
        return
    try:
        _ = await update.message.reply_chat_action(ChatAction.TYPING)
    except Exception:
        logger.debug("Could not send the typing indicator.")


async def get_trains_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    service: ScheduleService,
    limits: QuestionLimits,
) -> None:
    if not update.message:
        return
    if not update.message.text:
        _ = await update.message.reply_text(TEXT_ONLY_MESSAGE)
        return
    question = " ".join(update.message.text.split())
    user = _user_key(update)
    if len(question) > MAX_QUESTION_LENGTH:
        refusal = QUESTION_TOO_LONG_MESSAGE
    else:
        refusal = limits.refusal(user)
    if refusal is not None:
        if limits.refusal_replies.allow(user):
            _ = await update.message.reply_text(refusal)
        return
    try:
        # The typing indicator goes out while the question is being analyzed,
        # so it does not delay the answer.
        _, answer = await asyncio.gather(
            _show_typing(update),
            answer_question(question, service),
        )
    finally:
        limits.llm_slots.release()
    if answer.message is not None:
        _ = await update.message.reply_text(answer.message)
        return
    trains_message = format_trains_message(list(answer.trains))
    _ = await update.message.reply_text(trains_message, parse_mode="HTML")


def build_app(token: str, service: ScheduleService) -> Application:
    app = (
        ApplicationBuilder()
        .token(token)
        # Handle each message as soon as it arrives instead of queueing behind
        # another user's question.
        .concurrent_updates(True)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("info", info))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            functools.partial(
                get_trains_info, service=service, limits=QuestionLimits()
            ),
        )
    )
    return app
