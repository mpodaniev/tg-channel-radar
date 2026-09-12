from collections.abc import Awaitable, Callable
from functools import lru_cache

from fastapi import HTTPException, Path, Query

from app.config import get_settings
from app.services import channels
from app.services.ai.client import AiGateway, GeminiClient, LlmClient
from app.services.analytics import DEFAULT_PERIOD_DAYS, PERIOD_OPTIONS
from app.services.errors import InvalidChannelUsernameError
from app.web import background

__all__ = ["PERIOD_OPTIONS", "ai_client", "channel_username", "ingest_runner", "period_days"]

IngestRunner = Callable[[str], Awaitable[None]]


@lru_cache
def _ai_gateway() -> AiGateway:
    settings = get_settings()
    client: LlmClient | None = (
        GeminiClient(settings.gemini_api_key, model=settings.gemini_model)
        if settings.gemini_api_key
        else None
    )
    return AiGateway(
        client,
        failure_threshold=settings.ai_failure_threshold,
        cooldown_minutes=settings.ai_cooldown_minutes,
        daily_call_budget=settings.ai_daily_call_budget,
    )


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


def ai_client() -> LlmClient:
    return _ai_gateway()
