from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelDigest, PostAiAnnotation
from app.services.ai import tasks as ai_tasks
from app.services.errors import AiUnavailableError, ChannelNotFoundInDbError
from tests.factories import make_channel, make_post
from tests.helpers import count as _count

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


class FakeLlmClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []

    async def generate_json(self, prompt: str, *, schema: dict) -> object:
        self.calls += 1
        self.prompts.append(prompt)
        response = self._responses[self.calls - 1]
        if isinstance(response, Exception):
            raise response
        return response


def _classification_response(post_ids: list[int], category: str = "news") -> list[dict]:
    return [{"post_id": pid, "category": category, "topics": ["a"]} for pid in post_ids]


async def test_classifies_only_posts_without_annotation(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session)
    post1 = await make_post(db_session, channel, message_id=1, posted_at=NOW, text="hello")
    post2 = await make_post(db_session, channel, message_id=2, posted_at=NOW, text="world")
    db_session.add(PostAiAnnotation(post_id=post1.id, category="old", prompt_version="v0"))
    await db_session.flush()

    fake = FakeLlmClient([_classification_response([post2.id])])
    classified = await ai_tasks.classify_new_posts(db_session, channel.username, client=fake)

    assert classified == 1
    assert fake.calls == 1
    assert str(post2.id) in fake.prompts[0]


async def test_rerun_is_idempotent_no_calls_when_all_classified(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session)
    post = await make_post(db_session, channel, message_id=1, posted_at=NOW, text="hello")

    fake = FakeLlmClient([_classification_response([post.id])])
    await ai_tasks.classify_new_posts(db_session, channel.username, client=fake)

    fake2 = FakeLlmClient([])
    classified = await ai_tasks.classify_new_posts(db_session, channel.username, client=fake2)

    assert classified == 0
    assert fake2.calls == 0


async def test_batches_of_ten_for_twenty_three_posts(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session)
    posts = [
        await make_post(db_session, channel, message_id=i, posted_at=NOW, text=f"post {i}")
        for i in range(23)
    ]

    responses = [
        _classification_response([p.id for p in posts[0:10]]),
        _classification_response([p.id for p in posts[10:20]]),
        _classification_response([p.id for p in posts[20:23]]),
    ]
    fake = FakeLlmClient(responses)

    classified = await ai_tasks.classify_new_posts(db_session, channel.username, client=fake)

    assert fake.calls == 3
    assert classified == 23


async def test_malformed_response_item_skipped_rest_saved(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session)
    post1 = await make_post(db_session, channel, message_id=1, posted_at=NOW, text="hello")
    post2 = await make_post(db_session, channel, message_id=2, posted_at=NOW, text="world")

    fake = FakeLlmClient(
        [
            [
                {"post_id": post1.id, "category": 123, "topics": []},  # invalid category type
                {"post_id": post2.id, "category": "news", "topics": ["x"]},
            ]
        ]
    )

    classified = await ai_tasks.classify_new_posts(db_session, channel.username, client=fake)

    assert classified == 1
    annotations = await ai_tasks.annotations_for_posts(db_session, [post1.id, post2.id])
    assert post1.id not in annotations
    assert annotations[post2.id].category == "news"


async def test_ai_unavailable_stops_batching_without_raising(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session)
    posts = [
        await make_post(db_session, channel, message_id=i, posted_at=NOW, text=f"post {i}")
        for i in range(15)
    ]
    fake = FakeLlmClient(
        [_classification_response([p.id for p in posts[0:10]]), AiUnavailableError("down")]
    )

    classified = await ai_tasks.classify_new_posts(db_session, channel.username, client=fake)

    assert classified == 10
    assert fake.calls == 2


async def test_classify_new_posts_unknown_channel_raises(db_session: AsyncSession) -> None:
    fake = FakeLlmClient([])

    with pytest.raises(ChannelNotFoundInDbError):
        await ai_tasks.classify_new_posts(db_session, "nope", client=fake)

    assert fake.calls == 0


async def test_peek_digest_returns_none_when_no_cached_digest(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session)

    digest = await ai_tasks.peek_digest(db_session, channel.username, days=7, now=NOW)

    assert digest is None


async def test_peek_digest_returns_cached_digest_by_username(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session)
    await make_post(
        db_session, channel, message_id=1, posted_at=NOW - timedelta(hours=1), text="hi"
    )
    fake = FakeLlmClient([{"bullets": ["one"]}])
    await ai_tasks.get_or_create_digest(db_session, channel.username, days=7, client=fake, now=NOW)

    digest = await ai_tasks.peek_digest(db_session, channel.username, days=7, now=NOW)

    assert digest is not None
    assert digest.bullets == ["one"]


async def test_peek_digest_with_channel_id_skips_username_lookup(
    db_session: AsyncSession,
) -> None:
    channel = await make_channel(db_session)
    await make_post(
        db_session, channel, message_id=1, posted_at=NOW - timedelta(hours=1), text="hi"
    )
    fake = FakeLlmClient([{"bullets": ["one"]}])
    await ai_tasks.get_or_create_digest(db_session, channel.username, days=7, client=fake, now=NOW)

    # `username` here does not resolve to any channel in the DB. If the
    # implementation still performed a lookup by username, this would raise
    # ChannelNotFoundInDbError instead of returning the cached digest.
    digest = await ai_tasks.peek_digest(
        db_session, "does-not-exist", days=7, now=NOW, channel_id=channel.id
    )

    assert digest is not None
    assert digest.bullets == ["one"]


async def test_digest_cache_hit_does_not_call_llm(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session)
    await make_post(
        db_session, channel, message_id=1, posted_at=NOW - timedelta(hours=1), text="hi"
    )

    fake = FakeLlmClient([{"bullets": ["one", "two"]}])
    first = await ai_tasks.get_or_create_digest(
        db_session, channel.username, days=7, client=fake, now=NOW
    )
    assert first is not None
    assert fake.calls == 1

    fake2 = FakeLlmClient([])
    second = await ai_tasks.get_or_create_digest(
        db_session, channel.username, days=7, client=fake2, now=NOW
    )

    assert second is not None
    assert second.bullets == ["one", "two"]
    assert fake2.calls == 0
    assert await _count(db_session, ChannelDigest) == 1


async def test_different_days_produce_different_cache_rows(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session)
    await make_post(
        db_session, channel, message_id=1, posted_at=NOW - timedelta(hours=1), text="hi"
    )

    fake7 = FakeLlmClient([{"bullets": ["seven"]}])
    fake30 = FakeLlmClient([{"bullets": ["thirty"]}])

    digest7 = await ai_tasks.get_or_create_digest(
        db_session, channel.username, days=7, client=fake7, now=NOW
    )
    digest30 = await ai_tasks.get_or_create_digest(
        db_session, channel.username, days=30, client=fake30, now=NOW
    )

    assert digest7.bullets == ["seven"]
    assert digest30.bullets == ["thirty"]
    assert digest7.period_start != digest30.period_start


async def test_digest_ai_unavailable_returns_none_not_exception(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session)
    await make_post(
        db_session, channel, message_id=1, posted_at=NOW - timedelta(hours=1), text="hi"
    )
    fake = FakeLlmClient([AiUnavailableError("down")])

    result = await ai_tasks.get_or_create_digest(
        db_session, channel.username, days=7, client=fake, now=NOW
    )

    assert result is None


async def test_digest_with_no_posts_in_period_returns_none_without_calling_llm(
    db_session: AsyncSession,
) -> None:
    channel = await make_channel(db_session)
    fake = FakeLlmClient([])

    result = await ai_tasks.get_or_create_digest(
        db_session, channel.username, days=7, client=fake, now=NOW
    )

    assert result is None
    assert fake.calls == 0


async def test_anomaly_note_is_saved_and_cached(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session)
    post = await make_post(db_session, channel, message_id=1, posted_at=NOW, text="hello")

    fake = FakeLlmClient([{"note": "Unusually high engagement."}])
    notes = await ai_tasks.explain_anomalies(
        db_session, [(post.id, "hello", "spike", 4.0)], client=fake
    )

    assert notes[post.id] == "Unusually high engagement."
    assert fake.calls == 1

    fake2 = FakeLlmClient([])
    notes2 = await ai_tasks.explain_anomalies(
        db_session, [(post.id, "hello", "spike", 4.0)], client=fake2
    )
    assert notes2[post.id] == "Unusually high engagement."
    assert fake2.calls == 0

    annotation = await ai_tasks.annotation_for_post(db_session, post.id)
    assert annotation.anomaly_note == "Unusually high engagement."
