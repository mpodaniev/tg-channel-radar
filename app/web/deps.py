from typing import Final

from fastapi import HTTPException, Path, Query

from app.parser.errors import InvalidUsernameError
from app.parser.normalize import normalize_username
from app.services.analytics import DEFAULT_PERIOD_DAYS

PERIOD_OPTIONS: Final[tuple[int, ...]] = (7, 30, 90)


def period_days(days: int = Query(DEFAULT_PERIOD_DAYS)) -> int:
    if days not in PERIOD_OPTIONS:
        raise HTTPException(status_code=400, detail="Unsupported period")
    return days


def channel_username(username: str = Path(...)) -> str:
    # TODO(stage-6): route this through services/channels.py once it exists.
    try:
        return normalize_username(username)
    except InvalidUsernameError:
        raise HTTPException(status_code=404, detail="Channel not found") from None
