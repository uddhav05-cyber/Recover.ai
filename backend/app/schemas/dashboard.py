"""Dashboard API schemas and responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.enums import DiagnosisCategory, OutcomeStatus, SubscriptionStatus


class SubscriptionDTO(BaseModel):
    """A subscription in the dashboard list."""

    id: str
    razorpay_subscription_id: str
    status: SubscriptionStatus | None
    amount: int | None  # paise
    currency: str
    last_event_at: datetime | None
    recovery_status: str  # "no_failures" | "pending" | "recovered" | "escalated" | "dead"


class RecoveryFunnelItem(BaseModel):
    """One step in the recovery funnel."""

    stage: str  # "failed" | "retried" | "recovered" | "escalated" | "dead"
    count: int
    amount: int  # total paise


class RecoveryMetrics(BaseModel):
    """Aggregate recovery metrics."""

    total_subscriptions: int
    failed_events: int
    recovered_count: int
    recovery_rate: float
    amount_recovered: int  # paise
    amount_at_risk: int  # paise
    funnel: list[RecoveryFunnelItem]


class ExceptionDTO(BaseModel):
    """An unresolved exception / at-risk case."""

    id: str
    subscription_id: str
    razorpay_subscription_id: str
    category: DiagnosisCategory
    outcome: OutcomeStatus
    amount: int  # paise
    amount_at_risk: int  # paise
    last_action_at: datetime | None
    last_action_detail: str | None


class AuthLoginRequest(BaseModel):
    """Stub auth login request."""

    email: str
    password: str


class AuthLoginResponse(BaseModel):
    """Stub auth login response."""

    access_token: str
    token_type: str = "bearer"
    user: dict[str, Any]
