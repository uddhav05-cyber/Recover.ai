"""Async engine and session management.

Provides a single ``make_async_engine`` factory used by both the FastAPI app
and Alembic, so both connect with identical TLS handling. Hosted Postgres
providers (Neon/Supabase) hand out ``?sslmode=require`` URLs, which asyncpg
does not understand; we translate that into an ``ssl`` connect arg.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

# libpq-style query params that asyncpg rejects and must be stripped/translated.
_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding"}
_SSL_MODES_REQUIRING_TLS = {"require", "verify-ca", "verify-full", "prefer", "allow"}


def _split_url_and_connect_args(raw_url: str) -> tuple[str, dict[str, Any]]:
    """Return an asyncpg-safe URL plus ``connect_args`` for TLS.

    Strips ``sslmode``/``channel_binding`` from the query string and, when TLS
    is requested, supplies a default SSL context (valid for Neon/Supabase).
    """
    parts = urlsplit(raw_url)
    query = dict(parse_qsl(parts.query))
    connect_args: dict[str, Any] = {}

    if query.get("sslmode") in _SSL_MODES_REQUIRING_TLS:
        connect_args["ssl"] = ssl.create_default_context()

    cleaned_query = {k: v for k, v in query.items() if k not in _LIBPQ_ONLY_PARAMS}
    cleaned_url = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(cleaned_query), parts.fragment)
    )
    return cleaned_url, connect_args


def make_async_engine(**kwargs: Any) -> AsyncEngine:
    """Create an async engine from settings, applying TLS handling.

    Extra keyword args are forwarded to ``create_async_engine`` (e.g. a
    ``poolclass`` for Alembic). Any caller-supplied ``connect_args`` are merged
    on top of the TLS-derived ones.
    """
    url, connect_args = _split_url_and_connect_args(get_settings().async_database_url)
    kwargs.setdefault("pool_pre_ping", True)
    caller_connect_args = kwargs.pop("connect_args", {})
    return create_async_engine(url, connect_args={**connect_args, **caller_connect_args}, **kwargs)


engine: AsyncEngine = make_async_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an ``AsyncSession``."""
    async with SessionLocal() as session:
        yield session
