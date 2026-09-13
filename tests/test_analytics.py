from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import analytics
from app.services.errors import ChannelNotFoundInDbError, InvalidPeriodError, PostNotFoundError
from tests.factories import add_channel_snapshot, add_metric, make_channel, make_post

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


async def _make_channel(session: AsyncSession, **kwargs) -> object:
    kwargs.setdefault("last_fetch_at", NOW)
    return await make_channel(session, **kwargs)


async def test_uses_latest_metric_snapshot_not_first_or_sum(db_session: AsyncSession) -> None:
    channel = await _make_channel(db_session)
    post = await make_post(db_session, channel, message_id=1, posted_at=NOW)
    await add_metric(db_session, post, views=100, captured_at=NOW - timedelta(hours=2))
    await add_metric(db_session, post, views=250, captured_at=NOW - timedelta(hours=1))

    result = await analytics.get_channel_analytics(db_session, channel.username, now=NOW)

    assert result.posts[0].views == 250


async def test_post_outside_period_excluded_from_posts_in_period_but_counted_in_total(
    db_session: AsyncSession,
) -> None:
    channel = await _make_channel(db_session)
    await make_post(db_session, channel, message_id=1, posted_at=NOW - timedelta(days=100))
    await make_post(db_session, channel, message_id=2, posted_at=NOW - timedelta(days=1))

    result = await analytics.get_channel_analytics(db_session, channel.username, days=30, now=NOW)

    assert result.overview.posts_total == 2
    assert result.overview.posts_in_period == 1


async def test_channel_without_snapshots_has_none_metrics(db_session: AsyncSession) -> None:
    channel = await _make_channel(db_session)

    result = await analytics.get_channel_analytics(db_session, channel.username, now=NOW)

    assert result.overview.avg_views is None
    assert result.overview.subscribers is None


async def test_subscribers_trend_sorted_ascending_by_captured_at(db_session: AsyncSession) -> None:
    channel = await _make_channel(db_session)
    await add_channel_snapshot(
        db_session, channel, subscribers=200, captured_at=NOW - timedelta(days=1)
    )
    await add_channel_snapshot(
        db_session, channel, subscribers=100, captured_at=NOW - timedelta(days=5)
    )

    result = await analytics.get_channel_analytics(db_session, channel.username, days=30, now=NOW)

    captured_ats = [point.captured_at for point in result.subscribers_trend]
    assert captured_ats == sorted(captured_ats)
    assert result.overview.subscribers_growth is not None
    assert result.overview.subscribers_growth.absolute == 100


async def test_anomalous_post_is_flagged(db_session: AsyncSession) -> None:
    channel = await _make_channel(db_session)
    for i in range(6):
        post = await make_post(
            db_session, channel, message_id=i, posted_at=NOW - timedelta(hours=6 - i)
        )
        await add_metric(db_session, post, views=100, captured_at=post.posted_at)

    viral_post = await make_post(db_session, channel, message_id=100, posted_at=NOW)
    await add_metric(db_session, viral_post, views=50_000, captured_at=NOW)

    result = await analytics.get_channel_analytics(db_session, channel.username, days=30, now=NOW)

    flagged = {p.post_id: p.anomaly for p in result.posts if p.anomaly is not None}
    assert viral_post.id in flagged
    assert len(flagged) == 1


async def test_detect_channel_anomalies_flags_viral_post_with_text(
    db_session: AsyncSession,
) -> None:
    channel = await _make_channel(db_session)
    for i in range(6):
        post = await make_post(
            db_session, channel, message_id=i, posted_at=NOW - timedelta(hours=6 - i)
        )
        await add_metric(db_session, post, views=100, captured_at=post.posted_at)

    viral_post = await make_post(db_session, channel, message_id=100, posted_at=NOW, text="viral")
    await add_metric(db_session, viral_post, views=50_000, captured_at=NOW)

    anomalies = await analytics.detect_channel_anomalies(db_session, channel.id)

    assert len(anomalies) == 1
    post_id, text, direction, score = anomalies[0]
    assert post_id == viral_post.id
    assert text == "viral"
    assert direction == "spike"
    assert score > 0


async def test_detect_channel_anomalies_skips_posts_without_text(
    db_session: AsyncSession,
) -> None:
    channel = await _make_channel(db_session)
    for i in range(6):
        post = await make_post(
            db_session, channel, message_id=i, posted_at=NOW - timedelta(hours=6 - i)
        )
        await add_metric(db_session, post, views=100, captured_at=post.posted_at)

    viral_post = await make_post(db_session, channel, message_id=100, posted_at=NOW, text=None)
    await add_metric(db_session, viral_post, views=50_000, captured_at=NOW)

    anomalies = await analytics.detect_channel_anomalies(db_session, channel.id)

    assert anomalies == []


async def test_unknown_username_raises(db_session: AsyncSession) -> None:
    with pytest.raises(ChannelNotFoundInDbError):
        await analytics.get_channel_analytics(db_session, "does-not-exist", now=NOW)


async def test_invalid_period_raises(db_session: AsyncSession) -> None:
    channel = await _make_channel(db_session)

    with pytest.raises(InvalidPeriodError):
        await analytics.get_channel_analytics(db_session, channel.username, days=0, now=NOW)


async def test_get_post_analytics_returns_growth_in_chronological_order(
    db_session: AsyncSession,
) -> None:
    channel = await _make_channel(db_session)
    post = await make_post(db_session, channel, message_id=1, posted_at=NOW, text="full text")
    await add_metric(db_session, post, views=10, captured_at=NOW - timedelta(hours=2))
    await add_metric(db_session, post, views=50, captured_at=NOW - timedelta(hours=1))
    await add_metric(db_session, post, views=90, captured_at=NOW)

    result = await analytics.get_post_analytics(db_session, post.id)

    assert result.channel_username == channel.username
    assert result.channel_title == channel.title
    assert result.text == "full text"
    assert result.post.text_preview == "full text"
    assert [point.value for point in result.growth] == [10, 50, 90]
    assert [point.captured_at for point in result.growth] == sorted(
        point.captured_at for point in result.growth
    )


async def test_get_post_analytics_unknown_id_raises(db_session: AsyncSession) -> None:
    with pytest.raises(PostNotFoundError):
        await analytics.get_post_analytics(db_session, 999_999)


async def test_get_post_analytics_full_text_not_truncated(db_session: AsyncSession) -> None:
    channel = await _make_channel(db_session)
    raw_text = "word " * 60
    post = await make_post(db_session, channel, message_id=1, posted_at=NOW, text=raw_text)

    result = await analytics.get_post_analytics(db_session, post.id)

    assert result.text == raw_text
    assert len(result.post.text_preview) == 160
    assert len(result.text) > 160


async def test_get_post_analytics_normalizes_and_truncates_text_preview(
    db_session: AsyncSession,
) -> None:
    channel = await _make_channel(db_session)
    raw_text = "word " * 40 + "\n\n  extra   whitespace   " + "tail " * 10
    post = await make_post(db_session, channel, message_id=1, posted_at=NOW, text=raw_text)

    result = await analytics.get_post_analytics(db_session, post.id)

    expected_preview = " ".join(raw_text.split())[:160]
    assert result.post.text_preview == expected_preview
    assert len(result.post.text_preview) <= 160
    assert "  " not in result.post.text_preview
    assert "\n" not in result.post.text_preview


async def test_list_channels_overview_covers_multiple_channels(db_session: AsyncSession) -> None:
    channel_a = await _make_channel(db_session, username="channel_a")
    channel_b = await _make_channel(db_session, username="channel_b")
    post_a = await make_post(db_session, channel_a, message_id=1, posted_at=NOW)
    await add_metric(db_session, post_a, views=100, captured_at=NOW)

    overviews = await analytics.list_channels_overview(db_session, now=NOW)

    by_username = {o.username: o for o in overviews}
    assert by_username[channel_a.username].posts_total == 1
    assert by_username[channel_b.username].posts_total == 0
