from caltrain_bot.telegram_bot import format_info_message, format_start_message


def test_format_start_message_includes_examples_and_info_prompt():
    message = format_start_message("Dima & Friends")

    assert "Hello Dima &amp; Friends!" in message
    assert "San Francisco to Palo Alto after 7pm" in message
    assert "Next train from Mountain View to San Jose Diridon" in message
    assert "Need a train from Millbrae to 22nd Street around 8:30" in message
    assert "Send <code>/info</code>" in message


def test_format_info_message_includes_capabilities_contact_and_contributing():
    message = format_info_message()

    assert "scheduled Caltrain trips between stations" in message
    assert "What is the next train from Sunnyvale to Millbrae?" in message
    assert "not live delay or service alert feeds" in message
    assert "1127655+CuriousDima@users.noreply.github.com" in message
    assert "uv run pytest" in message
    assert "uv run caltrain-bot" in message
