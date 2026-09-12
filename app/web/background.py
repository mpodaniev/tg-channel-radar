import logging

from app.config import get_settings
from app.db import async_session_factory
from app.models import ChannelStatus
from app.services.ai import tasks as ai_tasks
from app.services.ingest import COLLECT_WINDOW_DAYS, SAFETY_MAX_POSTS, ingest_channel

logger = logging.getLogger(__name__)

FIRST_INGEST_MAX_POSTS = 20

# In-process only: an approximate "posts fetched so far" counter for channels
# currently in their deep-ingest pass, so the UI can show live progress before
# the pass commits its results. Lost on restart and not shared across workers.
_backfill_progress: dict[str, int] = {}


def get_backfill_progress(username: str) -> int | None:
    return _backfill_progress.get(username)


async def _classify_best_effort(username: str) -> None:
    if not get_settings().ai_auto_classify_enabled:
        return

    # Imported lazily: app.web.deps imports this module, so importing it back
    # at module load time would be circular.
    from app.web import deps

    async with async_session_factory() as session:
        try:
            await ai_tasks.classify_new_posts(session, username, client=deps.ai_client())
        except Exception:
            logger.exception("post classification failed for %s", username)


async def run_first_ingest(username: str, *, max_posts: int = FIRST_INGEST_MAX_POSTS) -> None:
    async with async_session_factory() as session:
        try:
            result = await ingest_channel(session, username, since_days=None, max_posts=max_posts)
        except Exception:
            logger.exception("first ingest failed for %s", username)
            return

    # The fast pass has no date window, so hitting its post cap is the only
    # signal that older posts may remain, and a deep pass is coming next.
    # Mark backfill progress *before* classification below so the row's
    # polling condition (`status == pending or backfill_progress is not
    # none`) stays true across the gap -- otherwise the row would render
    # without hx-trigger while classification is running (status is already
    # ACTIVE, progress not set yet) and stop polling for good, missing the
    # deep pass entirely.
    needs_deep_ingest = (
        result.status == ChannelStatus.ACTIVE.value and result.posts_seen >= max_posts
    )
    if needs_deep_ingest:
        _backfill_progress[username] = result.posts_seen

    if result.status == ChannelStatus.ACTIVE.value:
        await _classify_best_effort(username)

    # Chain a full-depth pass right away instead of waiting for the next cron
    # run, so the channel fills in without extra user action.
    if needs_deep_ingest:
        await _run_deep_ingest(username)


async def _run_deep_ingest(
    username: str,
    *,
    since_days: int = COLLECT_WINDOW_DAYS,
    max_posts: int = SAFETY_MAX_POSTS,
) -> None:
    async with async_session_factory() as session:
        try:
            result = await ingest_channel(
                session,
                username,
                since_days=since_days,
                max_posts=max_posts,
                on_progress=lambda count: _backfill_progress.__setitem__(username, count),
            )
        except Exception:
            logger.exception("deep ingest failed for %s", username)
            return
        finally:
            _backfill_progress.pop(username, None)

    if result.status == ChannelStatus.ACTIVE.value:
        await _classify_best_effort(username)
