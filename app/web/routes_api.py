from typing import Annotated, Final

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import channels
from app.services.analytics_types import ChannelOverview
from app.services.errors import (
    ChannelAlreadyExistsError,
    ChannelNotFoundInDbError,
    InvalidChannelUsernameError,
)
from app.web import deps
from app.web.templating import templates

router = APIRouter(prefix="/api/channels", tags=["api"])

CHANNEL_ROW_POLL_URL: Final = "/api/channels/{username}/status"


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _reject_non_htmx(request: Request) -> Response | None:
    if _is_htmx(request):
        return None
    return RedirectResponse("/", status_code=303)


async def _get_overview_or_none(session: AsyncSession, username: str) -> ChannelOverview | None:
    try:
        return await channels.get_overview(session, username)
    except ChannelNotFoundInDbError:
        return None


def _render_row(request: Request, overview: ChannelOverview) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/channel_row.html",
        {"overview": overview, "channel_row_poll_url": CHANNEL_ROW_POLL_URL},
    )


def _render_form_error(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/add_channel_error.html",
        {"message": message},
        status_code=200,
        headers={"HX-Retarget": "#add-channel-error", "HX-Reswap": "innerHTML"},
    )


@router.post("", response_class=HTMLResponse)
async def create_channel(
    request: Request,
    background_tasks: BackgroundTasks,
    username: Annotated[str, Form()],
    session: AsyncSession = Depends(get_session),
    runner: deps.IngestRunner = Depends(deps.ingest_runner),
) -> Response:
    if guard := _reject_non_htmx(request):
        return guard

    try:
        normalized = await channels.add_channel(session, username)
    except (InvalidChannelUsernameError, ChannelAlreadyExistsError) as exc:
        return _render_form_error(request, str(exc))

    background_tasks.add_task(runner, normalized)
    overview = await channels.get_overview(session, normalized)
    return _render_row(request, overview)


@router.get("/{username}/status", response_class=HTMLResponse)
async def channel_status(
    request: Request,
    username: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    overview = await _get_overview_or_none(session, username)
    if overview is None:
        # The row polled a channel that no longer exists (e.g. deleted while
        # pending). Empty body + hx-swap="outerHTML" removes the stale row.
        return HTMLResponse("", status_code=200)
    return _render_row(request, overview)


@router.get("/{username}/posts-total", response_class=HTMLResponse)
async def posts_total_card(
    request: Request,
    username: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    overview = await _get_overview_or_none(session, username)
    if overview is None:
        # The channel detail page polled a channel that no longer exists
        # (e.g. deleted in another tab). Empty body + hx-swap="outerHTML"
        # removes the stale card instead of erroring.
        return HTMLResponse("", status_code=200)
    return templates.TemplateResponse(
        request,
        "partials/posts_total_card.html",
        {"username": overview.username, "posts_total": overview.posts_total},
    )


@router.post("/{username}/refresh", response_class=HTMLResponse)
async def refresh_channel(
    request: Request,
    username: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    runner: deps.IngestRunner = Depends(deps.ingest_runner),
) -> Response:
    if guard := _reject_non_htmx(request):
        return guard

    await channels.mark_pending(session, username)
    background_tasks.add_task(runner, username)
    overview = await channels.get_overview(session, username)
    return _render_row(request, overview)


@router.delete("/{username}")
async def delete_channel(
    request: Request,
    username: str,
    session: AsyncSession = Depends(get_session),
) -> Response:
    if guard := _reject_non_htmx(request):
        return guard

    await channels.delete_channel(session, username)
    return HTMLResponse("", status_code=200)
