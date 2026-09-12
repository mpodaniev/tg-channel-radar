import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import create_app
from app.models import Channel, ChannelStatus
from app.web import background, deps
from tests.factories import make_channel

_HTMX_HEADERS = {"HX-Request": "true"}


async def _client_with_runner(db_session: AsyncSession, calls: list[str]) -> httpx.AsyncClient:
    app = create_app()

    async def _override_session():
        yield db_session

    async def _runner(username: str) -> None:
        calls.append(username)

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[deps.ingest_runner] = lambda: _runner
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_create_channel_returns_pending_row(db_session: AsyncSession) -> None:
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.post(
            "/api/channels", data={"username": "@Durov"}, headers=_HTMX_HEADERS
        )

    assert response.status_code == 200
    assert 'id="channel-row-durov"' in response.text
    assert 'hx-get="/api/channels/durov/status"' in response.text
    assert calls == ["durov"]

    result = await db_session.execute(select(Channel).where(Channel.username == "durov"))
    assert result.scalar_one().status == ChannelStatus.PENDING.value


async def test_create_channel_duplicate_returns_error_fragment(db_session: AsyncSession) -> None:
    await make_channel(db_session, username="durov")
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.post(
            "/api/channels", data={"username": "durov"}, headers=_HTMX_HEADERS
        )

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#add-channel-error"
    assert response.headers["HX-Reswap"] == "innerHTML"
    assert calls == []


async def test_create_channel_invalid_username_returns_error_fragment(
    db_session: AsyncSession,
) -> None:
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.post("/api/channels", data={"username": "-"}, headers=_HTMX_HEADERS)

    assert response.status_code == 200
    assert response.headers["HX-Retarget"] == "#add-channel-error"
    assert calls == []
    result = await db_session.execute(select(Channel))
    assert result.scalar_one_or_none() is None


async def test_create_channel_without_hx_request_redirects(db_session: AsyncSession) -> None:
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.post(
            "/api/channels", data={"username": "durov"}, follow_redirects=False
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


async def test_status_for_active_channel_stops_polling(db_session: AsyncSession) -> None:
    await make_channel(db_session, username="durov", status=ChannelStatus.ACTIVE.value)
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.get("/api/channels/durov/status")

    assert response.status_code == 200
    assert 'data-status="active"' in response.text
    assert "hx-get" not in response.text


async def test_status_for_pending_channel_keeps_polling(db_session: AsyncSession) -> None:
    await make_channel(db_session, username="durov", status=ChannelStatus.PENDING.value)
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.get("/api/channels/durov/status")

    assert response.status_code == 200
    assert "hx-get" in response.text


async def test_status_for_unknown_channel_returns_empty_body(db_session: AsyncSession) -> None:
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.get("/api/channels/nope/status")

    assert response.status_code == 200
    assert response.text == ""


async def test_delete_channel_removes_row(db_session: AsyncSession) -> None:
    await make_channel(db_session, username="durov")
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.delete("/api/channels/durov", headers=_HTMX_HEADERS)

    assert response.status_code == 200
    result = await db_session.execute(select(Channel).where(Channel.username == "durov"))
    assert result.scalar_one_or_none() is None


async def test_posts_total_card_shows_progress_while_backfilling(
    db_session: AsyncSession,
) -> None:
    await make_channel(db_session, username="durov", status=ChannelStatus.ACTIVE.value)
    background._backfill_progress["durov"] = 42
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.get("/api/channels/durov/posts-total")

    assert response.status_code == 200
    assert "↻ 42" in response.text
    assert 'hx-get="/api/channels/durov/posts-total"' in response.text


async def test_posts_total_card_has_no_progress_when_not_backfilling(
    db_session: AsyncSession,
) -> None:
    await make_channel(db_session, username="durov", status=ChannelStatus.ACTIVE.value)
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.get("/api/channels/durov/posts-total")

    assert response.status_code == 200
    assert "↻" not in response.text
    assert "hx-get" not in response.text


async def test_posts_total_card_for_unknown_channel_returns_empty_body(
    db_session: AsyncSession,
) -> None:
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.get("/api/channels/nope/posts-total")

    assert response.status_code == 200
    assert response.text == ""


async def test_refresh_channel_marks_pending_and_schedules_ingest(
    db_session: AsyncSession,
) -> None:
    await make_channel(db_session, username="durov", status=ChannelStatus.ERROR.value)
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.post("/api/channels/durov/refresh", headers=_HTMX_HEADERS)

    assert response.status_code == 200
    assert 'data-status="pending"' in response.text
    assert calls == ["durov"]


async def test_refresh_channel_without_hx_request_redirects(db_session: AsyncSession) -> None:
    await make_channel(db_session, username="durov", status=ChannelStatus.ERROR.value)
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.post(
            "/api/channels/durov/refresh", follow_redirects=False
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert calls == []


async def test_delete_channel_without_hx_request_redirects(db_session: AsyncSession) -> None:
    await make_channel(db_session, username="durov")
    calls: list[str] = []
    async with await _client_with_runner(db_session, calls) as client:
        response = await client.delete("/api/channels/durov", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"

    result = await db_session.execute(select(Channel).where(Channel.username == "durov"))
    assert result.scalar_one_or_none() is not None
