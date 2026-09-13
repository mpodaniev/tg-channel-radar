# 0002 — Facts vs. measurements: append-only metric snapshots

## Context

Telegram channel/post metrics (subscriber count, views, forwards, reactions) change continuously
between collection runs, and the dashboard needs to show *growth over time* (charts), not just the
latest value. Mixing a fact table's row with the metric that changes on every fetch would either
lose history (overwrite in place) or force awkward wide tables (one column per snapshot).

## Decision

- Facts and measurements live in separate tables: `channels`/`posts` hold identity and
  content that doesn't change on every fetch; `channel_snapshots`/`post_metric_snapshots` hold
  time-series measurements, one row per capture.
- Snapshot tables are **append-only**: a snapshot row is written only if the value actually
  changed relative to the latest existing snapshot for that same channel/post
  (`app/services/ingest.py:_snapshot_unchanged`). Without this dedup, a cron running every 30
  minutes against an inactive channel would still insert an identical row every run, bloating the
  tables with no new information.
- Posts are upserted idempotently: `INSERT ... ON CONFLICT (channel_id, message_id) DO UPDATE`
  (`app/services/ingest.py:_upsert_posts`), keyed on the post's natural key
  `(channel_id, message_id)` — the same page fetched twice, or a post that Telegram shows again in
  a later page, never creates a duplicate row. A `content_hash` over the mutable fields detects
  edits (Telegram allows editing a post after publishing) without a separate "revisions" table.

## Consequences

- Every dashboard chart is a query over a snapshot table ordered by `captured_at`, not a stored
  "current value" column — the current value is just the latest snapshot, derived rather than
  duplicated.
- Snapshot tables grow only when something actually changed, so a channel that reliably gets 100
  views per post keeps a small history; a channel with fast-changing metrics gets a denser one.
  Either way, growth is proportional to real activity, not to how often the cron runs.
- Re-running ingest against the same fetched HTML twice is safe and produces no visible side
  effects beyond the first run — this is what `tests/test_ingest.py`'s idempotency tests verify.
