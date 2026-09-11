from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, ChannelSnapshot, ChannelStatus, Post, PostMetricSnapshot
from app.parser.errors import ChannelNotFoundError, FetchError
from app.parser.normalize import parse_channel_page
from app.parser.types import ParsedChannel, ParsedPost
from app.services.errors import InvalidChannelUsernameError
from app.services.ingest import ChannelFetcher, ingest_channel
from tests.helpers import load_fixture


def _base_channel() -> ParsedChannel:
    return parse_channel_page(load_fixture("channel_basic.html"), username="durov")


def _minimal_channel(post: ParsedPost) -> ParsedChannel:
    return ParsedChannel(
        username="durov",
        title="Durov",
        description=None,
        avatar_url=None,
        subscribers=100,
        photos_count=None,
        videos_count=None,
        links_count=None,
        posts=[post],
    )


def _fetcher(result: ParsedChannel | Exception) -> ChannelFetcher:
    async def fetch(username: str, *, max_posts: int) -> ParsedChannel:
        if isinstance(result, Exception):
            raise result
        return result

    return fetch


async def _count(session: AsyncSession, model: type) -> int:
    result = await session.execute(select(func.count()).select_from(model))
    return result.scalar_one()


async def test_first_run_creates_channel_and_posts(db_session: AsyncSession) -> None:
    channel = _base_channel()

    result = await ingest_channel(db_session, "durov", fetcher=_fetcher(channel))

    assert result.status == ChannelStatus.ACTIVE.value
    assert result.posts_seen == len(channel.posts)
    assert result.posts_created == len(channel.posts)
    assert result.post_snapshots_created > 0
    assert result.channel_snapshot_created is True

    channel_row = (
        await db_session.execute(select(Channel).where(Channel.username == "durov"))
    ).scalar_one()
    assert channel_row.last_fetch_status == ChannelStatus.ACTIVE.value


async def test_idempotent_on_repeated_run(db_session: AsyncSession) -> None:
    channel = _base_channel()
    fetcher = _fetcher(channel)

    await ingest_channel(db_session, "durov", fetcher=fetcher)
    posts_after_first = await _count(db_session, Post)
    snapshots_after_first = await _count(db_session, PostMetricSnapshot)
    channel_snapshots_after_first = await _count(db_session, ChannelSnapshot)

    result = await ingest_channel(db_session, "durov", fetcher=fetcher)

    assert result.posts_created == 0
    assert await _count(db_session, Post) == posts_after_first
    assert await _count(db_session, PostMetricSnapshot) == snapshots_after_first
    assert await _count(db_session, ChannelSnapshot) == channel_snapshots_after_first


async def test_metric_change_creates_single_new_snapshot(db_session: AsyncSession) -> None:
    channel = _base_channel()
    fetcher = _fetcher(channel)
    await ingest_channel(db_session, "durov", fetcher=fetcher)
    await ingest_channel(db_session, "durov", fetcher=fetcher)
    snapshots_before = await _count(db_session, PostMetricSnapshot)

    target = next(post for post in channel.posts if post.views is not None)
    updated_post = replace(target, views=target.views + 100)  # type: ignore[operator]
    updated_posts = [
        updated_post if post.message_id == target.message_id else post for post in channel.posts
    ]
    updated_channel = replace(channel, posts=updated_posts)

    result = await ingest_channel(db_session, "durov", fetcher=_fetcher(updated_channel))

    assert result.post_snapshots_created == 1
    assert await _count(db_session, PostMetricSnapshot) == snapshots_before + 1

    post_row = (
        await db_session.execute(
            select(Post).where(
                Post.channel_id == result.channel_id, Post.message_id == target.message_id
            )
        )
    ).scalar_one()
    latest_snapshot = (
        await db_session.execute(
            select(PostMetricSnapshot)
            .where(PostMetricSnapshot.post_id == post_row.id)
            .order_by(PostMetricSnapshot.id.desc())
            .limit(1)
        )
    ).scalar_one()
    assert latest_snapshot.views == updated_post.views


async def test_edit_text_updates_same_post(db_session: AsyncSession) -> None:
    channel = _base_channel()
    fetcher = _fetcher(channel)
    await ingest_channel(db_session, "durov", fetcher=fetcher)

    target = channel.posts[0]
    post_before = (
        await db_session.execute(select(Post).where(Post.message_id == target.message_id))
    ).scalar_one()
    first_seen_before = post_before.first_seen_at
    posts_before = await _count(db_session, Post)

    edited_post = replace(target, text="edited")
    updated_posts = [
        edited_post if post.message_id == target.message_id else post for post in channel.posts
    ]
    updated_channel = replace(channel, posts=updated_posts)

    result = await ingest_channel(db_session, "durov", fetcher=_fetcher(updated_channel))

    assert result.posts_edited == 1
    assert result.posts_created == 0
    assert await _count(db_session, Post) == posts_before

    # the upsert is a Core-level bulk statement, so the identity map holding
    # `post_before` is stale until explicitly expired
    db_session.expire_all()
    post_after = (
        await db_session.execute(select(Post).where(Post.message_id == target.message_id))
    ).scalar_one()
    assert post_after.id == post_before.id
    assert post_after.text == "edited"
    assert post_after.first_seen_at == first_seen_before


async def test_new_post_is_created_without_duplicating_others(db_session: AsyncSession) -> None:
    channel = _base_channel()
    fetcher = _fetcher(channel)
    await ingest_channel(db_session, "durov", fetcher=fetcher)
    posts_before = await _count(db_session, Post)

    max_id = max(post.message_id for post in channel.posts)
    new_post = replace(channel.posts[0], message_id=max_id + 1)
    updated_channel = replace(channel, posts=[*channel.posts, new_post])

    result = await ingest_channel(db_session, "durov", fetcher=_fetcher(updated_channel))

    assert result.posts_created == 1
    assert await _count(db_session, Post) == posts_before + 1


async def test_channel_snapshot_dedup(db_session: AsyncSession) -> None:
    channel = _base_channel()
    fetcher = _fetcher(channel)
    await ingest_channel(db_session, "durov", fetcher=fetcher)

    result_same = await ingest_channel(db_session, "durov", fetcher=fetcher)
    assert result_same.channel_snapshot_created is False

    changed_channel = replace(channel, subscribers=(channel.subscribers or 0) + 1000)
    result_changed = await ingest_channel(db_session, "durov", fetcher=_fetcher(changed_channel))
    assert result_changed.channel_snapshot_created is True


async def test_none_metrics_are_not_snapshotted_but_zero_is(db_session: AsyncSession) -> None:
    none_post = ParsedPost(
        message_id=1,
        posted_at=None,
        text="hi",
        has_media=False,
        media_type=None,
        link_preview_url=None,
        views=None,
        forwards=None,
        reactions_total=None,
        reactions=None,
    )
    result1 = await ingest_channel(
        db_session, "durov", fetcher=_fetcher(_minimal_channel(none_post))
    )
    assert result1.post_snapshots_created == 0

    zero_post = replace(none_post, views=0)
    result2 = await ingest_channel(
        db_session, "durov", fetcher=_fetcher(_minimal_channel(zero_post))
    )
    assert result2.post_snapshots_created == 1


async def test_channel_not_found_records_failure(db_session: AsyncSession) -> None:
    result = await ingest_channel(
        db_session, "ghostchan", fetcher=_fetcher(ChannelNotFoundError("nope"))
    )

    assert result.status == ChannelStatus.NOT_FOUND.value
    assert result.error is not None

    channel = (
        await db_session.execute(select(Channel).where(Channel.username == "ghostchan"))
    ).scalar_one()
    assert channel.consecutive_failures == 1
    assert channel.last_error is not None
    assert await _count(db_session, Post) == 0


async def test_fetch_error_increments_consecutive_failures(db_session: AsyncSession) -> None:
    fetcher = _fetcher(FetchError("boom"))

    await ingest_channel(db_session, "errchan", fetcher=fetcher)
    result = await ingest_channel(db_session, "errchan", fetcher=fetcher)

    assert result.status == ChannelStatus.ERROR.value
    channel = (
        await db_session.execute(select(Channel).where(Channel.username == "errchan"))
    ).scalar_one()
    assert channel.consecutive_failures == 2


async def test_recovers_after_failure(db_session: AsyncSession) -> None:
    await ingest_channel(db_session, "durov", fetcher=_fetcher(FetchError("boom")))

    channel = _base_channel()
    result = await ingest_channel(db_session, "durov", fetcher=_fetcher(channel))

    assert result.status == ChannelStatus.ACTIVE.value
    row = (
        await db_session.execute(select(Channel).where(Channel.username == "durov"))
    ).scalar_one()
    assert row.consecutive_failures == 0
    assert row.last_error is None
    assert row.last_fetch_status == ChannelStatus.ACTIVE.value


async def test_empty_page_marks_private(db_session: AsyncSession) -> None:
    empty = ParsedChannel(
        username="durov",
        title=None,
        description=None,
        avatar_url=None,
        subscribers=None,
        photos_count=None,
        videos_count=None,
        links_count=None,
        posts=[],
    )

    result = await ingest_channel(db_session, "durov", fetcher=_fetcher(empty))

    assert result.status == ChannelStatus.PRIVATE.value


@pytest.mark.parametrize("raw", ["ab", "not valid!"])
async def test_invalid_username_raises_without_db_write(db_session: AsyncSession, raw: str) -> None:
    with pytest.raises(InvalidChannelUsernameError):
        await ingest_channel(db_session, raw, fetcher=_fetcher(_base_channel()))

    assert await _count(db_session, Channel) == 0


async def test_normalizes_username_to_same_channel_row(db_session: AsyncSession) -> None:
    channel = _base_channel()

    await ingest_channel(db_session, "@Durov", fetcher=_fetcher(channel))
    await ingest_channel(db_session, "https://t.me/durov", fetcher=_fetcher(channel))

    assert await _count(db_session, Channel) == 1
