import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelStatus
from app.services import channels
from app.services.ingest import ChannelFetcher, ingest_channel

logger = logging.getLogger(__name__)

DEFAULT_TIME_BUDGET_SECONDS = 60.0
DEFAULT_BATCH_LIMIT = 200
REFRESH_WINDOW_DAYS = 3  # small window vs. first ingest, to stay within the cron time budget
REFRESHABLE_STATUSES = (ChannelStatus.ACTIVE.value, ChannelStatus.ERROR.value)


@dataclass(frozen=True)
class RefreshBatchResult:
    succeeded: int
    time_budget_exceeded: bool
    elapsed_seconds: float
    usernames_failed: list[str] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return len(self.usernames_failed)

    @property
    def processed(self) -> int:
        return self.succeeded + self.failed


async def refresh_due_channels(
    session_factory: Callable[[], AsyncSession],
    *,
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
    batch_limit: int = DEFAULT_BATCH_LIMIT,
    since_days: int = REFRESH_WINDOW_DAYS,
    fetcher: ChannelFetcher | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> RefreshBatchResult:
    started = clock()

    async with session_factory() as list_session:
        due = await channels.list_refreshable(
            list_session, statuses=REFRESHABLE_STATUSES, limit=batch_limit
        )

    succeeded = 0
    usernames_failed: list[str] = []
    time_budget_exceeded = False

    for username in due:
        if clock() - started >= time_budget_seconds:
            time_budget_exceeded = True
            break

        async with session_factory() as session:
            try:
                result = await ingest_channel(
                    session, username, since_days=since_days, fetcher=fetcher
                )
                ok = result.status == ChannelStatus.ACTIVE.value
            except Exception:
                logger.exception("periodic refresh failed for %s", username)
                ok = False

        if ok:
            succeeded += 1
        else:
            usernames_failed.append(username)

    return RefreshBatchResult(
        succeeded=succeeded,
        time_budget_exceeded=time_budget_exceeded,
        elapsed_seconds=round(clock() - started, 1),
        usernames_failed=usernames_failed,
    )
