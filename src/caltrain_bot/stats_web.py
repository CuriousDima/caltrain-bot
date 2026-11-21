"""Private statistics page: how the web page is used, read from the usage log.

It is a separate aiohttp app on its own port, so that nothing under the public
page can ever show statistics. Keep that port off the internet. The app only
reads: there is no endpoint that changes anything.
"""

import base64
import hashlib
from dataclasses import asdict
from datetime import date
from html import escape

from aiohttp import web
from loguru import logger

from caltrain_bot.usage import DailyUsage, UsageLog, UsageStats, VisitCount

DEFAULT_DAYS = 30
MIN_DAYS = 1
MAX_DAYS = 365
_WINDOWS = ((7, "7 days"), (30, "30 days"), (90, "90 days"), (365, "1 year"))

# The colors follow the public page: stone grays with a red accent.
_CSS = """
:root {
  color-scheme: light dark;
  --page: #fafaf9; --card: #fff; --text: #1c1917; --muted: #57534e;
  --line: #e7e5e4; --accent: #dc2626;
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #0c0a09; --card: #1c1917; --text: #f5f5f4; --muted: #a8a29e;
    --line: #292524; --accent: #f87171;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--text);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-text-size-adjust: 100%;
}
main { max-width: 72rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
h1 { margin: 0; font-size: 1.5rem; line-height: 1.2; }
h2 {
  margin: 0 0 .5rem; color: var(--muted); font-size: .75rem;
  letter-spacing: .05em; text-transform: uppercase;
}
p { margin: .25rem 0 0; }
section { margin-top: 1.5rem; }
.muted { color: var(--muted); font-size: .875rem; }
nav { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: 1rem; }
nav a {
  padding: .5rem .9rem; border: 1px solid var(--line); border-radius: 999px;
  background: var(--card); color: var(--text); font-size: .875rem;
  text-decoration: none;
}
nav a[aria-current] {
  border-color: var(--accent); color: var(--accent); font-weight: 600;
}
.tiles, .cards { display: grid; gap: .75rem; }
.tiles { grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); margin: 0; }
.cards { grid-template-columns: repeat(auto-fit, minmax(17rem, 1fr)); }
.tile, .card {
  padding: 1rem; border: 1px solid var(--line); border-radius: 1rem;
  background: var(--card);
}
.tile { display: grid; align-content: start; }
.tile dt { color: var(--muted); font-size: .875rem; }
.tile dd {
  order: -1; margin: 0; font-size: 1.75rem; font-weight: 700; line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td {
  padding: .4rem .6rem; border-bottom: 1px solid var(--line);
  text-align: right; white-space: nowrap;
}
th { color: var(--muted); font-size: .8125rem; font-weight: 600; }
th:first-child, td:first-child { padding-left: 0; text-align: left; }
th:last-child, td:last-child { padding-right: 0; }
tbody tr:last-child td { border-bottom: 0; }
/* The name column takes the room the numbers leave and cuts a long name off. */
.counts td:first-child { width: 100%; max-width: 0; }
.name { overflow: hidden; text-overflow: ellipsis; }
"""


def _csp_hash(source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


RESPONSE_HEADERS = {
    "Content-Security-Policy": "; ".join(
        (
            "default-src 'none'",
            f"style-src {_csp_hash(_CSS)}",
            "img-src data:",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
        )
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex",
}


def parse_days(value: str | None) -> int:
    """Read ``?days=N``: 30 when missing or not a number, else within 1-365."""
    try:
        # Longer than any sensible number: do not even try to convert it.
        days = int(value) if value is not None and len(value) <= 10 else DEFAULT_DAYS
    except ValueError:
        return DEFAULT_DAYS
    return min(max(days, MIN_DAYS), MAX_DAYS)


def _text(value: object) -> str:
    """Format one value for HTML. Everything on the page goes through here."""
    if isinstance(value, int):
        return f"{value:,}"
    return escape(str(value))


def _row(cells: list[str], tag: str = "td") -> str:
    return "<tr>" + "".join(f"<{tag}>{cell}</{tag}>" for cell in cells) + "</tr>"


def _table(header: list[str], rows: list[list[str]]) -> str:
    body = "".join(_row(row) for row in rows)
    return (
        '<div class="scroll"><table>'
        f"<thead>{_row([_text(title) for title in header], 'th')}</thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _share(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def _counts_card(
    title: str, name_title: str, counts: list[VisitCount], unnamed: str, visits: int
) -> str:
    if counts:
        names = [_text(count.name or unnamed) for count in counts]
        content = _table(
            [name_title, "Visits", "Visitors", "Share"],
            [
                [
                    # The title shows a name that was cut off in full.
                    f'<div class="name" title="{name}">{name}</div>',
                    _text(count.visits),
                    _text(count.visitors),
                    _share(count.visits, visits),
                ]
                for name, count in zip(names, counts)
            ],
        )
    else:
        content = '<p class="muted">No visits in these days.</p>'
    return f'<div class="card counts"><h2>{_text(title)}</h2>{content}</div>'


def _daily_row(usage: DailyUsage) -> list[str]:
    weekday = date.fromisoformat(usage.day).strftime("%a")
    return [
        f'{_text(usage.day)} <span class="muted">{_text(weekday)}</span>',
        _text(usage.visits),
        _text(usage.visitors),
        _text(usage.new_visitors),
        _text(usage.questions),
        _text(usage.with_trains),
        _text(usage.no_trains),
        _text(usage.unsupported),
        _text(usage.failed),
        "–" if usage.average_ms is None else _text(usage.average_ms),
    ]


def render_stats_page(stats: UsageStats) -> str:
    """The whole dashboard as one HTML document, with no JavaScript."""
    totals = stats.totals
    current = ' aria-current="page"'
    windows = "".join(
        f'<a href="?days={days}"{current if days == stats.days else ""}>'
        f"{_text(label)}</a>"
        for days, label in _WINDOWS
    )
    tiles = "".join(
        f'<div class="tile"><dt>{_text(label)}</dt><dd>{_text(value)}</dd></div>'
        for label, value in (
            ("Visitors, all time", totals.visitors),
            ("Visits, all time", totals.visits),
            ("Questions, all time", totals.questions),
            ("Active visitors, 7 days", totals.visitors_last_7_days),
            ("Active visitors, 30 days", totals.visitors_last_30_days),
        )
    )
    visits = sum(usage.visits for usage in stats.daily)
    cards = "".join(
        (
            _counts_card(
                "Top countries", "Country", stats.countries, "Unknown", visits
            ),
            _counts_card(
                "Top referrers", "Site", stats.referrers, "Direct or unknown", visits
            ),
            _counts_card("Devices", "Device", stats.devices, "other", visits),
        )
    )
    if stats.daily:
        daily = _table(
            [
                "Day",
                "Visits",
                "Visitors",
                "New",
                "Questions",
                "Trains",
                "No trains",
                "Off-topic",
                "Failed",
                "Avg ms",
            ],
            [_daily_row(usage) for usage in stats.daily],
        )
    else:
        daily = '<p class="muted">Nothing was recorded in these days.</p>'
    period = "day" if stats.days == 1 else f"{stats.days} days"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Caltrain page statistics</title>
<link rel="icon" href="data:,">
<style>{_CSS}</style>
</head>
<body>
<main>
<h1>Caltrain page statistics</h1>
<p class="muted">Days are in Caltrain local time. This page is private.</p>
<section>
<h2>All time</h2>
<dl class="tiles">{tiles}</dl>
</section>
<nav aria-label="Period">{windows}</nav>
<section>
<h2>Visits in the last {_text(period)}</h2>
<div class="cards">{cards}</div>
</section>
<section>
<h2>Each day in the last {_text(period)}</h2>
<div class="card">{daily}</div>
</section>
<section class="muted">
<p>A visitor is a random ID kept in one browser. Someone who opened the page
and someone who asked a question count the same; a visit that sent no ID is a
visit without a visitor. New visitors were never seen on an earlier day.</p>
</section>
</main>
</body>
</html>
"""


def build_stats_app(usage_log: UsageLog) -> web.Application:
    async def add_headers(_: web.Request, response: web.StreamResponse) -> None:
        # Runs for every response, error pages included.
        response.headers.update(RESPONSE_HEADERS)

    async def index(request: web.Request) -> web.Response:
        stats = usage_log.stats(parse_days(request.query.get("days")))
        return web.Response(text=render_stats_page(stats), content_type="text/html")

    async def api_stats(request: web.Request) -> web.Response:
        stats = usage_log.stats(parse_days(request.query.get("days")))
        return web.json_response(asdict(stats))

    app = web.Application()
    app.on_response_prepare.append(add_headers)
    app.router.add_get("/", index)
    app.router.add_get("/api/stats", api_stats)
    return app


async def start_stats_server(app: web.Application, port: int) -> web.AppRunner:
    """Serve the statistics app on the running event loop, on its own port."""
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, port=port).start()
    except OSError:
        await runner.cleanup()
        raise
    logger.info(f"Statistics page listening on port {port}")
    return runner
