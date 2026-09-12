import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

import httpx

from app.parser.errors import ChannelNotFoundError, FetchError, ParserError
from app.parser.normalize import normalize_username, parse_channel_page
from app.parser.types import ParsedChannel, ParsedPost

_BASE_URL = "https://t.me/s/"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_TIMEOUT = 15.0
_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0
_PAGE_DELAY_SECONDS = 0.5
_DEFAULT_MAX_POSTS = 2000  # safety cap only; `since` is the primary stopping criterion
_MAX_PAGES = 120


class TmeClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_channel_page(self, username: str, before: int | None = None) -> str:
        params = {"before": before} if before is not None else None

        for attempt in range(_MAX_RETRIES + 1):
            is_last_attempt = attempt >= _MAX_RETRIES
            try:
                response = await self._client.get(username, params=params)
            except httpx.HTTPError as exc:
                if is_last_attempt:
                    raise FetchError(f"failed to fetch {username!r}: {exc}") from exc
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (2**attempt))
                continue

            if response.status_code == 200:
                return response.text
            # t.me/s/<username> 302-redirects to the non-preview t.me/<username>
            # page for both nonexistent and preview-disabled channels; there is
            # no reliable HTTP signal to tell those apart at this layer.
            if response.status_code in (301, 302, 404):
                raise ChannelNotFoundError(f"no web preview for channel {username!r}")
            if is_last_attempt:
                raise FetchError(f"failed to fetch {username!r}: HTTP {response.status_code}")
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (2**attempt))

        raise FetchError(f"failed to fetch {username!r}: exhausted retries")


def _reached_cutoff(posts: list[ParsedPost], since: datetime | None) -> bool:
    if since is None:
        return False
    oldest = min((p.posted_at for p in posts if p.posted_at is not None), default=None)
    return oldest is not None and oldest < since


async def fetch_channel(
    username: str,
    *,
    since: datetime | None = None,
    max_posts: int = _DEFAULT_MAX_POSTS,
    on_progress: Callable[[int], None] | None = None,
    client: TmeClient | None = None,
) -> ParsedChannel:
    normalized = normalize_username(username)
    owns_client = client is None
    client = client or TmeClient()

    try:
        html = await client.fetch_channel_page(normalized)
        channel = parse_channel_page(html, username=normalized)

        posts: list[ParsedPost] = list(channel.posts)
        seen_ids = {post.message_id for post in posts}
        before = channel.next_before
        reached_cutoff = _reached_cutoff(channel.posts, since)
        pages = 1
        if on_progress is not None:
            on_progress(len(posts))

        while (
            before is not None
            and len(posts) < max_posts
            and not reached_cutoff
            and pages < _MAX_PAGES
        ):
            await asyncio.sleep(_PAGE_DELAY_SECONDS)
            try:
                page_html = await client.fetch_channel_page(normalized, before=before)
                page = parse_channel_page(page_html, username=normalized)
            except ParserError:
                break
            pages += 1

            new_posts = [post for post in page.posts if post.message_id not in seen_ids]
            if not new_posts:
                break

            posts.extend(new_posts)
            seen_ids.update(post.message_id for post in new_posts)
            before = page.next_before
            reached_cutoff = _reached_cutoff(page.posts, since)
            if on_progress is not None:
                on_progress(len(posts))

        kept = [
            post
            for post in posts
            if since is None or post.posted_at is None or post.posted_at >= since
        ]
        kept.sort(key=lambda post: post.message_id)
        return replace(channel, posts=kept[-max_posts:], next_before=before)
    finally:
        if owns_client:
            await client.aclose()
