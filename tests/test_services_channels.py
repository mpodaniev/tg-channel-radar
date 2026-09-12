from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, ChannelSnapshot, ChannelStatus, Post, PostMetricSnapshot
from app.services import channels
from app.services.errors import (
    ChannelAlreadyExistsError,
    ChannelNotFoundInDbError,
    InvalidChannelUsernameError,
)
from tests.factories import add_channel_snapshot, add_metric, make_channel, make_post
from tests.helpers import count as _count


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("@Durov", "durov"),
        ("durov", "durov"),
        ("https://t.me/durov", "durov"),
        ("t.me/s/Durov", "durov"),
        ("  durov/ ", "durov"),
        ("DUROV", "durov"),
    ],
)
def test_normalize_accepts_valid_forms(raw: str, expected: str) -> None:
    assert channels.normalize(raw) == expected


@pytest.mark.parametrize("raw", ["", "-", "abc", "a" * 33, "dur ov", "durov!"])
def test_normalize_rejects_invalid_usernames(raw: str) -> None:
    with pytest.raises(InvalidChannelUsernameError):
        channels.normalize(raw)


async def test_get_or_create_channel_creates_pending_row(db_session: AsyncSession) -> None:
    channel = await channels.get_or_create_channel(db_session, "durov")

    assert channel.username == "durov"
    assert channel.status == ChannelStatus.PENDING.value
    assert await _count(db_session, Channel) == 1


async def test_get_or_create_channel_returns_existing_row(db_session: AsyncSession) -> None:
    existing = await make_channel(db_session, username="durov", status=ChannelStatus.ACTIVE.value)

    channel = await channels.get_or_create_channel(db_session, "durov")

    assert channel.id == existing.id
    assert channel.status == ChannelStatus.ACTIVE.value
    assert await _count(db_session, Channel) == 1


async def test_add_channel_creates_pending_row(db_session: AsyncSession) -> None:
    username = await channels.add_channel(db_session, "@Durov")

    assert username == "durov"
    result = await db_session.execute(select(Channel).where(Channel.username == "durov"))
    channel = result.scalar_one()
    assert channel.status == ChannelStatus.PENDING.value


async def test_add_channel_duplicate_raises(db_session: AsyncSession) -> None:
    await channels.add_channel(db_session, "durov")

    with pytest.raises(ChannelAlreadyExistsError):
        await channels.add_channel(db_session, "@Durov")

    assert await _count(db_session, Channel) == 1


async def test_get_overview_unknown_channel_raises(db_session: AsyncSession) -> None:
    with pytest.raises(ChannelNotFoundInDbError):
        await channels.get_overview(db_session, "nope")


async def test_get_overview_returns_pending_channel(db_session: AsyncSession) -> None:
    await channels.add_channel(db_session, "durov")

    overview = await channels.get_overview(db_session, "durov")

    assert overview.status == ChannelStatus.PENDING.value


async def test_mark_pending_resets_status_and_error(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session, username="durov", status=ChannelStatus.ERROR.value)
    channel.last_error = "boom"
    await db_session.flush()

    await channels.mark_pending(db_session, "durov")

    result = await db_session.execute(select(Channel).where(Channel.username == "durov"))
    updated = result.scalar_one()
    assert updated.status == ChannelStatus.PENDING.value
    assert updated.last_error is None


async def test_mark_pending_unknown_channel_raises(db_session: AsyncSession) -> None:
    with pytest.raises(ChannelNotFoundInDbError):
        await channels.mark_pending(db_session, "nope")


async def test_delete_channel_cascades(db_session: AsyncSession) -> None:
    now = datetime(2026, 9, 11, tzinfo=UTC)
    channel = await make_channel(db_session, username="durov")
    post = await make_post(db_session, channel, message_id=1, posted_at=now)
    await add_metric(db_session, post, views=10, captured_at=now)
    await add_channel_snapshot(db_session, channel, subscribers=100, captured_at=now)

    await channels.delete_channel(db_session, "durov")

    assert await _count(db_session, Channel) == 0
    assert await _count(db_session, Post) == 0
    assert await _count(db_session, PostMetricSnapshot) == 0
    assert await _count(db_session, ChannelSnapshot) == 0


async def test_delete_channel_unknown_raises(db_session: AsyncSession) -> None:
    with pytest.raises(ChannelNotFoundInDbError):
        await channels.delete_channel(db_session, "nope")
