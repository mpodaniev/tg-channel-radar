from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import Row, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, ChannelSnapshot, Post, PostMetricSnapshot
from app.services import stats
from app.services.analytics_types import (
    ChannelAnalytics,
    ChannelOverview,
    PostAnalytics,
    PostSummary,
    TrendPoint,
)
from app.services.db_queries import latest_post_metric_snapshot_subquery
from app.services.errors import ChannelNotFoundInDbError, InvalidPeriodError, PostNotFoundError

DEFAULT_PERIOD_DAYS = 30
PERIOD_OPTIONS: Final[tuple[int, ...]] = (7, 30, 90)
_TEXT_PREVIEW_LENGTH = 160


def _text_preview(text: str | None, *, length: int = _TEXT_PREVIEW_LENGTH) -> str | None:
    if text is None:
        return None
    return " ".join(text.split())[:length]


def _post_row_to_summary(row: Row, anomaly: stats.Anomaly | None = None) -> PostSummary:
    return PostSummary(
        post_id=row.post_id,
        message_id=row.message_id,
        posted_at=row.posted_at,
        text_preview=_text_preview(row.text),
        has_media=row.has_media,
        media_type=row.media_type,
        views=row.views,
        forwards=row.forwards,
        reactions_total=row.reactions_total,
        reaction_rate=stats.safe_ratio(row.reactions_total, row.views),
        forward_rate=stats.safe_ratio(row.forwards, row.views),
        anomaly=anomaly,
    )


def _build_channel_overview(
    channel: Channel,
    *,
    posts_total: int,
    period_posts: Sequence[Row],
    subs_rows: Sequence[Row],
    period_start: datetime,
    now: datetime,
) -> ChannelOverview:
    health = stats.source_health(
        status=channel.status,
        last_fetch_at=channel.last_fetch_at,
        consecutive_failures=channel.consecutive_failures,
        now=now,
    )
    current_subscribers = subs_rows[-1].subscribers if subs_rows else None
    period_subs = [row for row in subs_rows if row.captured_at >= period_start]
    subscribers_growth = (
        stats.growth(period_subs[0].subscribers, period_subs[-1].subscribers)
        if period_subs
        else None
    )
    return ChannelOverview(
        channel_id=channel.id,
        username=channel.username,
        title=channel.title,
        status=channel.status,
        health=health,
        subscribers=current_subscribers,
        subscribers_growth=subscribers_growth,
        posts_total=posts_total,
        posts_in_period=len(period_posts),
        avg_views=stats.mean_or_none(row.views for row in period_posts),
        median_views=stats.median_or_none(row.views for row in period_posts),
        avg_reaction_rate=stats.mean_or_none(
            stats.safe_ratio(row.reactions_total, row.views) for row in period_posts
        ),
        avg_forward_rate=stats.mean_or_none(
            stats.safe_ratio(row.forwards, row.views) for row in period_posts
        ),
        last_fetch_at=channel.last_fetch_at,
        consecutive_failures=channel.consecutive_failures,
        last_error=channel.last_error,
    )


async def get_channel_analytics(
    session: AsyncSession,
    username: str,
    *,
    days: int = DEFAULT_PERIOD_DAYS,
    now: datetime | None = None,
) -> ChannelAnalytics:
    if days <= 0:
        raise InvalidPeriodError(f"days must be positive, got {days}")

    now = now or datetime.now(UTC)
    period_start = now - timedelta(days=days)

    channel_result = await session.execute(select(Channel).where(Channel.username == username))
    channel = channel_result.scalar_one_or_none()
    if channel is None:
        raise ChannelNotFoundInDbError(f"channel not found: {username}")

    latest_metrics = latest_post_metric_snapshot_subquery(
        select(Post.id).where(Post.channel_id == channel.id)
    )
    posts_result = await session.execute(
        select(
            Post.id.label("post_id"),
            Post.message_id,
            Post.posted_at,
            Post.text,
            Post.has_media,
            Post.media_type,
            PostMetricSnapshot.views,
            PostMetricSnapshot.forwards,
            PostMetricSnapshot.reactions_total,
        )
        .select_from(Post)
        .outerjoin(latest_metrics, latest_metrics.c.post_id == Post.id)
        .outerjoin(PostMetricSnapshot, PostMetricSnapshot.id == latest_metrics.c.latest_id)
        .where(Post.channel_id == channel.id)
        .order_by(desc(Post.posted_at))
    )
    all_posts = posts_result.all()

    subs_result = await session.execute(
        select(ChannelSnapshot.captured_at, ChannelSnapshot.subscribers)
        .where(ChannelSnapshot.channel_id == channel.id, ChannelSnapshot.subscribers.is_not(None))
        .order_by(ChannelSnapshot.captured_at)
    )
    subs_rows = subs_result.all()

    posts_total = len(all_posts)

    period_posts = [
        row for row in all_posts if row.posted_at is not None and row.posted_at >= period_start
    ]
    anomaly_window = all_posts[: stats.ANOMALY_WINDOW]
    anomalies = stats.detect_anomalies([row.views for row in anomaly_window])
    anomaly_by_post_id = {anomaly_window[a.index].post_id: a for a in anomalies}

    post_summaries = [
        _post_row_to_summary(row, anomaly_by_post_id.get(row.post_id)) for row in period_posts
    ]

    period_subs = [row for row in subs_rows if row.captured_at >= period_start]
    subscribers_trend = [
        TrendPoint(captured_at=row.captured_at, value=row.subscribers) for row in period_subs
    ]

    overview = _build_channel_overview(
        channel,
        posts_total=posts_total,
        period_posts=period_posts,
        subs_rows=subs_rows,
        period_start=period_start,
        now=now,
    )

    daily = stats.daily_points(
        [(row.posted_at, row.views) for row in period_posts if row.posted_at is not None],
        start=period_start.date(),
        end=now.date(),
    )

    return ChannelAnalytics(
        overview=overview,
        period_days=days,
        subscribers_trend=subscribers_trend,
        daily=daily,
        posts=post_summaries,
    )


async def list_channels_overview(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    usernames: Sequence[str] | None = None,
) -> list[ChannelOverview]:
    now = now or datetime.now(UTC)
    period_start = now - timedelta(days=DEFAULT_PERIOD_DAYS)

    channels_query = select(Channel)
    if usernames is not None:
        channels_query = channels_query.where(Channel.username.in_(usernames))
    channels_result = await session.execute(channels_query)
    channels = channels_result.scalars().all()
    if not channels:
        return []
    channel_ids = [channel.id for channel in channels]

    totals_result = await session.execute(
        select(Post.channel_id, func.count().label("total"))
        .where(Post.channel_id.in_(channel_ids))
        .group_by(Post.channel_id)
    )
    posts_total_by_channel = {row.channel_id: row.total for row in totals_result}

    period_post_ids = select(Post.id).where(
        Post.channel_id.in_(channel_ids), Post.posted_at >= period_start
    )
    latest_metrics = latest_post_metric_snapshot_subquery(period_post_ids)
    period_posts_result = await session.execute(
        select(
            Post.channel_id,
            Post.id.label("post_id"),
            PostMetricSnapshot.views,
            PostMetricSnapshot.forwards,
            PostMetricSnapshot.reactions_total,
        )
        .select_from(Post)
        .outerjoin(latest_metrics, latest_metrics.c.post_id == Post.id)
        .outerjoin(PostMetricSnapshot, PostMetricSnapshot.id == latest_metrics.c.latest_id)
        .where(Post.channel_id.in_(channel_ids), Post.posted_at >= period_start)
    )
    period_posts_by_channel: dict[int, list[Row]] = defaultdict(list)
    for post_row in period_posts_result:
        period_posts_by_channel[post_row.channel_id].append(post_row)

    subs_result = await session.execute(
        select(ChannelSnapshot.channel_id, ChannelSnapshot.captured_at, ChannelSnapshot.subscribers)
        .where(
            ChannelSnapshot.channel_id.in_(channel_ids), ChannelSnapshot.subscribers.is_not(None)
        )
        .order_by(ChannelSnapshot.channel_id, ChannelSnapshot.captured_at)
    )
    subs_by_channel: dict[int, list[Row]] = defaultdict(list)
    for subs_row in subs_result:
        subs_by_channel[subs_row.channel_id].append(subs_row)

    overviews = []
    for channel in channels:
        period_posts = period_posts_by_channel.get(channel.id, [])
        subs_rows = subs_by_channel.get(channel.id, [])
        overviews.append(
            _build_channel_overview(
                channel,
                posts_total=posts_total_by_channel.get(channel.id, 0),
                period_posts=period_posts,
                subs_rows=subs_rows,
                period_start=period_start,
                now=now,
            )
        )
    return overviews


async def get_post_analytics(session: AsyncSession, post_id: int) -> PostAnalytics:
    latest_metrics = latest_post_metric_snapshot_subquery([post_id])
    result = await session.execute(
        select(
            Post.id.label("post_id"),
            Post.message_id,
            Post.posted_at,
            Post.text,
            Post.has_media,
            Post.media_type,
            Channel.username.label("channel_username"),
            Channel.title.label("channel_title"),
            PostMetricSnapshot.views,
            PostMetricSnapshot.forwards,
            PostMetricSnapshot.reactions_total,
        )
        .select_from(Post)
        .join(Channel, Channel.id == Post.channel_id)
        .outerjoin(latest_metrics, latest_metrics.c.post_id == Post.id)
        .outerjoin(PostMetricSnapshot, PostMetricSnapshot.id == latest_metrics.c.latest_id)
        .where(Post.id == post_id)
    )
    row = result.one_or_none()
    if row is None:
        raise PostNotFoundError(f"post not found: {post_id}")

    growth_result = await session.execute(
        select(PostMetricSnapshot.captured_at, PostMetricSnapshot.views)
        .where(PostMetricSnapshot.post_id == post_id, PostMetricSnapshot.views.is_not(None))
        .order_by(PostMetricSnapshot.captured_at)
    )
    growth_points = [TrendPoint(captured_at=r.captured_at, value=r.views) for r in growth_result]

    post_summary = _post_row_to_summary(row)
    return PostAnalytics(
        post=post_summary,
        channel_username=row.channel_username,
        text=row.text,
        channel_title=row.channel_title,
        growth=growth_points,
    )
