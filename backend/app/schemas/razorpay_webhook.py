"""Pydantic models for the Razorpay webhook envelope.

Razorpay wraps every webhook in a fixed envelope::

    {
      "entity": "event",
      "account_id": "acc_XXXX",
      "event": "subscription.charged",
      "contains": ["payment", "subscription"],
      "payload": {
        "payment":      {"entity": {"id": "pay_...", "amount": 79900, ...}},
        "subscription": {"entity": {"id": "sub_...", "status": "halted", ...}}
      },
      "created_at": 1690000000
    }

The models are intentionally lenient (``extra="ignore"``, every entity field
optional): Razorpay adds fields over time and the payload shape varies by event
type. We only pull out what we persist for the idempotency/ingestion layer; the
full raw body is stored verbatim in ``payment_events.payload`` regardless.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PaymentEntity(BaseModel):
    """The ``payload.payment.entity`` object (present on payment events)."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    amount: int | None = None  # paise
    currency: str | None = None
    status: str | None = None
    error_code: str | None = None
    error_description: str | None = None


class SubscriptionEntity(BaseModel):
    """The ``payload.subscription.entity`` object (present on subscription events)."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    status: str | None = None
    auth_attempts: int | None = None


class _PaymentWrapper(BaseModel):
    model_config = ConfigDict(extra="ignore")
    entity: PaymentEntity | None = None


class _SubscriptionWrapper(BaseModel):
    model_config = ConfigDict(extra="ignore")
    entity: SubscriptionEntity | None = None


class WebhookPayload(BaseModel):
    """The ``payload`` object holding per-entity wrappers."""

    model_config = ConfigDict(extra="ignore")

    payment: _PaymentWrapper | None = None
    subscription: _SubscriptionWrapper | None = None


class WebhookEnvelope(BaseModel):
    """Top-level Razorpay webhook envelope."""

    model_config = ConfigDict(extra="ignore")

    entity: str | None = None
    account_id: str | None = None
    event: str
    contains: list[str] = Field(default_factory=list)
    payload: WebhookPayload = Field(default_factory=WebhookPayload)
    created_at: int | None = None  # epoch seconds

    @property
    def payment(self) -> PaymentEntity | None:
        """Convenience accessor for ``payload.payment.entity``."""
        return self.payload.payment.entity if self.payload.payment else None

    @property
    def subscription(self) -> SubscriptionEntity | None:
        """Convenience accessor for ``payload.subscription.entity``."""
        return self.payload.subscription.entity if self.payload.subscription else None
