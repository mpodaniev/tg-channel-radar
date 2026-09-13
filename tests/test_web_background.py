from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest

from app.models import ChannelStatus
from app.web import background, deps


@dataclass
class _FakeSession:
    pass


@asynccontextmanager
async def _fake_session_factory():
    yield _FakeSession()


def _stub_session_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(background, "async_session_factory", _fake_session_factory)


def _result(
    *, status: str = ChannelStatus.ACTIVE.value, posts_seen: int = 0, channel_id: int = 1
) -> object:
    return type(
        "Result",
        (),
        {"status": status, "posts_seen": posts_seen, "channel_id": channel_id},
    )()


class _Recorder:
    def __init__(self, results: list[object] | Exception) -> None:
        self._results = results
        self.calls: list[dict[str, object]] = []
        self.progress_at_call: list[int | None] = []

    async def __call__(
        self,
        session,
        username,
        *,
        since_days,
        max_posts,
        on_progress: Callable[[int], None] | None = None,
    ):
        self.progress_at_call.append(background.get_backfill_progress(username))
        self.calls.append({"since_days": since_days, "max_posts": max_posts})
        if on_progress is not None:
            on_progress(77)
        if isinstance(self._results, Exception):
            raise self._results
        return self._results[len(self.calls) - 1]


async def test_full_first_page_chains_deep_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_session_factory(monkeypatch)
    recorder = _Recorder([_result(posts_seen=20), _result(posts_seen=5)])
    monkeypatch.setattr(background, "ingest_channel", recorder)

    await background.run_first_ingest("durov", max_posts=20)

    assert len(recorder.calls) == 2
    assert recorder.calls[0] == {"since_days": None, "max_posts": 20}
    assert recorder.calls[1] == {
        "since_days": background.COLLECT_WINDOW_DAYS,
        "max_posts": background.SAFETY_MAX_POSTS,
    }
    # progress is seeded with the fast pass's count before the deep call starts
    assert recorder.progress_at_call[1] == 20
    # the recorder's on_progress call updates the shared progress store
    assert background.get_backfill_progress("durov") is None


async def test_short_first_page_skips_deep_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_session_factory(monkeypatch)
    recorder = _Recorder([_result(posts_seen=5)])
    monkeypatch.setattr(background, "ingest_channel", recorder)

    await background.run_first_ingest("durov", max_posts=20)

    assert len(recorder.calls) == 1
    assert background.get_backfill_progress("durov") is None


async def test_failed_first_ingest_skips_deep_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_session_factory(monkeypatch)
    recorder = _Recorder(RuntimeError("boom"))
    monkeypatch.setattr(background, "ingest_channel", recorder)

    await background.run_first_ingest("durov", max_posts=20)

    assert len(recorder.calls) == 1
    assert background.get_backfill_progress("durov") is None


class _FakeSettings:
    def __init__(self, *, ai_auto_classify_enabled: bool) -> None:
        self.ai_auto_classify_enabled = ai_auto_classify_enabled


async def test_classify_best_effort_skipped_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        background, "get_settings", lambda: _FakeSettings(ai_auto_classify_enabled=False)
    )
    calls: list[str] = []

    async def _fake_classify_new_posts(session, username, *, client):
        calls.append(username)

    monkeypatch.setattr(background.ai_tasks, "classify_new_posts", _fake_classify_new_posts)

    await background._classify_best_effort("durov")

    assert calls == []


async def test_classify_best_effort_runs_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_session_factory(monkeypatch)
    monkeypatch.setattr(
        background, "get_settings", lambda: _FakeSettings(ai_auto_classify_enabled=True)
    )
    monkeypatch.setattr(deps, "ai_client", lambda: object())
    calls: list[str] = []

    async def _fake_classify_new_posts(session, username, *, client):
        calls.append(username)

    monkeypatch.setattr(background.ai_tasks, "classify_new_posts", _fake_classify_new_posts)

    await background._classify_best_effort("durov")

    assert calls == ["durov"]


async def test_explain_anomalies_best_effort_skipped_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        background, "get_settings", lambda: _FakeSettings(ai_auto_classify_enabled=False)
    )
    calls: list[int] = []

    async def _fake_detect(session, channel_id, **kwargs):
        calls.append(channel_id)
        return []

    monkeypatch.setattr(background.analytics, "detect_channel_anomalies", _fake_detect)

    await background._explain_anomalies_best_effort("durov", 1)

    assert calls == []


async def test_explain_anomalies_best_effort_runs_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_session_factory(monkeypatch)
    monkeypatch.setattr(
        background, "get_settings", lambda: _FakeSettings(ai_auto_classify_enabled=True)
    )
    monkeypatch.setattr(deps, "ai_client", lambda: object())

    async def _fake_detect(session, channel_id, **kwargs):
        return [(1, "post text", "spike", 4.2)]

    explain_calls: list[object] = []

    async def _fake_explain(session, anomalies, *, client):
        explain_calls.append(anomalies)
        return {}

    monkeypatch.setattr(background.analytics, "detect_channel_anomalies", _fake_detect)
    monkeypatch.setattr(background.ai_tasks, "explain_anomalies", _fake_explain)

    await background._explain_anomalies_best_effort("durov", 1)

    assert explain_calls == [[(1, "post text", "spike", 4.2)]]


async def test_explain_anomalies_best_effort_skips_llm_call_when_no_anomalies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_session_factory(monkeypatch)
    monkeypatch.setattr(
        background, "get_settings", lambda: _FakeSettings(ai_auto_classify_enabled=True)
    )
    monkeypatch.setattr(deps, "ai_client", lambda: object())

    async def _fake_detect(session, channel_id, **kwargs):
        return []

    async def _fake_explain(session, anomalies, *, client):
        raise AssertionError("explain_anomalies should not be called with an empty list")

    monkeypatch.setattr(background.analytics, "detect_channel_anomalies", _fake_detect)
    monkeypatch.setattr(background.ai_tasks, "explain_anomalies", _fake_explain)

    await background._explain_anomalies_best_effort("durov", 1)
