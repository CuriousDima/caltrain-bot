import asyncio
import functools
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
from caltrain_bot.schedule import Train


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
) -> None:
    if not update.message:
        return
    if not update.message.text:
        _ = await update.message.reply_text("Sorry, I can only process text messages.")
        return
    # The typing indicator goes out while the question is being analyzed, so
    # it does not delay the answer.
    _, answer = await asyncio.gather(
        _show_typing(update),
        answer_question(update.message.text, service),
    )
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
            functools.partial(get_trains_info, service=service),
        )
    )
    return app
