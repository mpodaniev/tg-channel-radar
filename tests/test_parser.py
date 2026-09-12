from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import app.parser.tme as tme_module
from app.parser.errors import ChannelNotFoundError, FetchError, InvalidUsernameError
from app.parser.normalize import normalize_username, parse_channel_page, parse_count
from app.parser.tme import TmeClient, fetch_channel
from app.parser.types import ParsedChannel, ParsedPost
from tests.helpers import load_fixture as _load_fixture

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _patch_tme(
    monkeypatch: pytest.MonkeyPatch,
    fetch_fn: Callable[..., object],
    parse_fn: Callable[..., ParsedChannel],
) -> None:
    monkeypatch.setattr(tme_module.TmeClient, "fetch_channel_page", fetch_fn)
    monkeypatch.setattr(tme_module, "parse_channel_page", parse_fn)


def _page(
    ids: list[int],
    days_ago: list[float | None],
    next_before: int | None,
) -> ParsedChannel:
    posts = [
        ParsedPost(
            message_id=message_id,
            posted_at=_NOW - timedelta(days=age) if age is not None else None,
            text=f"post {message_id}",
            has_media=False,
            media_type=None,
            link_preview_url=None,
            views=None,
            forwards=None,
            reactions_total=None,
            reactions=None,
        )
        for message_id, age in zip(ids, days_ago, strict=True)
    ]
    return ParsedChannel(
        username="durov",
        title="Durov",
        description=None,
        avatar_url=None,
        subscribers=100,
        photos_count=None,
        videos_count=None,
        links_count=None,
        posts=posts,
        next_before=next_before,
    )


class TestNormalizeUsername:
    @pytest.mark.parametrize(
        "raw",
        [
            "durov",
            "@durov",
            "https://t.me/durov",
            "t.me/s/durov",
            "DUROV",
            "  durov  ",
        ],
    )
    def test_valid_forms(self, raw: str) -> None:
        assert normalize_username(raw) == "durov"

    @pytest.mark.parametrize("raw", ["ab", "a" * 33, "not valid!", "", "@"])
    def test_invalid_forms_raise(self, raw: str) -> None:
        with pytest.raises(InvalidUsernameError):
            normalize_username(raw)


class TestParseCount:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12.5K", 12500),
            ("1,234", 1234),
            ("2M", 2_000_000),
            ("199", 199),
            ("", None),
            (None, None),
        ],
    )
    def test_parse_count(self, raw: str | None, expected: int | None) -> None:
        assert parse_count(raw) == expected


class TestParseChannelPageBasic:
    @classmethod
    @pytest.fixture(scope="class")
    def channel(cls) -> ParsedChannel:
        return parse_channel_page(_load_fixture("channel_basic.html"), username="durov")

    def test_header_fields(self, channel: ParsedChannel) -> None:
        assert channel.title
        assert channel.description
        assert channel.subscribers is not None and channel.subscribers > 0

    def test_has_posts(self, channel: ParsedChannel) -> None:
        assert len(channel.posts) > 0

    def test_message_ids_increase(self, channel: ParsedChannel) -> None:
        ids = [post.message_id for post in channel.posts]
        assert ids == sorted(ids)

    def test_posted_at_is_aware_utc(self, channel: ParsedChannel) -> None:
        for post in channel.posts:
            assert post.posted_at is not None
            assert post.posted_at.tzinfo is not None
            offset = post.posted_at.utcoffset()
            assert offset is not None
            assert offset.total_seconds() == 0

    def test_text_present(self, channel: ParsedChannel) -> None:
        assert any(post.text for post in channel.posts)

    def test_forwards_always_none(self, channel: ParsedChannel) -> None:
        assert all(post.forwards is None for post in channel.posts)

    def test_reactions_present_when_available(self, channel: ParsedChannel) -> None:
        with_reactions = [post for post in channel.posts if post.reactions is not None]
        assert with_reactions
        for post in with_reactions:
            assert post.reactions is not None
            assert post.reactions_total == sum(post.reactions.values())


class TestParseChannelPageMedia:
    @classmethod
    @pytest.fixture(scope="class")
    def channel(cls) -> ParsedChannel:
        return parse_channel_page(_load_fixture("channel_media.html"), username="telegram")

    def test_media_types_detected(self, channel: ParsedChannel) -> None:
        media_types = {post.media_type for post in channel.posts if post.has_media}
        assert media_types & {"photo", "video"}

    def test_has_media_matches_media_type(self, channel: ParsedChannel) -> None:
        for post in channel.posts:
            assert post.has_media == (post.media_type is not None)

    def test_link_preview_detected(self, channel: ParsedChannel) -> None:
        previews = [post.link_preview_url for post in channel.posts if post.link_preview_url]
        assert previews
        assert all(url.startswith("http") for url in previews)

    def test_next_before_cursor(self, channel: ParsedChannel) -> None:
        assert channel.next_before is not None


class TestParseChannelPageEdgeCases:
    def test_not_found_page_has_no_posts(self) -> None:
        channel = parse_channel_page(
            _load_fixture("channel_not_found.html"), username="nonexistent"
        )
        assert channel.posts == []

    def test_paginated_page_parses(self) -> None:
        channel = parse_channel_page(_load_fixture("channel_paginated.html"), username="durov")
        assert len(channel.posts) > 0
        assert channel.next_before is not None


class TestFetchChannel:
    async def test_dedups_and_stops_on_no_new_posts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        basic = parse_channel_page(_load_fixture("channel_basic.html"), username="durov")

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            return "basic" if before is None else "basic"

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return basic

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        result = await fetch_channel("durov", max_posts=1000)

        assert len(result.posts) == len(basic.posts)
        assert {post.message_id for post in result.posts} == {
            post.message_id for post in basic.posts
        }

    async def test_stops_at_max_posts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        basic = parse_channel_page(_load_fixture("channel_basic.html"), username="durov")
        paginated = parse_channel_page(_load_fixture("channel_paginated.html"), username="durov")

        call_count = 0

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            nonlocal call_count
            call_count += 1
            return "page"

        pages_by_call = [basic, paginated]

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return pages_by_call[min(call_count - 1, len(pages_by_call) - 1)]

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        max_posts = len(basic.posts) + 5
        result = await fetch_channel("durov", max_posts=max_posts)

        assert len(result.posts) <= max_posts

    async def test_next_before_none_stops_pagination(self, monkeypatch: pytest.MonkeyPatch) -> None:
        not_found = parse_channel_page(_load_fixture("channel_not_found.html"), username="durov")
        assert not_found.next_before is None

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            return "page"

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return not_found

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        result = await fetch_channel("durov", max_posts=100)

        assert result.posts == []
        assert result.next_before is None

    async def test_stops_when_page_oldest_crosses_cutoff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        since = _NOW - timedelta(days=15)
        # page1: all fresh; page2: oldest crosses the cutoff; page3: entirely stale (never fetched)
        p1 = _page([3, 4], [2, 1], next_before=2)
        p2 = _page([1, 2], [20, 5], next_before=1)
        p3 = _page([0], [50], next_before=None)
        pages = [p1, p2, p3]
        call_count = 0

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            nonlocal call_count
            call_count += 1
            return "page"

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return pages[call_count - 1]

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        result = await fetch_channel("durov", since=since, max_posts=1000)

        assert call_count == 2
        ids = {post.message_id for post in result.posts}
        assert ids == {3, 4, 2}
        assert 1 not in ids

    async def test_continues_while_oldest_post_in_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        since = _NOW - timedelta(days=90)
        p1 = _page([3], [10], next_before=2)
        p2 = _page([2], [20], next_before=1)
        p3 = _page([1], [200], next_before=None)
        pages = [p1, p2, p3]
        call_count = 0

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            nonlocal call_count
            call_count += 1
            return "page"

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return pages[call_count - 1]

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        result = await fetch_channel("durov", since=since, max_posts=1000)

        assert call_count == 3
        ids = {post.message_id for post in result.posts}
        assert 1 not in ids

    async def test_filters_posts_older_than_since(self, monkeypatch: pytest.MonkeyPatch) -> None:
        since = _NOW - timedelta(days=15)
        page = _page([1, 2, 3], [20, 10, 5], next_before=None)

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            return "page"

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return page

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        result = await fetch_channel("durov", since=since, max_posts=1000)

        assert {post.message_id for post in result.posts} == {2, 3}

    async def test_keeps_posts_with_missing_posted_at(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        since = _NOW - timedelta(days=15)
        page = _page([1, 2, 3], [20, None, 5], next_before=None)

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            return "page"

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return page

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        result = await fetch_channel("durov", since=since, max_posts=1000)

        ids = {post.message_id for post in result.posts}
        assert ids == {2, 3}
        assert 1 not in ids

    async def test_all_none_dates_page_does_not_stop_pagination(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        since = _NOW - timedelta(days=15)
        p1 = _page([2, 3], [None, None], next_before=1)
        p2 = _page([1], [100], next_before=None)
        pages = [p1, p2]
        call_count = 0

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            nonlocal call_count
            call_count += 1
            return "page"

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return pages[call_count - 1]

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        result = await fetch_channel("durov", since=since, max_posts=1000)

        assert call_count == 2
        assert 1 not in {post.message_id for post in result.posts}

    async def test_safety_cap_keeps_newest_posts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        p1 = _page([5, 6], [2, 1], next_before=4)
        p2 = _page([3, 4], [4, 3], next_before=2)
        p3 = _page([1, 2], [6, 5], next_before=None)
        pages = [p1, p2, p3]
        call_count = 0

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            nonlocal call_count
            call_count += 1
            return "page"

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return pages[call_count - 1]

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        max_posts = 3
        result = await fetch_channel("durov", max_posts=max_posts)

        assert len(result.posts) == max_posts
        assert {post.message_id for post in result.posts} == {4, 5, 6}

    async def test_partial_failure_returns_collected_posts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p1 = _page([1, 2], [2, 1], next_before=1)
        call_count = 0

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "page"
            raise FetchError("boom")

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return p1

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        result = await fetch_channel("durov", max_posts=1000)

        assert {post.message_id for post in result.posts} == {1, 2}

    async def test_since_none_keeps_count_only_behaviour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p1 = _page([3, 4], [2, 1], next_before=2)
        p2 = _page([1, 2], [4, 3], next_before=None)
        pages = [p1, p2]
        call_count = 0

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            nonlocal call_count
            call_count += 1
            return "page"

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return pages[call_count - 1]

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        result = await fetch_channel("durov", since=None, max_posts=1000)

        assert {post.message_id for post in result.posts} == {1, 2, 3, 4}

    async def test_reports_progress_after_each_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        p1 = _page([3, 4], [2, 1], next_before=2)
        p2 = _page([1, 2], [4, 3], next_before=None)
        pages = [p1, p2]
        call_count = 0

        async def fake_fetch_channel_page(
            self: object, username: str, before: int | None = None
        ) -> str:
            nonlocal call_count
            call_count += 1
            return "page"

        def fake_parse(html: str, *, username: str) -> ParsedChannel:
            return pages[call_count - 1]

        _patch_tme(monkeypatch, fake_fetch_channel_page, fake_parse)

        seen: list[int] = []
        await fetch_channel("durov", max_posts=1000, on_progress=seen.append)

        assert seen == [2, 4]


class TestTmeClientFetchChannelPage:
    @pytest.fixture(autouse=True)
    def _no_real_sleep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(tme_module.asyncio, "sleep", fake_sleep)

    async def test_returns_text_on_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = TmeClient()

        async def fake_get(
            username: str, params: dict[str, object] | None = None
        ) -> httpx.Response:
            return httpx.Response(200, text="<html>ok</html>")

        monkeypatch.setattr(client._client, "get", fake_get)

        result = await client.fetch_channel_page("durov")

        assert result == "<html>ok</html>"

    @pytest.mark.parametrize("status_code", [301, 302, 404])
    async def test_raises_channel_not_found_without_retrying(
        self, monkeypatch: pytest.MonkeyPatch, status_code: int
    ) -> None:
        client = TmeClient()
        call_count = 0

        async def fake_get(
            username: str, params: dict[str, object] | None = None
        ) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(status_code, text="")

        monkeypatch.setattr(client._client, "get", fake_get)

        with pytest.raises(ChannelNotFoundError):
            await client.fetch_channel_page("nonexistent")

        assert call_count == 1

    async def test_retries_on_http_error_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = TmeClient()
        call_count = 0

        async def fake_get(
            username: str, params: dict[str, object] | None = None
        ) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, text="<html>ok</html>")

        monkeypatch.setattr(client._client, "get", fake_get)

        result = await client.fetch_channel_page("durov")

        assert result == "<html>ok</html>"
        assert call_count == 2

    async def test_raises_fetch_error_after_exhausting_retries_on_http_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = TmeClient()
        call_count = 0

        async def fake_get(
            username: str, params: dict[str, object] | None = None
        ) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("boom")

        monkeypatch.setattr(client._client, "get", fake_get)

        with pytest.raises(FetchError):
            await client.fetch_channel_page("durov")

        assert call_count == tme_module._MAX_RETRIES + 1

    async def test_retries_on_non_ok_status_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = TmeClient()
        call_count = 0

        async def fake_get(
            username: str, params: dict[str, object] | None = None
        ) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(500, text="")
            return httpx.Response(200, text="<html>ok</html>")

        monkeypatch.setattr(client._client, "get", fake_get)

        result = await client.fetch_channel_page("durov")

        assert result == "<html>ok</html>"
        assert call_count == 2

    async def test_raises_fetch_error_after_exhausting_retries_on_bad_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = TmeClient()
        call_count = 0

        async def fake_get(
            username: str, params: dict[str, object] | None = None
        ) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(500, text="")

        monkeypatch.setattr(client._client, "get", fake_get)

        with pytest.raises(FetchError):
            await client.fetch_channel_page("durov")

        assert call_count == tme_module._MAX_RETRIES + 1
