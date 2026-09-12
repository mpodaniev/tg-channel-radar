from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, ChannelDigest, Post, PostAiAnnotation
from app.services import channels
from app.services.ai.client import LlmClient
from app.services.ai.prompts import (
    ANOMALY_SCHEMA,
    CLASSIFICATION_SCHEMA,
    DIGEST_SCHEMA,
    PROMPT_VERSION,
    build_anomaly_prompt,
    build_classification_prompt,
    build_digest_prompt,
)
from app.services.ai.types import DigestView, PostAiSummary
from app.services.errors import AiUnavailableError

_BULLET_PREFIX = "- "


def _digest_period(days: int, now: datetime) -> tuple[datetime, datetime]:
    # period_end is the start of *tomorrow* (UTC) so today's posts are always
    # included, while both boundaries stay pinned to day granularity for the
    # whole day -- otherwise every call would compute a slightly different
    # `now` and never hit the cache.
    period_end = datetime.combine(now.date(), time.min, tzinfo=UTC) + timedelta(days=1)
    period_start = period_end - timedelta(days=days)
    return period_start, period_end


def _bullets_to_markdown(bullets: list[str]) -> str:
    return "\n".join(f"{_BULLET_PREFIX}{bullet}" for bullet in bullets)


def _markdown_to_bullets(digest_md: str | None) -> list[str]:
    if not digest_md:
        return []
    return [
        line[len(_BULLET_PREFIX) :] if line.startswith(_BULLET_PREFIX) else line
        for line in digest_md.splitlines()
        if line.strip()
    ]


async def classify_new_posts(
    session: AsyncSession,
    username: str,
    *,
    client: LlmClient,
    max_batches: int = 3,
    batch_size: int = 10,
) -> int:
    """Classifies posts without an annotation row yet. Returns the number of
    posts successfully classified. Best-effort: an unavailable AI layer stops
    the loop early instead of raising, so callers (e.g. background ingest)
    never fail because of this."""
    channel = await _get_channel_or_raise(session, username)

    unclassified_result = await session.execute(
        select(Post.id, Post.text)
        .outerjoin(PostAiAnnotation, PostAiAnnotation.post_id == Post.id)
        .where(
            Post.channel_id == channel.id,
            Post.text.is_not(None),
            PostAiAnnotation.id.is_(None),
        )
        .order_by(Post.id)
        .limit(max_batches * batch_size)
    )
    pending = unclassified_result.all()

    classified = 0
    for batch_start in range(0, len(pending), batch_size):
        batch = pending[batch_start : batch_start + batch_size]
        if not batch:
            break

        prompt = build_classification_prompt([(row.id, row.text) for row in batch])
        try:
            raw = await client.generate_json(prompt, schema=CLASSIFICATION_SCHEMA)
        except AiUnavailableError:
            break

        valid_post_ids = {row.id for row in batch}
        rows = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            post_id = item.get("post_id")
            category = item.get("category")
            topics = item.get("topics")
            if post_id not in valid_post_ids or not isinstance(category, str):
                continue
            if not isinstance(topics, list) or not all(isinstance(t, str) for t in topics):
                topics = []
            rows.append(
                {
                    "post_id": post_id,
                    "category": category,
                    "topics_json": topics,
                    "model": None,
                    "prompt_version": PROMPT_VERSION,
                }
            )

        if not rows:
            continue

        insert_stmt = pg_insert(PostAiAnnotation).values(rows)
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=["post_id"],
            set_={
                "category": insert_stmt.excluded.category,
                "topics_json": insert_stmt.excluded.topics_json,
                "model": insert_stmt.excluded.model,
                "prompt_version": insert_stmt.excluded.prompt_version,
            },
        )
        await session.execute(stmt)
        classified += len(rows)

    await session.commit()
    return classified


async def _get_channel_or_raise(session: AsyncSession, username: str) -> Channel:
    normalized = channels.normalize(username)
    return await channels.get_channel_or_raise(session, normalized)


async def _find_cached_digest(
    session: AsyncSession, channel_id: int, period_start: datetime, period_end: datetime
) -> ChannelDigest | None:
    result = await session.execute(
        select(ChannelDigest).where(
            ChannelDigest.channel_id == channel_id,
            ChannelDigest.period_start == period_start,
            ChannelDigest.period_end == period_end,
            ChannelDigest.prompt_version == PROMPT_VERSION,
        )
    )
    return result.scalar_one_or_none()


def _digest_view_from_row(
    channel_id: int, period_start: datetime, period_end: datetime, row: ChannelDigest
) -> DigestView:
    return DigestView(
        channel_id=channel_id,
        period_start=period_start,
        period_end=period_end,
        bullets=_markdown_to_bullets(row.digest_md),
    )


async def peek_digest(
    session: AsyncSession,
    username: str,
    *,
    days: int,
    now: datetime | None = None,
    channel_id: int | None = None,
) -> DigestView | None:
    """Reads a cached digest, if any, without ever calling the LLM. Used by
    page rendering, which must never block on AI to stay fast on a cold
    Render dyno. Pass `channel_id` when the caller already resolved the
    channel, to skip a second lookup by username on the same request."""
    now = now or datetime.now(UTC)
    if channel_id is None:
        channel_id = (await _get_channel_or_raise(session, username)).id
    period_start, period_end = _digest_period(days, now)

    existing = await _find_cached_digest(session, channel_id, period_start, period_end)
    if existing is None:
        return None
    return _digest_view_from_row(channel_id, period_start, period_end, existing)


async def get_or_create_digest(
    session: AsyncSession,
    username: str,
    *,
    days: int,
    client: LlmClient,
    now: datetime | None = None,
) -> DigestView | None:
    now = now or datetime.now(UTC)
    channel = await _get_channel_or_raise(session, username)
    period_start, period_end = _digest_period(days, now)

    existing = await _find_cached_digest(session, channel.id, period_start, period_end)
    if existing is not None:
        return _digest_view_from_row(channel.id, period_start, period_end, existing)

    posts_result = await session.execute(
        select(Post.text).where(
            Post.channel_id == channel.id,
            Post.text.is_not(None),
            Post.posted_at >= period_start,
            Post.posted_at < period_end,
        )
    )
    texts = [row.text for row in posts_result]
    if not texts:
        return None

    prompt = build_digest_prompt(channel.title, texts)
    try:
        raw = await client.generate_json(prompt, schema=DIGEST_SCHEMA)
    except AiUnavailableError:
        return None

    bullets = raw.get("bullets") if isinstance(raw, dict) else None
    if not isinstance(bullets, list) or not all(isinstance(b, str) for b in bullets):
        return None

    insert_stmt = pg_insert(ChannelDigest).values(
        channel_id=channel.id,
        period_start=period_start,
        period_end=period_end,
        digest_md=_bullets_to_markdown(bullets),
        model=None,
        prompt_version=PROMPT_VERSION,
    )
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=["channel_id", "period_start", "period_end", "prompt_version"],
        set_={"digest_md": insert_stmt.excluded.digest_md, "model": insert_stmt.excluded.model},
    )
    await session.execute(stmt)
    await session.commit()

    return DigestView(
        channel_id=channel.id, period_start=period_start, period_end=period_end, bullets=bullets
    )


async def explain_anomalies(
    session: AsyncSession,
    anomalies: Sequence[tuple[int, str, str, float]],
    *,
    client: LlmClient,
) -> dict[int, str]:
    """`anomalies` is (post_id, post_text, direction, score) for posts already
    flagged by app.services.stats.detect_anomalies. Skips posts that already
    have a cached note and stops early if the AI layer becomes unavailable."""
    if not anomalies:
        return {}

    post_ids = [post_id for post_id, *_ in anomalies]
    cached_result = await session.execute(
        select(PostAiAnnotation.post_id, PostAiAnnotation.anomaly_note).where(
            PostAiAnnotation.post_id.in_(post_ids), PostAiAnnotation.anomaly_note.is_not(None)
        )
    )
    notes: dict[int, str] = {row.post_id: row.anomaly_note for row in cached_result}

    for post_id, text, direction, score in anomalies:
        if post_id in notes:
            continue

        prompt = build_anomaly_prompt(text, direction, score)
        try:
            raw = await client.generate_json(prompt, schema=ANOMALY_SCHEMA)
        except AiUnavailableError:
            break

        note = raw.get("note") if isinstance(raw, dict) else None
        if not isinstance(note, str) or not note:
            continue

        insert_stmt = pg_insert(PostAiAnnotation).values(
            post_id=post_id,
            anomaly_note=note,
            prompt_version=PROMPT_VERSION,
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=["post_id"], set_={"anomaly_note": insert_stmt.excluded.anomaly_note}
        )
        await session.execute(stmt)
        notes[post_id] = note

    await session.commit()
    return notes


async def annotations_for_posts(
    session: AsyncSession, post_ids: Sequence[int]
) -> dict[int, PostAiSummary]:
    if not post_ids:
        return {}
    result = await session.execute(
        select(PostAiAnnotation).where(PostAiAnnotation.post_id.in_(post_ids))
    )
    return {
        row.post_id: PostAiSummary(
            post_id=row.post_id,
            category=row.category,
            topics=row.topics_json or [],
            summary=row.summary,
            anomaly_note=row.anomaly_note,
        )
        for row in result.scalars()
    }


async def annotation_for_post(session: AsyncSession, post_id: int) -> PostAiSummary | None:
    result = await annotations_for_posts(session, [post_id])
    return result.get(post_id)
