import logging
import sys

from aiohttp.web import AppRunner, run_app
from dotenv import load_dotenv
from loguru import logger
from telegram.ext import Application

from caltrain_bot.answers import build_schedule_service
from caltrain_bot.config import load_settings
from caltrain_bot.telegram_bot import build_app
from caltrain_bot.web import build_web_app, start_web_server


class _WithoutTraceback(logging.Filter):
    """Lets a log record through as one line, without its traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.exc_info = None
        record.exc_text = None
        return True


_WITHOUT_TRACEBACK = _WithoutTraceback()


def configure_logging() -> None:
    # loguru's default prints every local variable in a traceback, which puts
    # riders' questions (and whatever else is in scope) into the logs.
    logger.remove()
    logger.add(sys.stderr, diagnose=False)
    # aiohttp answers a malformed request by itself and logs the parser's
    # whole traceback for it. Anyone can send those as fast as they like, so
    # one line each is enough. Errors in our own handlers never get this far:
    # security_headers_middleware logs them in full.
    logging.getLogger("aiohttp.server").addFilter(_WITHOUT_TRACEBACK)


def main() -> None:
    configure_logging()
    _ = load_dotenv()
    settings = load_settings()
    # The schedule takes ~1.6 GB of memory, so both frontends share one copy.
    service = build_schedule_service(settings)

    web_app = None
    web_port = settings.web_port
    if web_port is not None:
        web_app = build_web_app(service)

    if settings.telegram_bot_token is None:
        # load_settings() guarantees at least one frontend, so this is the web.
        if web_app is not None:
            run_app(web_app, port=web_port)
        return

    telegram_app = build_app(settings.telegram_bot_token, service)
    if web_app is not None and web_port is not None:
        # Telegram polling owns the event loop; the web server rides along.
        runners: list[AppRunner] = []

        async def start_web(_: Application) -> None:
            runners.append(await start_web_server(web_app, web_port))

        async def stop_web(_: Application) -> None:
            for runner in runners:
                await runner.cleanup()

        telegram_app.post_init = start_web
        telegram_app.post_shutdown = stop_web
    telegram_app.run_polling()


if __name__ == "__main__":
    main()
