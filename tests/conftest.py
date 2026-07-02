import asyncio
import os

# Must happen before any `app.*` import: pydantic-settings reads the env var
# when app.config.settings is constructed at first import. Pointing at a
# dedicated database means the suite never touches whatever the developer
# has running in the normal "group_chat" dev database.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/group_chat_test")

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models import Base


def _admin_database_url() -> str:
    return settings.database_url.rsplit("/", 1)[0] + "/postgres"


def _test_database_name() -> str:
    return settings.database_url.rsplit("/", 1)[1]


async def _ensure_database_exists() -> None:
    admin_engine = create_async_engine(_admin_database_url(), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": _test_database_name()}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{_test_database_name()}"'))
    finally:
        await admin_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _prepare_test_database():
    """Points app.db at group_chat_test (creating it if needed) and swaps
    in NullPool. TestClient serves requests on its own internal event loop
    (a background portal thread), while this fixture's asyncio.run() calls
    run on separate throwaway loops -- with the default pool, a connection
    checked out on one loop and later reused on another makes asyncpg raise
    "attached to a different loop". NullPool means every checkout is a
    brand-new physical connection, so no pooled connection ever crosses a
    loop boundary.
    """
    import app.db as db_module

    asyncio.run(_ensure_database_exists())

    db_module.engine = create_async_engine(settings.database_url, poolclass=NullPool)
    db_module.async_session_factory = async_sessionmaker(db_module.engine, expire_on_commit=False)

    async def _create_schema():
        # Recreate the schema from scratch every session so it always
        # matches the *current* models.py exactly. Base.metadata.drop_all
        # tries to drop each constraint by the name in metadata, which
        # breaks the moment that name doesn't match what's actually in the
        # database (e.g. a prior run's schema, before a column gained an
        # explicit constraint name) -- DROP SCHEMA sidesteps that entirely
        # by not caring what's in there.
        async with db_module.engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_schema())
    yield
    asyncio.run(db_module.engine.dispose())


@pytest.fixture(autouse=True)
def _truncate_tables():
    """Each test starts against an empty database. Truncation (not
    transaction rollback) is used because websocket tests involve multiple
    concurrent connections that all need to see the same committed data
    mid-test."""
    import app.db as db_module

    async def _truncate():
        # Order doesn't matter -- CASCADE handles the users<->media circular
        # FK (avatar_media_id -> media, media.uploader_id -> users) in one
        # statement, unlike Base.metadata.sorted_tables which can't
        # topologically sort a genuine cycle.
        table_names = ", ".join(f'"{t.name}"' for t in Base.metadata.tables.values())
        async with db_module.engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))

    asyncio.run(_truncate())
    yield


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
