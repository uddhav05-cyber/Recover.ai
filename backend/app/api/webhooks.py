"""Razorpay webhook ingestion endpoint.

Contract (Phase 2), hardened per Razorpay's delivery semantics:

* **Verify first.** The ``X-Razorpay-Signature`` header is an HMAC-SHA256 of the
  *raw* request body under the webhook secret. We read the raw bytes and verify
  before parsing anything. Missing/invalid signature -> 400, nothing persisted.
  If no webhook secret is configured we refuse (503) rather than accept an
  unverified event.
* **Validate + persist only.** The handler does no synchronous diagnosis or
  Razorpay API calls — it stores the raw event as ``pending`` and returns fast
  (Razorpay times out webhook responses at ~5s). The agent pipeline runs later.
* **Idempotent.** Dedup is on the ``x-razorpay-event-id`` header via a DB unique
  constraint; redeliveries return 200 ``duplicate`` so Razorpay stops retrying.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import get_session
from app.integrations.razorpay import verify_webhook_signature
from app.schemas.razorpay_webhook import WebhookEnvelope
from app.services.ingestion import ingest_webhook_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_SIGNATURE_HEADER = "X-Razorpay-Signature"
_EVENT_ID_HEADER = "X-Razorpay-Event-Id"


@router.post("/razorpay", status_code=status.HTTP_200_OK)
async def razorpay_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Ingest a Razorpay webhook: verify signature, dedupe, persist as pending."""
    raw_body = await request.body()
    signature = request.headers.get(_SIGNATURE_HEADER)
    event_id = request.headers.get(_EVENT_ID_HEADER)

    secret = settings.razorpay_webhook_secret
    if not secret:
        # Never accept an unverifiable webhook. This is a server misconfiguration.
        logger.error("Razorpay webhook secret not configured; rejecting delivery")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook verification not configured",
        )

    if not signature or not verify_webhook_signature(raw_body, signature, secret):
        logger.warning("Rejected webhook: invalid/missing signature (event_id=%s)", event_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature"
        )

    if not event_id:
        # Signature is valid but we have no idempotency key to dedupe on.
        logger.warning("Rejected webhook: missing %s header", _EVENT_ID_HEADER)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing {_EVENT_ID_HEADER} header",
        )

    try:
        raw_payload = json.loads(raw_body)
        envelope = WebhookEnvelope.model_validate(raw_payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning("Rejected webhook: malformed payload (event_id=%s): %s", event_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed webhook payload"
        ) from exc

    result = await ingest_webhook_event(
        session,
        razorpay_event_id=event_id,
        signature_verified=True,
        raw_payload=raw_payload,
        envelope=envelope,
    )

    return {
        "status": "duplicate" if result.duplicate else "received",
        "event_id": result.razorpay_event_id,
        "event_type": result.event_type,
        "payment_event_id": (
            str(result.payment_event_id) if result.payment_event_id else None
        ),
    }
