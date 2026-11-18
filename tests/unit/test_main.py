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
        usage_log=object(),
    )
    mocks.build_web_app = MagicMock(return_value=mocks.web_app)
    mocks.open_usage_log = MagicMock(return_value=mocks.usage_log)
    mocks.build_app = MagicMock(return_value=mocks.telegram_app)
    mocks.start_web_server = AsyncMock(return_value=mocks.runner)
    monkeypatch.setattr(caltrain_bot, "load_dotenv", lambda: False)
    monkeypatch.setattr(caltrain_bot, "build_schedule_service", lambda _: mocks.service)
    monkeypatch.setattr(caltrain_bot, "build_web_app", mocks.build_web_app)
    monkeypatch.setattr(caltrain_bot, "open_usage_log", mocks.open_usage_log)
    monkeypatch.setattr(caltrain_bot, "build_app", mocks.build_app)
    monkeypatch.setattr(caltrain_bot, "run_app", mocks.run_app)
    monkeypatch.setattr(caltrain_bot, "start_web_server", mocks.start_web_server)

    def use_settings(telegram_bot_token, web_port):
        settings = SimpleNamespace(
            telegram_bot_token=telegram_bot_token,
            web_port=web_port,
            usage_db_path="usage.db",
        )
        monkeypatch.setattr(caltrain_bot, "load_settings", lambda: settings)

    mocks.use_settings = use_settings
    return mocks


def test_main_serves_only_the_web_page_without_a_telegram_token(wiring):
    wiring.use_settings(telegram_bot_token=None, web_port=8080)

    caltrain_bot.main()

    wiring.run_app.assert_called_once_with(wiring.web_app, port=8080)
    wiring.open_usage_log.assert_called_once_with("usage.db")
    wiring.build_web_app.assert_called_once_with(wiring.service, wiring.usage_log)
    wiring.build_app.assert_not_called()


def test_main_runs_only_telegram_without_a_web_port(wiring):
    wiring.use_settings(telegram_bot_token="token", web_port=None)

    caltrain_bot.main()

    wiring.build_app.assert_called_once_with("token", wiring.service)
    wiring.telegram_app.run_polling.assert_called_once_with()
    assert wiring.telegram_app.post_init is None
    wiring.run_app.assert_not_called()
    wiring.open_usage_log.assert_not_called()


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
