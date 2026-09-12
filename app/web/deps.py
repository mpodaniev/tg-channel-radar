from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Path, Query

from app.services import channels
from app.services.analytics import DEFAULT_PERIOD_DAYS, PERIOD_OPTIONS
from app.services.errors import InvalidChannelUsernameError
from app.web import background

__all__ = ["PERIOD_OPTIONS", "channel_username", "ingest_runner", "period_days"]

IngestRunner = Callable[[str], Awaitable[None]]


def period_days(days: int = Query(DEFAULT_PERIOD_DAYS)) -> int:
    if days not in PERIOD_OPTIONS:
        raise HTTPException(status_code=400, detail="Unsupported period")
    return days


def channel_username(username: str = Path(...)) -> str:
    try:
        return channels.normalize(username)
    except InvalidChannelUsernameError:
        raise HTTPException(status_code=404, detail="Channel not found") from None


def ingest_runner() -> IngestRunner:
    return background.run_first_ingest
