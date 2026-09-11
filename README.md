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
