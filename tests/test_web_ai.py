from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import create_app
from app.web import deps
from tests.factories import make_channel, make_post

_HTMX_HEADERS = {"HX-Request": "true"}
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


class FakeLlmClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def generate_json(self, prompt: str, *, schema: dict) -> object:
        self.calls += 1
        response = self._responses[self.calls - 1]
        if isinstance(response, Exception):
            raise response
        return response


async def _client(db_session: AsyncSession, ai_client) -> httpx.AsyncClient:
    app = create_app()

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[deps.ai_client] = lambda: ai_client
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_channel_page_renders_with_null_ai_state(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session, last_fetch_at=NOW)
    await make_post(db_session, channel, message_id=1, posted_at=NOW, text="hello")

    async with await _client(db_session, FakeLlmClient([])) as client:
        response = await client.get(f"/channels/{channel.username}")

    assert response.status_code == 200
    assert "Generate digest" in response.text


async def test_generate_digest_returns_fragment_with_bullets(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session, last_fetch_at=NOW)
    # The route calls get_or_create_digest without an injected clock, so the
    # post must fall within the period as measured by the real wall clock.
    real_now = datetime.now(UTC)
    await make_post(
        db_session, channel, message_id=1, posted_at=real_now - timedelta(hours=1), text="hello"
    )

    fake = FakeLlmClient([{"bullets": ["one thing happened", "another thing happened"]}])
    async with await _client(db_session, fake) as client:
        response = await client.post(
            f"/api/channels/{channel.username}/digest",
            params={"days": 7},
            headers=_HTMX_HEADERS,
        )

    assert response.status_code == 200
    assert "one thing happened" in response.text
    assert "another thing happened" in response.text


async def test_generate_digest_rejects_non_htmx_request(db_session: AsyncSession) -> None:
    channel = await make_channel(db_session, last_fetch_at=NOW)

    async with await _client(db_session, FakeLlmClient([])) as client:
        response = await client.post(
            f"/api/channels/{channel.username}/digest", params={"days": 7}, follow_redirects=False
        )

    assert response.status_code == 303
