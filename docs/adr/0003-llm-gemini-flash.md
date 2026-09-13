# 0003 — LLM provider: Gemini 2.0 Flash behind a single gateway

## Context

The dashboard's AI features (post classification, channel digests, anomaly explanations) are
purely additive: none of the core dashboard functionality (charts, post table, health badges)
should ever depend on an LLM being reachable, fast, or within quota. The project also runs on
Gemini's free tier, which has a real daily request quota.

## Decision

- All LLM access goes through one module, `app/services/ai/client.py`
  (`AiGateway`/`LlmClient`) — nothing else in the codebase imports `google.genai` directly. This
  is where every resilience concern lives: an empty `GEMINI_API_KEY` disables the layer entirely
  (no network attempted), a 20s timeout with 2 retries, a circuit breaker that opens after
  `AI_FAILURE_THRESHOLD` consecutive failures and stays open for `AI_COOLDOWN_MINUTES`, and a
  per-UTC-day call counter enforcing `AI_DAILY_CALL_BUDGET`.
- Every AI task (`app/services/ai/tasks.py`) catches `AiUnavailableError` and degrades to
  `None`/skip instead of propagating — a down or rate-limited LLM never breaks ingest or page
  rendering.
- `ai_auto_classify_enabled` is currently `False` (`app/config.py`): Gemini's free-tier daily quota
  (20 requests/day) was getting exhausted by automatic post classification running on every
  ingest, leaving no budget left for manually generating a digest during a demo. This is a
  deliberate quota trade-off, not a bug.
- The periodic `/internal/refresh` endpoint (cron-triggered every 30 minutes, up to 48 times/day)
  deliberately does **not** trigger AI classification of newly refreshed posts — only the initial
  ingest of a channel does. Running classification on every cron tick would compete for the same
  daily budget that manual digest generation depends on, and 48 runs/day would exhaust a 20-request
  quota almost immediately regardless of `ai_auto_classify_enabled`.

## Consequences

- The dashboard is fully usable with `GEMINI_API_KEY` unset or the quota exhausted: AI blocks show
  a "temporarily unavailable" / "Generate digest" null state instead of an error.
- Because auto-classification is off, most posts have no AI annotation unless a digest or
  explanation is explicitly requested — this is visible in the UI and expected, not a defect.
- If a future provider or quota tier changes this trade-off, the fix is one flag
  (`ai_auto_classify_enabled`) and one prompt-version bump if the schema changes — not a rewrite of
  call sites, since all call sites already treat AI results as optional.
