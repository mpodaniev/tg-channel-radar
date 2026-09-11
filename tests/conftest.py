import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings

REPO_ROOT = Path(__file__).parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


async def _forbidden_transport(
    self: httpx.AsyncHTTPTransport, request: httpx.Request
) -> AsyncIterator[httpx.Response]:
    raise RuntimeError("network access is forbidden in tests")


@pytest.fixture(autouse=True, scope="session")
def _guard_network() -> None:
    httpx.AsyncHTTPTransport.handle_async_request = _forbidden_transport  # type: ignore[method-assign]


def test_database_url() -> str:
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        return explicit

    base_url = get_settings().normalized_database_url.url
    parts = urlsplit(base_url)
    db_name = parts.path.lstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, f"/{db_name}_test", parts.query, parts.fragment))


def _to_asyncpg_dsn(url: str, *, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(("postgresql", parts.netloc, f"/{dbname}", "", ""))


async def _create_test_database_if_missing() -> None:
    test_url = test_database_url()
    db_name = urlsplit(test_url).path.lstrip("/")
    admin_dsn = _to_asyncpg_dsn(test_url, dbname="postgres")

    conn = await asyncpg.connect(admin_dsn)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_database() -> None:
    asyncio.run(_create_test_database_if_missing())


@pytest.fixture(scope="session", autouse=True)
def _migrate(_ensure_test_database: None) -> None:
    config = Config(str(ALEMBIC_INI))
    config.attributes["sqlalchemy_url"] = test_database_url()
    command.upgrade(config, "head")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(test_database_url(), poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()
