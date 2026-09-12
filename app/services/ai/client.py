import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol

from app.retry import backoff_delay
from app.services.errors import AiUnavailableError

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS: Final = 20
MAX_RETRIES: Final = 2
RETRY_BACKOFF_SECONDS: Final = 1.0


class LlmClient(Protocol):
    async def generate_json(self, prompt: str, *, schema: dict) -> Any: ...


class GeminiClient:
    def __init__(self, api_key: str, *, model: str) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    async def generate_json(self, prompt: str, *, schema: dict) -> Any:
        from google.genai import types

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        if response.text is None:
            raise ValueError("Gemini response had no text content")
        return json.loads(response.text)


class AiGateway:
    """Owns all resilience around raw LLM calls: timeout, retries, a circuit
    breaker, and a daily call budget. Instance-scoped state so tests never
    leak breaker/budget state between each other."""

    def __init__(
        self,
        client: LlmClient | None,
        *,
        failure_threshold: int,
        cooldown_minutes: int,
        daily_call_budget: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self.enabled = client is not None
        self._failure_threshold = failure_threshold
        self._cooldown_minutes = cooldown_minutes
        self._daily_call_budget = daily_call_budget
        self._now = now

        self._consecutive_failures = 0
        self._open_until: datetime | None = None
        self._budget_date = self._now().date()
        self._calls_today = 0

    def _reset_daily_budget_if_needed(self) -> None:
        today = self._now().date()
        if today != self._budget_date:
            self._budget_date = today
            self._calls_today = 0

    def _circuit_open(self) -> bool:
        return self._open_until is not None and self._now() < self._open_until

    async def generate_json(self, prompt: str, *, schema: dict) -> Any:
        if not self.enabled or self._client is None:
            raise AiUnavailableError("AI layer is disabled (no API key configured)")

        if self._circuit_open():
            raise AiUnavailableError("AI layer is temporarily unavailable (circuit open)")

        self._reset_daily_budget_if_needed()
        if self._calls_today >= self._daily_call_budget:
            raise AiUnavailableError("AI layer daily call budget exhausted")

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                    result = await self._client.generate_json(prompt, schema=schema)
                self._consecutive_failures = 0
                self._open_until = None
                self._calls_today += 1
                return result
            except Exception as exc:  # noqa: BLE001 - any failure counts toward the breaker
                last_exc = exc
                if attempt < MAX_RETRIES:
                    logger.warning("LLM call failed (attempt %d), retrying: %s", attempt + 1, exc)
                    await asyncio.sleep(backoff_delay(attempt, RETRY_BACKOFF_SECONDS))

        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._open_until = self._now() + timedelta(minutes=self._cooldown_minutes)
            logger.error(
                "AI circuit breaker opened for %d minutes after %d consecutive failures",
                self._cooldown_minutes,
                self._consecutive_failures,
            )

        logger.error("LLM call failed after %d retries: %s", MAX_RETRIES, last_exc)
        raise AiUnavailableError("AI layer call failed") from last_exc
