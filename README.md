# Caltrain Bot

Caltrain Bot is a Python chatbot that answers Caltrain schedule questions from natural language. It currently ships as a Telegram bot, loads Caltrain GTFS data into SQLite, and uses DSPy plus an LLM provider to understand questions such as "When is the next train from Palo Alto to San Francisco around 8am?"

## Project Layout

- `src/caltrain_bot/telegram_bot.py`: Telegram bot handlers and message formatting.
- `src/caltrain_bot/question_analysis.py`: DSPy-based question classification and entity extraction.
- `src/caltrain_bot/schedule.py`: GTFS loading, preprocessing, and schedule queries.
- `sql/train_stop_timeline.sql`: SQL used to preprocess imported GTFS data into query-friendly tables.
- `src/caltrain_bot/config.py`: Environment-based configuration for Telegram and the LLM provider.
- `data/caltrain-ca-us.zip`: Bundled Caltrain GTFS feed used by the app.
- `tests/unit/`: Unit tests for schedule loading and configuration validation.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- A Telegram bot token
- A supported LLM provider: Ollama or OpenRouter

## Configuration

The bot loads environment variables from your shell and from a `.env` file if present.

Required variables:

- `TELEGRAM_BOT_TOKEN`
- `LLM_PROVIDER` set to `ollama` or `openrouter`

If `LLM_PROVIDER=ollama`, also set:

- `OLLAMA_API_BASE`
- `OLLAMA_MODEL`

If `LLM_PROVIDER=openrouter`, also set:

- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL`

## Run Locally

Install dependencies:

```shell
uv sync
```

Start the bot:

```shell
uv run caltrain-bot
```

## Run Tests

```shell
uv run pytest
```

## Docker

Build and publish the Raspberry Pi image locally:

```shell
docker buildx build --platform linux/arm64 \
  -t curiousdima/caltrain-bot:0.1.1 \
  -t curiousdima/caltrain-bot:latest \
  --push .
```

Required environment variables:

- Always: `TELEGRAM_BOT_TOKEN`, `LLM_PROVIDER`
- OpenRouter mode: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
- Ollama mode: `OLLAMA_API_BASE`, `OLLAMA_MODEL`

Example `/opt/caltrain-bot/caltrain-bot.env` for OpenRouter:

```dotenv
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-openrouter-api-key
OPENROUTER_MODEL=openai/gpt-4.1-mini
```

Deploy on a Raspberry Pi:

```shell
docker run -d \
  --name caltrain-bot \
  --restart unless-stopped \
  --env-file /opt/caltrain-bot/caltrain-bot.env \
  curiousdima/caltrain-bot:latest
```

Example `/opt/caltrain-bot/caltrain-bot.env` for Ollama on the same Raspberry Pi host:

```dotenv
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
LLM_PROVIDER=ollama
OLLAMA_API_BASE=http://127.0.0.1:11434
OLLAMA_MODEL=llama3.2
```

Deploy with host networking when Ollama runs directly on the Raspberry Pi host:

```shell
docker run -d \
  --name caltrain-bot \
  --restart unless-stopped \
  --network host \
  --env-file /opt/caltrain-bot/caltrain-bot.env \
  curiousdima/caltrain-bot:latest
```

## Contributing

Contributions are welcome. The current project focuses on Telegram, but the schedule and question-analysis logic are already separated enough to support additional chat frontends.

People are especially welcome to contribute WhatsApp and WeChat support. If you want to add another messaging integration, try to keep transport-specific code at the edge and reuse the existing schedule lookup and question parsing modules.
