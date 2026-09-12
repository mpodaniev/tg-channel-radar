from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest

from app.models import ChannelStatus
from app.web import background


@dataclass
class _FakeSession:
    pass


@asynccontextmanager
async def _fake_session_factory():
    yield _FakeSession()


def _stub_session_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(background, "async_session_factory", _fake_session_factory)


def _result(*, status: str = ChannelStatus.ACTIVE.value, posts_seen: int = 0) -> object:
    return type(
        "Result",
        (),
        {"status": status, "posts_seen": posts_seen},
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
