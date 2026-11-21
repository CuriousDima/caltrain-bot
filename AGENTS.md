# AGENTS.md

These instructions apply to the whole repository.

## Project Snapshot

- This is a Python 3.11+ Telegram bot and mobile web page that answer Caltrain schedule questions from static GTFS data.
- Dependencies and task execution use `uv`.
- The package uses a `src/` layout with the CLI entrypoint in `src/caltrain_bot/__init__.py`.

## Setup And Verification

- Install dependencies: `uv sync`
- Run the bot: `uv run caltrain-bot`
- Run tests: `uv run pytest`
- Lint: `uv run ruff check .`
- Format Python: `uv run ruff format .`
- Type-check: `uv run ty check src`

Running the bot requires `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` from the
shell or `.env`, plus at least one frontend: `TELEGRAM_BOT_TOKEN` for Telegram,
`WEB_PORT` for the web page. `OPENROUTER_FALLBACK_MODEL` and
`OPENROUTER_REASONING_EFFORT` are optional. `STATS_PORT` (optional, needs
`WEB_PORT`) serves the private statistics page on a second port.

The local `.env` holds the live bot's `TELEGRAM_BOT_TOKEN`. To try the web page
without polling Telegram as the live bot, run
`TELEGRAM_BOT_TOKEN= WEB_PORT=8080 uv run caltrain-bot`.

## Code Map

- `src/caltrain_bot/__init__.py`: CLI entrypoint that starts the Telegram bot, the web server, or both in one process.
- `src/caltrain_bot/answers.py`: frontend-independent `answer_question()` flow and the shared `ScheduleService`.
- `src/caltrain_bot/telegram_bot.py`: Telegram handlers, app wiring, and user-facing message formatting.
- `src/caltrain_bot/web.py`: aiohttp app with the page, the `/api/ask` JSON endpoint, the `/api/visit` visit counter, and rate limiting.
- `src/caltrain_bot/stats_web.py`: separate aiohttp app for the private statistics page (`GET /`, `GET /api/stats`), served on `STATS_PORT`.
- `src/caltrain_bot/usage.py`: SQLite usage log for the web page (visits and questions), its queries, and the `caltrain-bot-usage` report command.
- `src/caltrain_bot/static/index.html`: the whole web page, including its JavaScript.
- `src/caltrain_bot/static/app.css`: generated Tailwind stylesheet, checked in. Rebuild with `npm install && npm run build:css`.
- `src/caltrain_bot/config.py`: environment validation and repo-relative asset paths.
- `src/caltrain_bot/schedule.py`: GTFS loading, SQL preprocessing, station lookup, and train queries.
- `src/caltrain_bot/question_analysis.py`: single-call DSPy question classification and station/time extraction.
- `sql/train_stop_timeline.sql`: preprocessing SQL executed against the GTFS-loaded SQLite database.
- `data/caltrain-ca-us.zip`: checked-in Caltrain GTFS archive required at runtime.
- `data/schedule_question_program.json`: GEPA-optimized prompt state loaded at startup when present.
- `data/schedule.db`: generated SQLite cache used only when `DEBUG=1`.
- `data/usage.db`: generated web usage statistics; `USAGE_DB_PATH` moves it. Never commit it.
- `evals/Evals & Optimizer.ipynb`: evaluation and GEPA optimization notebook that writes the prompt state.
- `tests/unit/`: unit tests.

## Repo-Specific Guardrails

- `load_settings()` expects `data/caltrain-ca-us.zip` and `sql/train_stop_timeline.sql` at fixed repo-relative paths. If either path changes, update code and tests in the same change.
- By default the bot uses an in-memory SQLite database. When `DEBUG=1`, it creates or reuses `data/schedule.db`. Treat `data/schedule.db` as generated debug state and avoid committing incidental changes to it.
- `telegram_bot.py` sends HTML-formatted Telegram messages. Escape any user-controlled text before interpolating it into responses.
- The web page is served with a hash-based Content-Security-Policy and renders answers with `textContent` only. Do not add `innerHTML`, external scripts, or a second inline `<style>`.
- `index.html` may only use Tailwind classes that exist in `app.css`; `tests/unit/test_web.py` fails when the stylesheet is stale. Keep every class in a `class="..."` attribute (toggle state with `hidden` and `data-*` attributes) so that test sees it.
- A question with a stated time gets the trains in a window around it (`get_trains()`); a question without one ("next train") gets the next three departures (`get_next_trains()`). `ScheduleQuestion.departure_time_stated` tells them apart, and the model signals it by returning a null `departure_time`.
- Both frontends share one `ScheduleService`, because the schedule is too large to load twice. Do not build a second `ScheduleManager`.
- Usage statistics hold, per question, a timestamp, an anonymous visitor ID, the outcome, and the duration; per page visit, a timestamp, the visitor ID, a two-letter country, a coarse device class, and the referring site's host name; and nothing else. Do not store question text, IP addresses, User-Agent strings, or full URLs, and do not use the visitor ID for anything but counting visitors. The page keeps its state in local storage and sets no cookies.
- Recording usage must never fail or delay an answer: `UsageLog.record_question()` and `UsageLog.record_visit()` swallow their own errors.
- The usage database in production predates newer tables. Only add tables or indexes with `IF NOT EXISTS` in `usage.py`; never drop, rename, or rewrite existing ones.
- Statistics are private. The public app in `web.py` must never serve them, and `stats_web.py` must stay a separate app on `STATS_PORT` with read-only routes and no routes shared with the public app. Country and referrer values come from clients: escape everything `stats_web.py` renders, and keep its Content-Security-Policy free of scripts and `unsafe-inline`.
- `/api/visit` is public and each accepted call is a disk write. Keep its rate limits, its strict validation, and its unconditional `204` in `web.py`.
- `/api/ask` is public and each call costs an LLM request. Keep the rate limits and the question length cap in `web.py`.
- Every web route runs behind `MIDDLEWARES` in `web.py`: `security_headers_middleware` (security headers on every response, a generic JSON 500 for unexpected errors) and `api_guard_middleware` (refuses cross-site and non-JSON requests for every non-GET route under `/api/`). A new `/api/` POST route is covered automatically, so the page must call it with `Content-Type: application/json`. Client addresses for rate limiting come from `_client_key()`, which trusts `CF-Connecting-IP`; the app must only be reachable through the trusted proxy.
- Schedule preprocessing executes the SQL file statement-by-statement and commits after each statement. Keep schema/query changes aligned with `ScheduleManager.get_trains()`, `get_next_trains()`, and related tests. `train_station_journeys` has millions of rows held in memory: keep it to the columns those queries read and keep its lookup index.
- Question analysis is a single LLM call per message and runs through `acall` so the handler never blocks the event loop. Do not add extra LLM round trips without measuring latency on the labeled questions.
- Prefer tests that avoid real network calls. Mock or stub DSPy, LLM providers, and Telegram objects instead of relying on live services.

## Change Guidance

- Keep transport-specific behavior in `telegram_bot.py` and `web.py`; reuse `answers.py`, `schedule.py`, and `question_analysis.py` instead of duplicating logic in handlers.
- Keep changes compatible with Python 3.11.
- If you change config or environment variable behavior, update `README.md` and the relevant tests.
- If you change user-visible bot copy, update `tests/unit/test_telegram_bot.py` and `tests/unit/test_web.py`.
- If you change schedule loading, SQL preprocessing, or query behavior, run the related unit tests before finishing.
