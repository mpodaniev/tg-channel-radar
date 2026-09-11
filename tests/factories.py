from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, ChannelSnapshot, ChannelStatus, Post, PostMetricSnapshot


async def make_channel(
    session: AsyncSession,
    *,
    username: str = "durov",
    status: str = ChannelStatus.ACTIVE.value,
    last_fetch_at: datetime | None = None,
    consecutive_failures: int = 0,
) -> Channel:
    channel = Channel(
        username=username,
        title=username.capitalize(),
        status=status,
        last_fetch_at=last_fetch_at,
        last_fetch_status=status,
        consecutive_failures=consecutive_failures,
    )
    session.add(channel)
    await session.flush()
    return channel


async def make_post(
    session: AsyncSession,
    channel: Channel,
    *,
    message_id: int,
    posted_at: datetime | None,
    text: str | None = "hello",
) -> Post:
    post = Post(
        channel_id=channel.id,
        message_id=message_id,
        posted_at=posted_at,
        text=text,
        has_media=False,
    )
    session.add(post)
    await session.flush()
    return post


async def add_metric(
    session: AsyncSession,
    post: Post,
    *,
    views: int | None = None,
    forwards: int | None = None,
    reactions_total: int | None = None,
    captured_at: datetime,
) -> PostMetricSnapshot:
    snapshot = PostMetricSnapshot(
        post_id=post.id,
        views=views,
        forwards=forwards,
        reactions_total=reactions_total,
        captured_at=captured_at,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def add_channel_snapshot(
    session: AsyncSession, channel: Channel, *, subscribers: int | None, captured_at: datetime
) -> ChannelSnapshot:
    snapshot = ChannelSnapshot(
        channel_id=channel.id, subscribers=subscribers, captured_at=captured_at
    )
    session.add(snapshot)
    await session.flush()
    return snapshot
