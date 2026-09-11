import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.errors import (
    ChannelNotFoundInDbError,
    InvalidChannelUsernameError,
    InvalidPeriodError,
    PostNotFoundError,
    ServiceError,
)
from app.web.templating import templates

logger = logging.getLogger(__name__)

_STATUS_BY_ERROR: dict[type[ServiceError], int] = {
    InvalidChannelUsernameError: 400,
    InvalidPeriodError: 400,
    ChannelNotFoundInDbError: 404,
    PostNotFoundError: 404,
}


def _wants_html(request: Request) -> bool:
    return not (request.url.path.startswith("/api/") or request.url.path.startswith("/internal/"))


def _status_for(error: ServiceError) -> int:
    return _STATUS_BY_ERROR.get(type(error), 500)


def _render(request: Request, status_code: int, message: str) -> Response:
    if _wants_html(request):
        return templates.TemplateResponse(
            request,
            "errors/error.html",
            {"status_code": status_code, "message": message},
            status_code=status_code,
        )
    return JSONResponse({"detail": message}, status_code=status_code)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _handle_service_error(request: Request, exc: ServiceError) -> Response:
        status_code = _status_for(exc)
        if status_code == 500:
            logger.exception("unhandled service error", exc_info=exc)
        return _render(request, status_code, str(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> Response:
        return _render(request, exc.status_code, str(exc.detail))
