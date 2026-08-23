"""Enumerations shared across the domain model.

These back the ``VARCHAR + CHECK`` columns in the schema (see ``db/models.py``)
and are used throughout the Diagnose -> Decide -> Act -> Outcome pipeline.
Keeping the sets small is intentional (per the build brief).
"""

from __future__ import annotations

from enum import StrEnum


class SubscriptionStatus(StrEnum):
    """Razorpay subscription lifecycle states we track."""

    CREATED = "created"
    AUTHENTICATED = "authenticated"
    ACTIVE = "active"
    PENDING = "pending"
    HALTED = "halted"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    EXPIRED = "expired"
    PAUSED = "paused"


class EventProcessingStatus(StrEnum):
    """Processing state of an ingested webhook event."""

    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class DiagnosisCategory(StrEnum):
    """Small, fixed set of failure root causes."""

    INSUFFICIENT_FUNDS = "insufficient_funds"
    EXPIRED_OR_INVALID_CARD = "expired_or_invalid_card"
    BANK_OR_GATEWAY_ERROR = "bank_or_gateway_error"
    MANDATE_REVOKED = "mandate_revoked"
    OTHER = "other"


class DiagnosisSource(StrEnum):
    """Whether a diagnosis came from the deterministic rules or the LLM."""

    RULES = "rules"
    LLM = "llm"


class ActionType(StrEnum):
    """Bounded set of recovery interventions the policy may choose."""

    RETRY_CHARGE = "retry_charge"
    SEND_PAYMENT_LINK = "send_payment_link"
    ESCALATE_MANUAL = "escalate_manual"
    MARK_DEAD = "mark_dead"


class ActionStatus(StrEnum):
    """Execution state of a recovery action."""

    PLANNED = "planned"
    SCHEDULED = "scheduled"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class OutcomeStatus(StrEnum):
    """Terminal result of a recovery episode (feeds the metrics)."""

    RECOVERED = "recovered"
    STILL_AT_RISK = "still_at_risk"
    ESCALATED = "escalated"
    DEAD = "dead"


class AuditActor(StrEnum):
    """Who performed an audited action."""

    SYSTEM = "system"
    GUARDRAIL = "guardrail"
    LLM = "llm"
    WORKER = "worker"
    API = "api"
    RAZORPAY_WEBHOOK = "razorpay_webhook"
