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

Running the bot requires `TELEGRAM_BOT_TOKEN`, `OPENROUTER_API_KEY`, and
`OPENROUTER_MODEL` from the shell or `.env`. `OPENROUTER_FALLBACK_MODEL` and
`OPENROUTER_REASONING_EFFORT` are optional.

## Code Map

- `src/caltrain_bot/__init__.py`: CLI entrypoint that builds and runs the Telegram app.
- `src/caltrain_bot/telegram_bot.py`: Telegram handlers, app wiring, and user-facing message formatting.
- `src/caltrain_bot/config.py`: environment validation and repo-relative asset paths.
- `src/caltrain_bot/schedule.py`: GTFS loading, SQL preprocessing, station lookup, and train queries.
- `src/caltrain_bot/question_analysis.py`: single-call DSPy question classification and station/time extraction.
- `sql/train_stop_timeline.sql`: preprocessing SQL executed against the GTFS-loaded SQLite database.
- `data/caltrain-ca-us.zip`: checked-in Caltrain GTFS archive required at runtime.
- `data/schedule_question_program.json`: GEPA-optimized prompt state loaded at startup when present.
- `data/schedule.db`: generated SQLite cache used only when `DEBUG=1`.
- `evals/Evals & Optimizer.ipynb`: evaluation and GEPA optimization notebook that writes the prompt state.
- `tests/unit/`: unit tests.

## Repo-Specific Guardrails

- `load_settings()` expects `data/caltrain-ca-us.zip` and `sql/train_stop_timeline.sql` at fixed repo-relative paths. If either path changes, update code and tests in the same change.
- By default the bot uses an in-memory SQLite database. When `DEBUG=1`, it creates or reuses `data/schedule.db`. Treat `data/schedule.db` as generated debug state and avoid committing incidental changes to it.
- `telegram_bot.py` sends HTML-formatted Telegram messages. Escape any user-controlled text before interpolating it into responses.
- Schedule preprocessing executes the SQL file statement-by-statement and commits after each statement. Keep schema/query changes aligned with `ScheduleManager.get_trains()` and related tests. `train_station_journeys` has millions of rows held in memory: keep it to the columns `get_trains()` reads and keep its lookup index.
- Question analysis is a single LLM call per message and runs through `acall` so the handler never blocks the event loop. Do not add extra LLM round trips without measuring latency on the labeled questions.
- Prefer tests that avoid real network calls. Mock or stub DSPy, LLM providers, and Telegram objects instead of relying on live services.

## Change Guidance

- Keep transport-specific behavior in `telegram_bot.py`; reuse `schedule.py` and `question_analysis.py` instead of duplicating logic in handlers.
- Keep changes compatible with Python 3.11.
- If you change config or environment variable behavior, update `README.md` and the relevant tests.
- If you change user-visible bot copy, update `tests/unit/test_telegram_bot.py`.
- If you change schedule loading, SQL preprocessing, or query behavior, run the related unit tests before finishing.
