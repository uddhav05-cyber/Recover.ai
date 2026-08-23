from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.db.session import get_session, make_async_engine
from app.main import app

# Known secret the async webhook tests sign their payloads with.
TEST_WEBHOOK_SECRET = "whsec_test_secret"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A session on a single connection inside a transaction that is rolled back.

    Uses SQLAlchemy's "join into an external transaction" pattern
    (``join_transaction_mode="create_savepoint"``) so the application code under
    test can call ``session.commit()`` normally while every write stays inside a
    savepoint. The outer transaction is rolled back at teardown — nothing is
    persisted to the real database. ``NullPool`` keeps the connection off the
    shared app engine's pool, avoiding cross-event-loop reuse between tests.
    """
    test_engine = make_async_engine(poolclass=NullPool)
    conn = await test_engine.connect()
    trans = await conn.begin()
    session = AsyncSession(
        bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    try:
        yield session
    finally:
        await session.close()
        if trans.is_active:
            await trans.rollback()
        await conn.close()
        await test_engine.dispose()


@pytest_asyncio.fixture
async def aclient(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Async HTTP client bound to the app, with DB + settings dependencies overridden.

    ``get_session`` yields the rolled-back test session; ``get_settings`` supplies
    a Settings whose webhook secret is :data:`TEST_WEBHOOK_SECRET`, so signed test
    payloads verify.
    """

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_settings() -> Settings:
        return get_settings().model_copy(update={"razorpay_webhook_secret": TEST_WEBHOOK_SECRET})

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_settings] = _override_settings
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
