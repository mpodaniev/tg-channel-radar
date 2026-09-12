import pytest

from app.web import deps


class _FakeSettings:
    def __init__(
        self,
        *,
        gemini_api_key: str = "",
        gemini_model: str = "gemini-test",
        ai_failure_threshold: int = 7,
        ai_cooldown_minutes: int = 42,
        ai_daily_call_budget: int = 13,
    ) -> None:
        self.gemini_api_key = gemini_api_key
        self.gemini_model = gemini_model
        self.ai_failure_threshold = ai_failure_threshold
        self.ai_cooldown_minutes = ai_cooldown_minutes
        self.ai_daily_call_budget = ai_daily_call_budget


@pytest.fixture(autouse=True)
def _clear_ai_gateway_cache():
    deps._ai_gateway.cache_clear()
    yield
    deps._ai_gateway.cache_clear()


def test_ai_gateway_built_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: _FakeSettings())

    gateway = deps.ai_client()

    assert gateway._failure_threshold == 7
    assert gateway._cooldown_minutes == 42
    assert gateway._daily_call_budget == 13


def test_ai_gateway_disabled_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: _FakeSettings(gemini_api_key=""))

    gateway = deps.ai_client()

    assert gateway.enabled is False
