import pytest

from caltrain_bot.telegram_bot import welcome_message


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Alice", "Hello Alice! Welcome to the Caltrain Bot."),
        ("Bob", "Hello Bob! Welcome to the Caltrain Bot."),
        ("", "Hello ! Welcome to the Caltrain Bot."),
    ],
)
def test_welcome_message(name, expected):
    assert welcome_message(name) == expected
