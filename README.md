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
