"""Webhook ingestion service.

Turns a verified Razorpay webhook into exactly one ``payment_events`` row.
Idempotency is enforced at the database level via ``INSERT ... ON CONFLICT DO
NOTHING`` against the ``uq_payment_events_razorpay_event_id`` unique constraint,
so concurrent redeliveries of the same ``x-razorpay-event-id`` collapse to a
single row without application-level locking or read-modify-write races.

This layer deliberately does *no* diagnosis or recovery work — it validates and
persists the raw event as ``pending``. The Diagnose -> Decide -> Act pipeline
(Phase 3) consumes pending rows separately.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, PaymentEvent
from app.enums import AuditActor
from app.schemas.razorpay_webhook import WebhookEnvelope


@dataclass(slots=True)
class IngestResult:
    """Outcome of ingesting one webhook event."""

    razorpay_event_id: str
    event_type: str
    payment_event_id: uuid.UUID | None
    duplicate: bool


def _created_at(epoch: int | None) -> datetime | None:
    return datetime.fromtimestamp(epoch, tz=UTC) if epoch else None


async def ingest_webhook_event(
    session: AsyncSession,
    *,
    razorpay_event_id: str,
    signature_verified: bool,
    raw_payload: dict[str, Any],
    envelope: WebhookEnvelope,
) -> IngestResult:
    """Persist a verified webhook event, deduplicating on ``razorpay_event_id``.

    Returns an :class:`IngestResult` flagging whether this was a fresh insert or
    a duplicate redelivery. Commits the transaction (event row + audit row).
    """
    payment = envelope.payment
    subscription = envelope.subscription

    values: dict[str, Any] = {
        "razorpay_event_id": razorpay_event_id,
        "event_type": envelope.event,
        "razorpay_subscription_id": subscription.id if subscription else None,
        "razorpay_payment_id": payment.id if payment else None,
        "error_code": payment.error_code if payment else None,
        "error_description": payment.error_description if payment else None,
        "amount": payment.amount if payment else None,
        "currency": payment.currency if payment else None,
        "payload": raw_payload,
        "signature_verified": signature_verified,
        "razorpay_created_at": _created_at(envelope.created_at),
    }

    # Race-safe dedup: the second concurrent delivery conflicts on the unique
    # constraint and returns no row.
    insert_stmt = (
        pg_insert(PaymentEvent)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["razorpay_event_id"])
        .returning(PaymentEvent.id)
    )
    inserted_id: uuid.UUID | None = await session.scalar(insert_stmt)
    duplicate = inserted_id is None

    if duplicate:
        # Resolve the pre-existing row so the audit entry still references it.
        payment_event_id = await session.scalar(
            select(PaymentEvent.id).where(PaymentEvent.razorpay_event_id == razorpay_event_id)
        )
    else:
        payment_event_id = inserted_id

    session.add(
        AuditLog(
            actor=AuditActor.RAZORPAY_WEBHOOK,
            action="webhook.duplicate" if duplicate else "webhook.received",
            payment_event_id=payment_event_id,
            detail={
                "razorpay_event_id": razorpay_event_id,
                "event_type": envelope.event,
                "signature_verified": signature_verified,
                "razorpay_subscription_id": values["razorpay_subscription_id"],
                "razorpay_payment_id": values["razorpay_payment_id"],
            },
        )
    )
    await session.commit()

    return IngestResult(
        razorpay_event_id=razorpay_event_id,
        event_type=envelope.event,
        payment_event_id=payment_event_id,
        duplicate=duplicate,
    )
