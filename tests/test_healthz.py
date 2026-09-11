from collections.abc import AsyncIterator

import httpx
import pytest

from app.db import get_session
from app.main import create_app


class _FakeSession:
    def __init__(self, *, should_fail: bool) -> None:
        self._should_fail = should_fail

    async def execute(self, _statement: object) -> None:
        if self._should_fail:
            raise RuntimeError("boom")


async def _client(should_fail: bool) -> httpx.AsyncClient:
    app = create_app()

    async def override_get_session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession(should_fail=should_fail)

    app.dependency_overrides[get_session] = override_get_session
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_healthz_ok() -> None:
    async with await _client(should_fail=False) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


@pytest.mark.asyncio
async def test_healthz_degraded_when_db_fails() -> None:
    async with await _client(should_fail=True) as client:
        response = await client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "error"}
