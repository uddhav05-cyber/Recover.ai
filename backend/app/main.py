"""FastAPI application entrypoint.

Phase 1 exposes a root banner and a ``/health`` probe that pings the database.
Later phases add the webhook route, the agent pipeline, and dashboard APIs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.db.session import engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Dispose of the engine's connection pool on shutdown."""
    yield
    await engine.dispose()


app = FastAPI(
    title="RecoverAI",
    version="0.1.0",
    description="Autonomous Razorpay subscription payment recovery agent.",
    lifespan=lifespan,
)


@app.get("/")
async def root() -> dict[str, Any]:
    """Service banner."""
    return {"service": "RecoverAI", "version": app.version, "docs": "/docs"}


@app.get("/health")
async def health() -> JSONResponse:
    """Liveness + database connectivity probe.

    Returns 200 when the database answers ``SELECT 1``, otherwise 503 with the
    connection error so setup issues are visible.
    """
    settings = get_settings()
    db_ok = False
    db_error: str | None = None
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # noqa: BLE001 - surface any connection failure
        db_error = str(exc)

    body: dict[str, Any] = {
        "status": "ok" if db_ok else "degraded",
        "app_env": settings.app_env,
        "database": {"connected": db_ok},
    }
    if db_error is not None:
        body["database"]["error"] = db_error

    return JSONResponse(status_code=200 if db_ok else 503, content=body)
