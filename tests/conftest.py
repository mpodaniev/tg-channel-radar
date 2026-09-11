from collections.abc import AsyncIterator

import httpx
import pytest


async def _forbidden_transport(
    self: httpx.AsyncHTTPTransport, request: httpx.Request
) -> AsyncIterator[httpx.Response]:
    raise RuntimeError("network access is forbidden in tests")


@pytest.fixture(autouse=True, scope="session")
def _guard_network() -> None:
    httpx.AsyncHTTPTransport.handle_async_request = _forbidden_transport  # type: ignore[method-assign]
