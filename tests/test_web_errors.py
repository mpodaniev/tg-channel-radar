import httpx
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.services.errors import (
    ChannelNotFoundInDbError,
    InvalidChannelUsernameError,
    InvalidPeriodError,
    PostNotFoundError,
    ServiceError,
)
from app.web.errors import _status_for, register_exception_handlers
from app.web.templating import STATIC_DIR


class TestStatusFor:
    def test_invalid_channel_username_maps_to_400(self) -> None:
        assert _status_for(InvalidChannelUsernameError("x")) == 400

    def test_invalid_period_maps_to_400(self) -> None:
        assert _status_for(InvalidPeriodError("x")) == 400

    def test_channel_not_found_maps_to_404(self) -> None:
        assert _status_for(ChannelNotFoundInDbError("x")) == 404

    def test_post_not_found_maps_to_404(self) -> None:
        assert _status_for(PostNotFoundError("x")) == 404

    def test_unmapped_service_error_maps_to_500(self) -> None:
        class SomeOtherError(ServiceError):
            pass

        assert _status_for(SomeOtherError("x")) == 500


class _BoomError(ServiceError):
    pass


def _build_error_test_app() -> FastAPI:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    register_exception_handlers(app)

    @app.get("/pages/not-found")
    async def _pages_not_found() -> None:
        raise ChannelNotFoundInDbError("channel not found: nope")

    @app.get("/api/not-found")
    async def _api_not_found() -> None:
        raise ChannelNotFoundInDbError("channel not found: nope")

    @app.get("/pages/boom")
    async def _pages_boom() -> None:
        raise _BoomError("something broke")

    @app.get("/api/boom")
    async def _api_boom() -> None:
        raise _BoomError("something broke")

    @app.get("/pages/http-404")
    async def _pages_http_404() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="not found")

    @app.get("/api/http-404")
    async def _api_http_404() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="not found")

    return app


@pytest.fixture
async def error_test_client() -> httpx.AsyncClient:
    app = _build_error_test_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_service_error_renders_html_page_for_page_routes(
    error_test_client: httpx.AsyncClient,
) -> None:
    response = await error_test_client.get("/pages/not-found")
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "channel not found: nope" in response.text


async def test_service_error_renders_json_for_api_routes(
    error_test_client: httpx.AsyncClient,
) -> None:
    response = await error_test_client.get("/api/not-found")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "channel not found: nope"}


async def test_unmapped_service_error_defaults_to_500_html(
    error_test_client: httpx.AsyncClient,
) -> None:
    response = await error_test_client.get("/pages/boom")
    assert response.status_code == 500
    assert "text/html" in response.headers["content-type"]


async def test_unmapped_service_error_defaults_to_500_json(
    error_test_client: httpx.AsyncClient,
) -> None:
    response = await error_test_client.get("/api/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "something broke"}


async def test_http_exception_renders_html_page_for_page_routes(
    error_test_client: httpx.AsyncClient,
) -> None:
    response = await error_test_client.get("/pages/http-404")
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "not found" in response.text


async def test_http_exception_renders_json_for_api_routes(
    error_test_client: httpx.AsyncClient,
) -> None:
    response = await error_test_client.get("/api/http-404")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "not found"}
