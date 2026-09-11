import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, ChannelSnapshot, ChannelStatus, Post, PostMetricSnapshot
from app.parser.errors import ChannelNotFoundError, InvalidUsernameError, ParserError
from app.parser.normalize import normalize_username
from app.parser.tme import fetch_channel
from app.parser.types import ParsedChannel, ParsedPost
from app.services.errors import InvalidChannelUsernameError

DEFAULT_MAX_POSTS = 200


class ChannelFetcher(Protocol):
    async def __call__(self, username: str, *, max_posts: int) -> ParsedChannel: ...


@dataclass(frozen=True)
class IngestResult:
    channel_id: int
    status: str
    posts_seen: int
    posts_created: int
    posts_edited: int
    post_snapshots_created: int
    channel_snapshot_created: bool
    error: str | None


def _content_hash(post: ParsedPost) -> str:
    # \x00 marks "absent" so None never collides with an empty string, and the
    # \x1f join keeps adjacent fields from concatenating into an ambiguous hash.
    parts = [
        post.text if post.text is not None else "\x00",
        post.media_type if post.media_type is not None else "\x00",
        post.link_preview_url if post.link_preview_url is not None else "\x00",
        str(post.has_media),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


async def _get_or_create_channel(session: AsyncSession, username: str) -> Channel:
    result = await session.execute(select(Channel).where(Channel.username == username))
    channel = result.scalar_one_or_none()
    if channel is not None:
        return channel

    channel = Channel(username=username, status=ChannelStatus.PENDING.value)
    session.add(channel)
    await session.flush()
    return channel


async def _record_failure(
    session: AsyncSession,
    channel: Channel,
    status: ChannelStatus,
    exc: Exception,
    now: datetime,
) -> IngestResult:
    error = str(exc)[:1000]
    channel.last_fetch_at = now
    channel.last_fetch_status = status.value
    channel.last_error = error
    channel.consecutive_failures += 1
    channel.status = status.value

    await session.commit()

    return IngestResult(
        channel_id=channel.id,
        status=status.value,
        posts_seen=0,
        posts_created=0,
        posts_edited=0,
        post_snapshots_created=0,
        channel_snapshot_created=False,
        error=error,
    )


async def _upsert_posts(
    session: AsyncSession,
    channel_id: int,
    parsed_posts: list[ParsedPost],
    now: datetime,
) -> tuple[dict[int, int], int, int]:
    if not parsed_posts:
        return {}, 0, 0

    message_ids = [post.message_id for post in parsed_posts]
    hashes = {post.message_id: _content_hash(post) for post in parsed_posts}

    existing_result = await session.execute(
        select(Post.message_id, Post.content_hash).where(
            Post.channel_id == channel_id, Post.message_id.in_(message_ids)
        )
    )
    existing_hashes = {row.message_id: row.content_hash for row in existing_result}

    posts_created = sum(1 for message_id in message_ids if message_id not in existing_hashes)
    posts_edited = sum(
        1
        for message_id in message_ids
        if message_id in existing_hashes and existing_hashes[message_id] != hashes[message_id]
    )

    rows = [
        {
            "channel_id": channel_id,
            "message_id": post.message_id,
            "posted_at": post.posted_at,
            "text": post.text,
            "has_media": post.has_media,
            "media_type": post.media_type,
            "link_preview_url": post.link_preview_url,
            "first_seen_at": now,
            "content_hash": hashes[post.message_id],
        }
        for post in parsed_posts
    ]

    insert_stmt = pg_insert(Post).values(rows)
    stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_posts_channel_message",
        set_={
            "posted_at": insert_stmt.excluded.posted_at,
            "text": insert_stmt.excluded.text,
            "has_media": insert_stmt.excluded.has_media,
            "media_type": insert_stmt.excluded.media_type,
            "link_preview_url": insert_stmt.excluded.link_preview_url,
            "content_hash": insert_stmt.excluded.content_hash,
        },
    ).returning(Post.id, Post.message_id)

    result = await session.execute(stmt)
    post_ids_by_message = {row.message_id: row.id for row in result}

    return post_ids_by_message, posts_created, posts_edited


def _snapshot_unchanged(latest: tuple | None, current: tuple) -> bool:
    return latest is not None and latest == current


async def _write_post_metric_snapshots(
    session: AsyncSession,
    post_ids_by_message: dict[int, int],
    parsed_posts: list[ParsedPost],
    now: datetime,
) -> int:
    measured_posts = [
        post
        for post in parsed_posts
        if any(
            v is not None
            for v in (post.views, post.forwards, post.reactions_total, post.reactions)
        )
    ]
    if not measured_posts:
        return 0

    post_ids = [post_ids_by_message[post.message_id] for post in measured_posts]

    latest_id_subq = (
        select(PostMetricSnapshot.post_id, func.max(PostMetricSnapshot.id).label("latest_id"))
        .where(PostMetricSnapshot.post_id.in_(post_ids))
        .group_by(PostMetricSnapshot.post_id)
        .subquery()
    )
    latest_result = await session.execute(
        select(
            PostMetricSnapshot.post_id,
            PostMetricSnapshot.views,
            PostMetricSnapshot.forwards,
            PostMetricSnapshot.reactions_total,
            PostMetricSnapshot.reactions_json,
        ).join(latest_id_subq, PostMetricSnapshot.id == latest_id_subq.c.latest_id)
    )
    latest_by_post_id = {
        row.post_id: (row.views, row.forwards, row.reactions_total, row.reactions_json)
        for row in latest_result
    }

    created = 0
    for post in measured_posts:
        post_id = post_ids_by_message[post.message_id]
        current = (post.views, post.forwards, post.reactions_total, post.reactions)
        if _snapshot_unchanged(latest_by_post_id.get(post_id), current):
            continue
        session.add(
            PostMetricSnapshot(
                post_id=post_id,
                captured_at=now,
                views=post.views,
                forwards=post.forwards,
                reactions_total=post.reactions_total,
                reactions_json=post.reactions,
            )
        )
        created += 1

    return created


async def _write_channel_snapshot(
    session: AsyncSession, channel_id: int, parsed: ParsedChannel, now: datetime
) -> bool:
    if (
        parsed.subscribers is None
        and parsed.photos_count is None
        and parsed.videos_count is None
        and parsed.links_count is None
    ):
        return False

    result = await session.execute(
        select(
            ChannelSnapshot.subscribers,
            ChannelSnapshot.photos_count,
            ChannelSnapshot.videos_count,
            ChannelSnapshot.links_count,
        )
        .where(ChannelSnapshot.channel_id == channel_id)
        .order_by(desc(ChannelSnapshot.id))
        .limit(1)
    )
    latest = result.first()
    current = (parsed.subscribers, parsed.photos_count, parsed.videos_count, parsed.links_count)
    if _snapshot_unchanged(tuple(latest) if latest is not None else None, current):
        return False

    session.add(
        ChannelSnapshot(
            channel_id=channel_id,
            captured_at=now,
            subscribers=parsed.subscribers,
            photos_count=parsed.photos_count,
            videos_count=parsed.videos_count,
            links_count=parsed.links_count,
        )
    )
    return True


async def ingest_channel(
    session: AsyncSession,
    username: str,
    *,
    max_posts: int = DEFAULT_MAX_POSTS,
    fetcher: ChannelFetcher | None = None,
) -> IngestResult:
    fetch = fetcher or fetch_channel

    try:
        normalized = normalize_username(username)
    except InvalidUsernameError as exc:
        raise InvalidChannelUsernameError(str(exc)) from exc

    channel = await _get_or_create_channel(session, normalized)
    now = datetime.now(UTC)

    try:
        parsed = await fetch(normalized, max_posts=max_posts)
    except ChannelNotFoundError as exc:
        return await _record_failure(session, channel, ChannelStatus.NOT_FOUND, exc, now)
    except ParserError as exc:
        return await _record_failure(session, channel, ChannelStatus.ERROR, exc, now)

    if parsed.title is None and not parsed.posts:
        empty_page = ParserError("channel page has no header and no posts (likely private)")
        return await _record_failure(session, channel, ChannelStatus.PRIVATE, empty_page, now)

    channel.title = parsed.title
    channel.description = parsed.description
    channel.avatar_url = parsed.avatar_url
    channel.status = ChannelStatus.ACTIVE.value
    channel.last_fetch_at = now
    channel.last_fetch_status = ChannelStatus.ACTIVE.value
    channel.last_error = None
    channel.consecutive_failures = 0

    post_ids_by_message, posts_created, posts_edited = await _upsert_posts(
        session, channel.id, parsed.posts, now
    )
    post_snapshots_created = await _write_post_metric_snapshots(
        session, post_ids_by_message, parsed.posts, now
    )
    channel_snapshot_created = await _write_channel_snapshot(session, channel.id, parsed, now)

    await session.commit()

    return IngestResult(
        channel_id=channel.id,
        status=ChannelStatus.ACTIVE.value,
        posts_seen=len(parsed.posts),
        posts_created=posts_created,
        posts_edited=posts_edited,
        post_snapshots_created=post_snapshots_created,
        channel_snapshot_created=channel_snapshot_created,
        error=None,
    )
