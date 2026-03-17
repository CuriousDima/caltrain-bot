from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from caltrain_bot.config import load_settings


def welcome_message(name: str) -> str:
    return f"Hello {name}! Welcome to the Caltrain Bot."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    name = update.effective_user.first_name if update.effective_user else "there"
    await update.message.reply_text(welcome_message(name))


def build_app():
    settings = load_settings()
    app = ApplicationBuilder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    return app
