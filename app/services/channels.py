from typing import cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, ChannelStatus
from app.parser.errors import InvalidUsernameError
from app.parser.normalize import normalize_username
from app.services import analytics
from app.services.analytics_types import ChannelOverview
from app.services.errors import (
    ChannelAlreadyExistsError,
    ChannelNotFoundInDbError,
    InvalidChannelUsernameError,
)


def normalize(raw: str) -> str:
    try:
        return normalize_username(raw)
    except InvalidUsernameError as exc:
        raise InvalidChannelUsernameError(str(exc)) from exc


async def _find_by_username(session: AsyncSession, username: str) -> Channel | None:
    result = await session.execute(select(Channel).where(Channel.username == username))
    return result.scalar_one_or_none()


async def get_channel_or_raise(session: AsyncSession, username: str) -> Channel:
    channel = await _find_by_username(session, username)
    if channel is None:
        raise ChannelNotFoundInDbError(f"channel not found: {username}")
    return channel


async def get_or_create_channel(session: AsyncSession, username: str) -> Channel:
    channel = await _find_by_username(session, username)
    if channel is not None:
        return channel

    channel = Channel(username=username, status=ChannelStatus.PENDING.value)
    session.add(channel)
    await session.flush()
    return channel


async def add_channel(session: AsyncSession, raw_username: str) -> str:
    username = normalize(raw_username)

    if await _find_by_username(session, username) is not None:
        raise ChannelAlreadyExistsError(f"channel already tracked: {username}")

    channel = Channel(username=username, status=ChannelStatus.PENDING.value)
    session.add(channel)
    await session.commit()
    return username


async def get_overview(session: AsyncSession, username: str) -> ChannelOverview:
    overviews = await analytics.list_channels_overview(session, usernames=[username])
    if not overviews:
        raise ChannelNotFoundInDbError(f"channel not found: {username}")
    return overviews[0]


async def mark_pending(session: AsyncSession, username: str) -> None:
    channel = await _find_by_username(session, username)
    if channel is None:
        raise ChannelNotFoundInDbError(f"channel not found: {username}")

    channel.status = ChannelStatus.PENDING.value
    channel.last_error = None
    await session.commit()


async def delete_channel(session: AsyncSession, username: str) -> None:
    result = cast(
        CursorResult, await session.execute(delete(Channel).where(Channel.username == username))
    )
    if result.rowcount == 0:
        raise ChannelNotFoundInDbError(f"channel not found: {username}")
    await session.commit()
