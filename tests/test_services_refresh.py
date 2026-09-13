from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelStatus
from app.services import refresh
from app.services.ingest import IngestResult
from tests.factories import make_channel


def _ingest_result(status: str = ChannelStatus.ACTIVE.value) -> IngestResult:
    return IngestResult(
        channel_id=1,
        status=status,
        posts_seen=0,
        posts_created=0,
        posts_edited=0,
        post_snapshots_created=0,
        channel_snapshot_created=False,
        error=None,
    )


def _session_factory(session: AsyncSession):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


class _Recorder:
    def __init__(self, raises: dict[str, Exception] | None = None) -> None:
        self.raises = raises or {}
        self.calls: list[dict[str, object]] = []

    async def __call__(self, session, username, *, since_days, fetcher=None):
        self.calls.append({"username": username, "since_days": since_days, "fetcher": fetcher})
        error = self.raises.get(username)
        if error is not None:
            raise error
        return _ingest_result()


async def test_only_active_and_error_are_refreshed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_channel(db_session, username="active_ch", status=ChannelStatus.ACTIVE.value)
    await make_channel(db_session, username="error_ch", status=ChannelStatus.ERROR.value)
    await make_channel(db_session, username="pending_ch", status=ChannelStatus.PENDING.value)
    await make_channel(db_session, username="not_found_ch", status=ChannelStatus.NOT_FOUND.value)
    await make_channel(db_session, username="private_ch", status=ChannelStatus.PRIVATE.value)

    recorder = _Recorder()
    monkeypatch.setattr(refresh, "ingest_channel", recorder)

    result = await refresh.refresh_due_channels(_session_factory(db_session))

    refreshed = {call["username"] for call in recorder.calls}
    assert refreshed == {"active_ch", "error_ch"}
    assert result.processed == 2
    assert result.succeeded == 2


async def test_oldest_last_fetch_at_refreshed_first(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    await make_channel(
        db_session, username="recent", status=ChannelStatus.ACTIVE.value, last_fetch_at=now
    )
    await make_channel(
        db_session,
        username="never_fetched",
        status=ChannelStatus.ACTIVE.value,
        last_fetch_at=None,
    )
    await make_channel(
        db_session,
        username="oldest",
        status=ChannelStatus.ACTIVE.value,
        last_fetch_at=now - timedelta(days=10),
    )

    recorder = _Recorder()
    monkeypatch.setattr(refresh, "ingest_channel", recorder)

    await refresh.refresh_due_channels(_session_factory(db_session))

    order = [call["username"] for call in recorder.calls]
    assert order == ["never_fetched", "oldest", "recent"]


async def test_stops_when_time_budget_exceeded(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_channel(db_session, username="ch1", status=ChannelStatus.ACTIVE.value)
    await make_channel(db_session, username="ch2", status=ChannelStatus.ACTIVE.value)

    recorder = _Recorder()
    monkeypatch.setattr(refresh, "ingest_channel", recorder)

    clock_values = iter([0.0, 100.0, 100.0])

    def fake_clock() -> float:
        return next(clock_values)

    result = await refresh.refresh_due_channels(
        _session_factory(db_session), time_budget_seconds=60.0, clock=fake_clock
    )

    assert len(recorder.calls) == 0
    assert result.time_budget_exceeded is True
    assert result.processed == 0


async def test_one_channel_failure_does_not_stop_the_batch(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_channel(db_session, username="failing", status=ChannelStatus.ACTIVE.value)
    await make_channel(db_session, username="ok", status=ChannelStatus.ERROR.value)

    recorder = _Recorder(raises={"failing": RuntimeError("boom")})
    monkeypatch.setattr(refresh, "ingest_channel", recorder)

    result = await refresh.refresh_due_channels(_session_factory(db_session))

    assert result.processed == 2
    assert result.succeeded == 1
    assert result.failed == 1
    assert result.usernames_failed == ["failing"]


async def test_since_days_and_fetcher_are_passed_through(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_channel(db_session, username="active_ch", status=ChannelStatus.ACTIVE.value)

    recorder = _Recorder()
    monkeypatch.setattr(refresh, "ingest_channel", recorder)

    async def fake_fetcher(username, *, since, max_posts, on_progress):
        raise AssertionError("fetcher itself should never be invoked in this test")

    await refresh.refresh_due_channels(
        _session_factory(db_session), since_days=3, fetcher=fake_fetcher
    )

    assert recorder.calls[0]["since_days"] == 3
    assert recorder.calls[0]["fetcher"] is fake_fetcher


async def test_batch_limit_truncates_channels_refreshed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    for offset, username in enumerate(["oldest", "middle", "newest"]):
        await make_channel(
            db_session,
            username=username,
            status=ChannelStatus.ACTIVE.value,
            last_fetch_at=now - timedelta(days=5 - offset),
        )

    recorder = _Recorder()
    monkeypatch.setattr(refresh, "ingest_channel", recorder)

    result = await refresh.refresh_due_channels(_session_factory(db_session), batch_limit=1)

    assert [call["username"] for call in recorder.calls] == ["oldest"]
    assert result.processed == 1
