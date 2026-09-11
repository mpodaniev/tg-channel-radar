import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import engine, get_session

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

    return app


app = create_app()
