import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import engine, get_session
from app.web import routes_api, routes_pages
from app.web.errors import register_exception_handlers
from app.web.templating import STATIC_DIR

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="channel-radar", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz(
        response: Response, session: AsyncSession = Depends(get_session)
    ) -> dict[str, str]:
        try:
            await session.execute(text("SELECT 1"))
        except Exception:
            logger.exception("healthz: database check failed")
            response.status_code = 503
            return {"status": "degraded", "db": "error"}
        return {"status": "ok", "db": "ok"}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    register_exception_handlers(app)
    app.include_router(routes_pages.router)
    app.include_router(routes_api.router)

    return app


app = create_app()
