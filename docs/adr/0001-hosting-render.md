# 0001 — Hosting: everything on Render (Web Service + Postgres)

## Context

The project needs a publicly reachable URL and a Postgres database, both on free tiers, without
managing servers or containers directly. The app is a single ASGI process (FastAPI/uvicorn) with
no background workers beyond `BackgroundTasks` and a periodic HTTP-triggered refresh. This is a
test-assignment deployment with a short expected lifespan, not a long-running production service.

## Decision

- **Render** free Web Service runs `uvicorn app.main:app`, built straight from `requirements.txt`
  and the repo — no Docker image to maintain, ASGI apps are a first-class deploy target.
- **Render Postgres (free)**, in the same account and dashboard as the Web Service, rather than a
  separate provider (e.g. Neon). Render's free Postgres expires and is deleted after 90 days,
  which would be a real problem for a long-lived production database — but for a project expected
  to be torn down well before that (or re-deployed if it isn't), the one-account convenience of
  provisioning both pieces from the same `render.yaml` Blueprint outweighs that limitation.
- The web service and the database are wired together only via `DATABASE_URL`, so either side
  could be swapped for another provider later without touching application code — `DATABASE_URL`
  is normalized in `app/config.py` regardless of where it comes from.

## Consequences

- Render free services sleep after ~15 minutes of no traffic; the first request after sleeping
  pays a cold-start cost (roughly 30-50s). The GitHub Actions cron (`cron-refresh.yml`, every 30
  minutes) keeps the service warm during normal operation, but a demo right after a long idle
  period may still see one slow request.
- The free Postgres database is deleted 90 days after creation with no automatic renewal — if the
  project needs to outlive that window, the database must be recreated and `DATABASE_URL`
  re-pasted into the Render dashboard by hand.
- Render's Postgres connection string uses the plain `postgresql://` scheme; the app's async
  driver (asyncpg) requires `postgresql+asyncpg://`, so the scheme must be edited by hand after
  copying the connection string from the Render dashboard — see `render.yaml`'s comment on
  `DATABASE_URL`.
- Both pieces are single-instance free tiers: no horizontal scaling, no HA. Acceptable for a demo/
  assignment deployment, not for production traffic.
