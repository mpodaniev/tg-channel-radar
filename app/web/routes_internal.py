import secrets
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import get_settings
from app.db import async_session_factory
from app.services.refresh import refresh_due_channels

router = APIRouter(prefix="/internal", tags=["internal"])


def _check_refresh_token(x_refresh_token: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().refresh_token
    # Compared as bytes: compare_digest raises TypeError on non-ASCII str inputs,
    # and a bogus header should be a 401, not a 500.
    if x_refresh_token is None or not secrets.compare_digest(
        x_refresh_token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid or missing refresh token")


@router.post("/refresh", dependencies=[Depends(_check_refresh_token)])
async def refresh() -> dict[str, object]:
    result = await refresh_due_channels(async_session_factory)
    return asdict(result) | {"processed": result.processed, "failed": result.failed}
