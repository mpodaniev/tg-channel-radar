from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import analytics
from app.web import deps, presenters
from app.web.routes_api import CHANNEL_ROW_POLL_URL
from app.web.templating import templates

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    overviews = await analytics.list_channels_overview(session)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"channels": overviews, "channel_row_poll_url": CHANNEL_ROW_POLL_URL},
    )


@router.get("/channels/{username}", response_class=HTMLResponse)
async def channel_page(
    request: Request,
    username: str = Depends(deps.channel_username),
    days: int = Depends(deps.period_days),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    data = await analytics.get_channel_analytics(session, username, days=days)
    return templates.TemplateResponse(
        request,
        "channel.html",
        {
            "analytics": data,
            "period_options": deps.PERIOD_OPTIONS,
            "subs_chart": presenters.subscribers_chart(data.subscribers_trend),
            "daily_chart": presenters.daily_chart(data.daily),
            "digest": None,
        },
    )


@router.get("/posts/{post_id}", response_class=HTMLResponse)
async def post_page(
    request: Request, post_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    data = await analytics.get_post_analytics(session, post_id)
    return templates.TemplateResponse(
        request,
        "post.html",
        {"post": data, "growth_chart": presenters.post_growth_chart(data.growth)},
    )
