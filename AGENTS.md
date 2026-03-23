# AGENTS.md

These instructions apply to the whole repository.

## Project Snapshot

- This is a Python 3.11+ Telegram bot that answers Caltrain schedule questions from static GTFS data.
- Dependencies and task execution use `uv`.
- The package uses a `src/` layout with the CLI entrypoint in `src/caltrain_bot/__init__.py`.

## Setup And Verification

- Install dependencies: `uv sync`
- Run the bot: `uv run caltrain-bot`
- Run tests: `uv run pytest`
- Lint: `uv run ruff check .`
- Format Python: `uv run ruff format .`
- Type-check: `uv run ty check src`

Running the bot requires environment variables from the shell or `.env`:

- Always: `TELEGRAM_BOT_TOKEN`, `LLM_PROVIDER`
- Ollama mode: `OLLAMA_API_BASE`, `OLLAMA_MODEL`
- OpenRouter mode: `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
- Optional: `MLFLOW_TRACKING_URL`

## Code Map

- `src/caltrain_bot/__init__.py`: CLI entrypoint that builds and runs the Telegram app.
- `src/caltrain_bot/telegram_bot.py`: Telegram handlers, app wiring, and user-facing message formatting.
- `src/caltrain_bot/config.py`: environment validation and repo-relative asset paths.
- `src/caltrain_bot/schedule.py`: GTFS loading, SQL preprocessing, station lookup, and train queries.
- `src/caltrain_bot/question_analysis.py`: DSPy-based question classification and station/time extraction.
- `sql/train_stop_timeline.sql`: preprocessing SQL executed against the GTFS-loaded SQLite database.
- `data/caltrain-ca-us.zip`: checked-in Caltrain GTFS archive required at runtime.
- `data/schedule.db`: generated SQLite cache used only when `DEBUG=1`.
- `tests/unit/`: unit tests.

## Repo-Specific Guardrails

- `load_settings()` expects `data/caltrain-ca-us.zip` and `sql/train_stop_timeline.sql` at fixed repo-relative paths. If either path changes, update code and tests in the same change.
- By default the bot uses an in-memory SQLite database. When `DEBUG=1`, it creates or reuses `data/schedule.db`. Treat `data/schedule.db` as generated debug state and avoid committing incidental changes to it.
- `telegram_bot.py` sends HTML-formatted Telegram messages. Escape any user-controlled text before interpolating it into responses.
- Schedule preprocessing executes the SQL file statement-by-statement and commits after each statement. Keep schema/query changes aligned with `ScheduleManager.get_trains()` and related tests.
- Prefer tests that avoid real network calls. Mock or stub DSPy, LLM providers, and Telegram objects instead of relying on live services.

## Change Guidance

- Keep transport-specific behavior in `telegram_bot.py`; reuse `schedule.py` and `question_analysis.py` instead of duplicating logic in handlers.
- Keep changes compatible with Python 3.11.
- If you change config or environment variable behavior, update `README.md` and the relevant tests.
- If you change user-visible bot copy, update `tests/unit/test_telegram_bot.py`.
- If you change schedule loading, SQL preprocessing, or query behavior, run the related unit tests before finishing.
