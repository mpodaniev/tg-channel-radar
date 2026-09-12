# tg-channel-radar

Live dashboard analytics for public Telegram channels. See `PLAN.md` for the full plan and
`TEST-TASK.md` for the assignment this project implements.

## Local development

```
uv sync                      # install runtime + dev dependencies
uv run pytest -q             # tests
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy app       # type check
```

> Full setup (env vars, DB, running the app) and deployment docs land in a later stage.

## Running tests

```
docker compose up -d db      # start local Postgres (see docker-compose.yml)
uv run pytest -q
```

The test suite is Postgres-only: `tests/conftest.py` creates a `<database>_test` database next to
`DATABASE_URL` automatically (override with `TEST_DATABASE_URL` in CI) and runs Alembic migrations
against it before any test executes. Network access is forbidden inside tests — a guard fixture
breaks the `httpx` transport, so the parser is tested against saved HTML fixtures and `ingest_channel`
is tested with an injected fetcher.

## Web pages

Three server-rendered Jinja2 pages, wired in `app/main.py:create_app`:

- `GET /` — list of tracked channels with health badges, subscriber growth, and an add-channel
  form (`app/web/templates/index.html`). Submitting `@channel` / `t.me/channel` hits
  `POST /api/channels` (`app/web/routes_api.py`), which creates the channel as `pending` and
  schedules a two-phase first ingest (`app/web/background.py`) as a `BackgroundTasks` job in
  its own DB session: a fast pass (20 posts, no date window) makes the channel `active` in a
  few seconds, then a deep pass runs in the background collecting the last 90 days of posts
  (`ingest.COLLECT_WINDOW_DAYS`, matching the longest dashboard filter), capped at 2000 posts /
  120 pages as a safety limit for hyperactive channels. The new row polls
  `GET /api/channels/{username}/status` every 2s
  (`hx-get`) until the channel leaves `pending`; a failed channel (`not_found` / `private` /
  `error`) stays visible with an explanation and `Retry` (`POST .../refresh`) / `Delete`
  (`DELETE /api/channels/{username}`) actions. All four endpoints render HTML fragments, not
  JSON — the form's own errors (invalid username, duplicate channel) come back as a
  `partials/add_channel_error.html` fragment with `HX-Retarget`/`HX-Reswap` headers instead of
  going through the global JSON error handler.
- `GET /channels/{username}` — subscriber/daily-views charts and the post table for one channel.
  Accepts `?days=7|30|90` (default 30, from `analytics.DEFAULT_PERIOD_DAYS`); any other value
  returns 400. `@Username` is normalized via `app.parser.normalize.normalize_username`, so
  `/channels/@Durov` and `/channels/durov` resolve to the same page.
- `GET /posts/{post_id}` — full post text, metrics, and a view-growth chart.

All three call straight into `app.services.analytics` and pass only plain DTOs
(`analytics_types.py`) into templates — no ORM instances ever reach Jinja (every relationship is
`lazy="raise"`). Business errors (`ChannelNotFoundInDbError`, `PostNotFoundError`,
`InvalidPeriodError`, ...) are mapped to HTTP status codes in one place, `app/web/errors.py`.

HTMX and Chart.js are vendored under `app/web/static/vendor/` (not loaded from a CDN) so the demo
doesn't depend on an external network or Render's cold start — versions and checksums are recorded
in `app/web/static/vendor/VERSIONS.md`.
