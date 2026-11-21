import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import caltrain_bot


@pytest.fixture
def wiring(monkeypatch):
    """Replace everything `main()` starts, so no schedule, port, or bot is real."""
    mocks = SimpleNamespace(
        service=object(),
        web_app=object(),
        telegram_app=MagicMock(post_init=None, post_shutdown=None),
        run_app=MagicMock(),
        runner=MagicMock(cleanup=AsyncMock()),
    )
    mocks.build_web_app = MagicMock(return_value=mocks.web_app)
    mocks.build_app = MagicMock(return_value=mocks.telegram_app)
    mocks.start_web_server = AsyncMock(return_value=mocks.runner)
    monkeypatch.setattr(caltrain_bot, "load_dotenv", lambda: False)
    monkeypatch.setattr(caltrain_bot, "configure_logging", lambda: None)
    monkeypatch.setattr(caltrain_bot, "build_schedule_service", lambda _: mocks.service)
    monkeypatch.setattr(caltrain_bot, "build_web_app", mocks.build_web_app)
    monkeypatch.setattr(caltrain_bot, "build_app", mocks.build_app)
    monkeypatch.setattr(caltrain_bot, "run_app", mocks.run_app)
    monkeypatch.setattr(caltrain_bot, "start_web_server", mocks.start_web_server)

    def use_settings(telegram_bot_token, web_port):
        settings = SimpleNamespace(
            telegram_bot_token=telegram_bot_token, web_port=web_port
        )
        monkeypatch.setattr(caltrain_bot, "load_settings", lambda: settings)

    mocks.use_settings = use_settings
    return mocks


def test_main_serves_only_the_web_page_without_a_telegram_token(wiring):
    wiring.use_settings(telegram_bot_token=None, web_port=8080)

    caltrain_bot.main()

    wiring.run_app.assert_called_once_with(wiring.web_app, port=8080)
    wiring.build_web_app.assert_called_once_with(wiring.service)
    wiring.build_app.assert_not_called()


def test_main_runs_only_telegram_without_a_web_port(wiring):
    wiring.use_settings(telegram_bot_token="token", web_port=None)

    caltrain_bot.main()

    wiring.build_app.assert_called_once_with("token", wiring.service)
    wiring.telegram_app.run_polling.assert_called_once_with()
    assert wiring.telegram_app.post_init is None
    wiring.run_app.assert_not_called()
    wiring.build_web_app.assert_not_called()


def test_main_serves_the_web_page_next_to_telegram(wiring):
    wiring.use_settings(telegram_bot_token="token", web_port=8080)

    caltrain_bot.main()

    wiring.telegram_app.run_polling.assert_called_once_with()
    wiring.run_app.assert_not_called()
    # Telegram runs these hooks around polling, on its own event loop.
    asyncio.run(wiring.telegram_app.post_init(wiring.telegram_app))
    wiring.start_web_server.assert_awaited_once_with(wiring.web_app, 8080)
    asyncio.run(wiring.telegram_app.post_shutdown(wiring.telegram_app))
    wiring.runner.cleanup.assert_awaited_once_with()


def test_logging_keeps_local_variables_out_of_tracebacks(monkeypatch):
    added = []
    monkeypatch.setattr(caltrain_bot.logger, "remove", lambda *args: None)
    monkeypatch.setattr(
        caltrain_bot.logger, "add", lambda sink, **options: added.append(options)
    )

    caltrain_bot.configure_logging()

    # diagnose=True would print riders' questions with every traceback.
    assert added == [{"diagnose": False}]
