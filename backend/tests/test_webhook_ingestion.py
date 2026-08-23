"""Phase 2 webhook ingestion tests.

The centerpiece is :func:`test_duplicate_webhook_ingested_once` — firing the
same payload twice and asserting exactly one ``payment_events`` row, per the
build brief. All tests run inside a rolled-back transaction (see the
``db_session``/``aclient`` fixtures), so nothing touches the real database.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import AuditLog, PaymentEvent
from app.db.session import get_session
from app.enums import EventProcessingStatus
from app.main import app
from tests.conftest import TEST_WEBHOOK_SECRET


def _sign(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _failed_payment_event() -> dict[str, Any]:
    """A representative Razorpay ``payment.failed`` webhook envelope.

    The idempotency key is the ``x-razorpay-event-id`` *header*, not part of the
    body — so this fixed body is reused across tests, keyed by distinct headers.
    """
    return {
        "entity": "event",
        "account_id": "acc_TEST",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_TEST0001",
                    "amount": 79900,
                    "currency": "INR",
                    "status": "failed",
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "payment failed due to insufficient funds",
                }
            },
            "subscription": {
                "entity": {"id": "sub_TEST0001", "status": "halted", "auth_attempts": 2}
            },
        },
        "created_at": 1690000000,
    }


def _headers(body: bytes, event_id: str, *, sign: bool = True) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Razorpay-Event-Id": event_id,
        "X-Razorpay-Signature": _sign(body) if sign else "deadbeef",
    }


async def test_valid_webhook_persists_pending_event(
    aclient: AsyncClient, db_session: AsyncSession
) -> None:
    event_id = "evt_persist_1"
    body = json.dumps(_failed_payment_event()).encode()

    resp = await aclient.post("/webhooks/razorpay", content=body, headers=_headers(body, event_id))

    assert resp.status_code == 200
    assert resp.json()["status"] == "received"

    row = await db_session.scalar(
        select(PaymentEvent).where(PaymentEvent.razorpay_event_id == event_id)
    )
    assert row is not None
    assert row.event_type == "payment.failed"
    assert row.amount == 79900
    assert row.razorpay_payment_id == "pay_TEST0001"
    assert row.razorpay_subscription_id == "sub_TEST0001"
    assert row.error_code == "BAD_REQUEST_ERROR"
    assert row.signature_verified is True
    # Handler only ingests — diagnosis/recovery happen later (Phase 3).
    assert row.processing_status == EventProcessingStatus.PENDING


async def test_duplicate_webhook_ingested_once(
    aclient: AsyncClient, db_session: AsyncSession
) -> None:
    """Fire the identical webhook twice -> exactly one row (the brief's key test)."""
    event_id = "evt_dedup_1"
    body = json.dumps(_failed_payment_event()).encode()
    headers = _headers(body, event_id)

    first = await aclient.post("/webhooks/razorpay", content=body, headers=headers)
    second = await aclient.post("/webhooks/razorpay", content=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "received"
    assert second.json()["status"] == "duplicate"
    # Both responses point at the same stored event.
    assert first.json()["payment_event_id"] == second.json()["payment_event_id"]

    count = await db_session.scalar(
        select(func.count())
        .select_from(PaymentEvent)
        .where(PaymentEvent.razorpay_event_id == event_id)
    )
    assert count == 1


async def test_invalid_signature_rejected_and_not_persisted(
    aclient: AsyncClient, db_session: AsyncSession
) -> None:
    event_id = "evt_badsig_1"
    body = json.dumps(_failed_payment_event()).encode()

    resp = await aclient.post(
        "/webhooks/razorpay", content=body, headers=_headers(body, event_id, sign=False)
    )

    assert resp.status_code == 400
    count = await db_session.scalar(
        select(func.count())
        .select_from(PaymentEvent)
        .where(PaymentEvent.razorpay_event_id == event_id)
    )
    assert count == 0


async def test_missing_event_id_rejected(aclient: AsyncClient) -> None:
    body = json.dumps(_failed_payment_event()).encode()
    resp = await aclient.post(
        "/webhooks/razorpay",
        content=body,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": _sign(body)},
    )
    assert resp.status_code == 400


async def test_audit_row_written_for_ingested_event(
    aclient: AsyncClient, db_session: AsyncSession
) -> None:
    event_id = "evt_audit_1"
    body = json.dumps(_failed_payment_event()).encode()

    await aclient.post("/webhooks/razorpay", content=body, headers=_headers(body, event_id))

    action = await db_session.scalar(
        select(AuditLog.action).where(
            AuditLog.detail["razorpay_event_id"].astext == event_id
        )
    )
    assert action == "webhook.received"


async def test_unconfigured_secret_returns_503(db_session: AsyncSession) -> None:
    """With no webhook secret set, we refuse rather than accept unverified events."""

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    def _override_settings() -> Settings:
        return get_settings().model_copy(update={"razorpay_webhook_secret": None})

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_settings] = _override_settings
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            body = json.dumps(_failed_payment_event()).encode()
            resp = await ac.post(
                "/webhooks/razorpay",
                content=body,
                headers=_headers(body, "evt_nosecret_1"),
            )
            assert resp.status_code == 503
    finally:
        app.dependency_overrides.clear()
