import json
import re
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import add_channel_snapshot, add_metric, make_channel, make_post

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def _chart_payload(html: str, script_id: str) -> dict:
    match = re.search(
        rf'<script id="{script_id}" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    assert match is not None, f"chart data script {script_id!r} not found"
    return json.loads(match.group(1))


async def test_healthz_still_ok_through_new_wiring(app_client: httpx.AsyncClient) -> None:
    response = await app_client.get("/healthz")
    assert response.status_code == 200


async def test_static_css_is_served(app_client: httpx.AsyncClient) -> None:
    response = await app_client.get("/static/css/app.css")
    assert response.status_code == 200


async def test_index_empty_state(app_client: httpx.AsyncClient) -> None:
    response = await app_client.get("/")
    assert response.status_code == 200
    assert 'data-testid="channels-empty"' in response.text
    assert 'action="/api/channels"' in response.text


async def test_index_lists_channel_rows(
    app_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    channel_a = await make_channel(db_session, username="channel_a", last_fetch_at=NOW)
    await make_channel(db_session, username="channel_b", last_fetch_at=NOW)
    post = await make_post(db_session, channel_a, message_id=1, posted_at=NOW)
    await add_metric(db_session, post, views=12_500, captured_at=NOW)

    response = await app_client.get("/")

    assert response.status_code == 200
    assert response.text.count('data-testid="channel-row"') == 2
    assert "12.5K" in response.text
    assert "badge--" in response.text
    assert "hx-get" not in response.text


async def test_channel_page_renders_chart_data(
    app_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    channel = await make_channel(db_session, username="durov", last_fetch_at=NOW)
    await add_channel_snapshot(
        db_session, channel, subscribers=100, captured_at=NOW - timedelta(days=2)
    )
    await add_channel_snapshot(db_session, channel, subscribers=150, captured_at=NOW)
    post = await make_post(db_session, channel, message_id=1, posted_at=NOW - timedelta(days=1))
    await add_metric(db_session, post, views=40, captured_at=NOW)

    response = await app_client.get("/channels/durov")

    assert response.status_code == 200
    subs_payload = _chart_payload(response.text, "subs-chart-data")
    assert len(subs_payload["labels"]) == 2
    daily_payload = _chart_payload(response.text, "daily-chart-data")
    assert len(daily_payload["labels"]) > 0


async def test_channel_page_normalizes_username(
    app_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await make_channel(db_session, username="durov", last_fetch_at=NOW)

    response = await app_client.get("/channels/@Durov")

    assert response.status_code == 200


async def test_channel_page_period_filter_marks_active_option(
    app_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await make_channel(db_session, username="durov", last_fetch_at=NOW)

    response = await app_client.get("/channels/durov?days=7")
    assert response.status_code == 200
    assert '<a href="?days=7" aria-current="true">' in response.text

    response = await app_client.get("/channels/durov?days=90")
    assert response.status_code == 200


async def test_channel_page_invalid_period_returns_400(
    app_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await make_channel(db_session, username="durov", last_fetch_at=NOW)

    response = await app_client.get("/channels/durov?days=5")

    assert response.status_code == 400
    assert "text/html" in response.headers["content-type"]


async def test_channel_page_unknown_channel_returns_404(app_client: httpx.AsyncClient) -> None:
    response = await app_client.get("/channels/nope")
    assert response.status_code == 404


async def test_channel_page_invalid_username_returns_404(app_client: httpx.AsyncClient) -> None:
    response = await app_client.get("/channels/-")
    assert response.status_code == 404


async def test_channel_page_flags_anomalous_post(
    app_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    channel = await make_channel(db_session, username="durov", last_fetch_at=NOW)
    for i in range(6):
        post = await make_post(
            db_session, channel, message_id=i, posted_at=NOW - timedelta(hours=6 - i)
        )
        await add_metric(db_session, post, views=100, captured_at=post.posted_at)
    viral_post = await make_post(db_session, channel, message_id=100, posted_at=NOW)
    await add_metric(db_session, viral_post, views=50_000, captured_at=NOW)

    response = await app_client.get("/channels/durov")

    assert response.status_code == 200
    assert 'data-testid="anomaly-badge"' in response.text


async def test_post_page_shows_full_text_and_tme_link(
    app_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    channel = await make_channel(db_session, username="durov", last_fetch_at=NOW)
    long_text = "word " * 60
    post = await make_post(db_session, channel, message_id=42, posted_at=NOW, text=long_text)

    response = await app_client.get(f"/posts/{post.id}")

    assert response.status_code == 200
    assert long_text.strip()[161:180] in response.text
    assert 'href="https://t.me/durov/42"' in response.text


async def test_post_page_unknown_id_returns_404(app_client: httpx.AsyncClient) -> None:
    response = await app_client.get("/posts/999999")
    assert response.status_code == 404


async def test_post_page_without_metrics_shows_no_history(
    app_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    channel = await make_channel(db_session, username="durov", last_fetch_at=NOW)
    post = await make_post(db_session, channel, message_id=1, posted_at=NOW)

    response = await app_client.get(f"/posts/{post.id}")

    assert response.status_code == 200
    assert "No view history yet" in response.text
