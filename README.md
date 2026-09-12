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

## AI layer (Gemini 2.0 Flash)

`app/services/ai/` is the only place in the codebase that talks to an LLM. Three tasks:

- **Post classification** (`ai.tasks.classify_new_posts`) — category + 1-3 topic tags per post,
  batched 10 posts/call, only for posts without a `post_ai_annotations` row yet. Runs
  automatically as a best-effort step after every background ingest (`app/web/background.py`),
  capped at 3 batches per run so one channel can't burn the whole daily quota.
- **Channel digest** (`ai.tasks.get_or_create_digest`) — 3-5 bullet points summarizing what a
  channel posted about over the selected period, cached in `channel_digests` keyed by
  `(channel_id, period_start, period_end, prompt_version)` with both boundaries truncated to a
  UTC day so repeated requests on the same day hit the cache. Generated on demand: the channel
  page never calls the LLM while rendering (`routes_pages.channel_page` only reads the cache) —
  a "Generate digest" button (`POST /api/channels/{username}/digest`, HTMX fragment) triggers the
  actual call.
- **Anomaly explanation** (`ai.tasks.explain_anomalies`) — the anomaly itself is still pure
  statistics (`app.services.stats.detect_anomalies`, z-score on view counts); the LLM only writes
  one sentence explaining a flagged spike/drop, cached in `post_ai_annotations.anomaly_note` and
  shown as the badge's tooltip on the channel page.

**Degradation is the point, not an afterthought.** All resilience lives in one place,
`ai.client.AiGateway`:

- an empty `GEMINI_API_KEY` disables the layer entirely (`enabled = False`) — every call raises
  `AiUnavailableError` immediately, no network attempted;
- every call has a 20s timeout and 2 retries with exponential backoff;
- a circuit breaker opens after `AI_FAILURE_THRESHOLD` (default 3) consecutive failures and stays
  open for `AI_COOLDOWN_MINUTES` (default 10), during which calls fail fast without hitting the
  network at all;
- a per-UTC-day call counter enforces `AI_DAILY_CALL_BUDGET` (default 200) against Gemini's free
  tier, resetting at UTC midnight.

Every task catches `AiUnavailableError` and degrades to a `None`/cached result instead of
propagating it — the channel page always renders with charts, the post table, and health badges
working, and the AI blocks either show cached content or a "temporarily unavailable" /
"Generate digest" state. **The dashboard never goes down because the LLM is down or over quota.**
Background ingest classification is wrapped in its own `try/except`: a classification failure
never fails the ingest run that triggered it.

Locally, with no `GEMINI_API_KEY` set, everything above still works — the dashboard, charts, and
post table are fully functional, only the AI blocks show their null state.

Tests never hit the real Gemini API: `tests/conftest.py` breaks `google.genai.Client.__init__`
(on top of the existing `httpx` transport guard), and every AI test injects a fake `LlmClient`.
