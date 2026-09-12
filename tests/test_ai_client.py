import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.services.ai import client as client_module
from app.services.ai.client import AiGateway
from app.services.errors import AiUnavailableError


class FakeLlmClient:
    def __init__(self, behaviors: list) -> None:
        self._behaviors = list(behaviors)
        self.calls = 0

    async def generate_json(self, prompt: str, *, schema: dict) -> object:
        self.calls += 1
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        if behavior == "sleep":
            await asyncio.sleep(1)
        return behavior


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.value = start

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs) -> None:
        self.value += timedelta(**kwargs)


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "RETRY_BACKOFF_SECONDS", 0.001)


def _gateway(
    client,
    *,
    failure_threshold: int = 3,
    cooldown_minutes: int = 10,
    daily_call_budget: int = 200,
    now=None,
) -> AiGateway:
    return AiGateway(
        client,
        failure_threshold=failure_threshold,
        cooldown_minutes=cooldown_minutes,
        daily_call_budget=daily_call_budget,
        now=now or (lambda: datetime(2026, 9, 12, tzinfo=UTC)),
    )


async def test_succeeds_on_first_try() -> None:
    fake = FakeLlmClient([{"ok": True}])
    gateway = _gateway(fake)

    result = await gateway.generate_json("prompt", schema={})

    assert result == {"ok": True}
    assert fake.calls == 1


async def test_retries_and_succeeds_on_second_attempt() -> None:
    fake = FakeLlmClient([RuntimeError("boom"), {"ok": True}])
    gateway = _gateway(fake)

    result = await gateway.generate_json("prompt", schema={})

    assert result == {"ok": True}
    assert fake.calls == 2


async def test_exhausted_retries_raise_ai_unavailable() -> None:
    fake = FakeLlmClient([RuntimeError("a"), RuntimeError("b"), RuntimeError("c")])
    gateway = _gateway(fake)

    with pytest.raises(AiUnavailableError):
        await gateway.generate_json("prompt", schema={})

    assert fake.calls == 3


async def test_timeout_counts_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "REQUEST_TIMEOUT_SECONDS", 0.01)
    fake = FakeLlmClient(["sleep", "sleep", "sleep"])
    gateway = _gateway(fake)

    with pytest.raises(AiUnavailableError):
        await gateway.generate_json("prompt", schema={})

    assert fake.calls == 3


async def test_circuit_breaker_opens_after_threshold_and_skips_calls() -> None:
    fake = FakeLlmClient(
        [
            RuntimeError("a"),
            RuntimeError("b"),
            RuntimeError("c"),
            RuntimeError("d"),
            RuntimeError("e"),
            RuntimeError("f"),
        ]
    )
    gateway = _gateway(fake, failure_threshold=2)

    with pytest.raises(AiUnavailableError):
        await gateway.generate_json("prompt", schema={})  # 1st failed call -> 1 consecutive failure
    assert fake.calls == 3  # initial + 2 retries

    with pytest.raises(AiUnavailableError):
        await gateway.generate_json("prompt", schema={})  # 2nd failed call -> breaker opens
    assert fake.calls == 6

    # circuit is now open: a 3rd call must not touch the client at all
    with pytest.raises(AiUnavailableError):
        await gateway.generate_json("prompt", schema={})
    assert fake.calls == 6


async def test_circuit_breaker_closes_after_cooldown() -> None:
    clock = FakeClock(datetime(2026, 9, 12, tzinfo=UTC))
    fake = FakeLlmClient([RuntimeError("a"), RuntimeError("b"), RuntimeError("c"), {"ok": True}])
    gateway = _gateway(fake, failure_threshold=1, cooldown_minutes=10, now=clock)

    with pytest.raises(AiUnavailableError):
        await gateway.generate_json("prompt", schema={})  # 3 failed attempts, opens the breaker
    assert fake.calls == 3

    with pytest.raises(AiUnavailableError):
        await gateway.generate_json("prompt", schema={})  # still open, no client call
    assert fake.calls == 3

    clock.advance(minutes=11)

    result = await gateway.generate_json("prompt", schema={})
    assert result == {"ok": True}
    assert fake.calls == 4


async def test_disabled_when_no_client_configured() -> None:
    gateway = _gateway(None)

    with pytest.raises(AiUnavailableError):
        await gateway.generate_json("prompt", schema={})
    assert gateway.enabled is False


async def test_daily_budget_exhausted_skips_call() -> None:
    fake = FakeLlmClient([{"ok": True}])
    gateway = _gateway(fake, daily_call_budget=0)

    with pytest.raises(AiUnavailableError):
        await gateway.generate_json("prompt", schema={})
    assert fake.calls == 0


async def test_daily_budget_resets_on_new_utc_day() -> None:
    clock = FakeClock(datetime(2026, 9, 12, 23, 59, tzinfo=UTC))
    fake = FakeLlmClient([{"ok": True}, {"ok": True}])
    gateway = _gateway(fake, daily_call_budget=1, now=clock)

    result = await gateway.generate_json("prompt", schema={})
    assert result == {"ok": True}

    with pytest.raises(AiUnavailableError):
        await gateway.generate_json("prompt", schema={})
    assert fake.calls == 1

    clock.advance(hours=1)  # crosses into the next UTC day

    result = await gateway.generate_json("prompt", schema={})
    assert result == {"ok": True}
    assert fake.calls == 2
