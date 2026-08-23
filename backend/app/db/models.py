"""SQLAlchemy ORM models — the RecoverAI schema (7 tables).

Design notes:
- Money is stored as integer paise (``BigInteger``); ``currency`` defaults INR.
- Enum columns use ``VARCHAR + CHECK`` (``native_enum=False``) so the value set
  is easy to evolve mid-build without a native-enum migration. ``values_callable``
  makes Postgres store the enum *value* (e.g. ``insufficient_funds``), not the
  member name.
- The failure -> recovery chain is reconstructable purely via foreign keys:
  ``payment_events`` -> ``diagnoses`` -> ``recovery_actions`` -> ``recovery_outcomes``,
  all also joinable by ``subscription_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.enums import (
    ActionStatus,
    ActionType,
    AuditActor,
    DiagnosisCategory,
    DiagnosisSource,
    EventProcessingStatus,
    OutcomeStatus,
    SubscriptionStatus,
)


def _enum(enum_cls: type[PyEnum], name: str) -> sa.Enum:
    """Build a portable ``VARCHAR + CHECK`` enum type storing member values."""
    return sa.Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        length=32,
        name=name,
        validate_strings=True,
        values_callable=lambda e: [member.value for member in e],
    )


class UUIDPKMixin:
    """Adds a UUID primary key generated in the database."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


class TimestampMixin:
    """Adds ``created_at`` / ``updated_at`` audit columns."""

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )


class Merchant(Base, UUIDPKMixin, TimestampMixin):
    """A Razorpay merchant/account using RecoverAI."""

    __tablename__ = "merchants"

    name: Mapped[str | None] = mapped_column(sa.String(255))
    razorpay_account_id: Mapped[str | None] = mapped_column(sa.String(64), unique=True, index=True)

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="merchant")


class Subscription(Base, UUIDPKMixin, TimestampMixin):
    """A Razorpay subscription we monitor and recover."""

    __tablename__ = "subscriptions"

    merchant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("merchants.id", ondelete="SET NULL"), index=True
    )
    razorpay_subscription_id: Mapped[str] = mapped_column(
        sa.String(64), unique=True, nullable=False
    )
    razorpay_customer_id: Mapped[str | None] = mapped_column(sa.String(64))
    plan_id: Mapped[str | None] = mapped_column(sa.String(64))
    status: Mapped[SubscriptionStatus | None] = mapped_column(
        _enum(SubscriptionStatus, "subscription_status"), index=True
    )
    total_count: Mapped[int | None] = mapped_column(sa.Integer)
    paid_count: Mapped[int | None] = mapped_column(sa.Integer)
    remaining_count: Mapped[int | None] = mapped_column(sa.Integer)
    auth_attempts: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0"), nullable=False
    )
    amount: Mapped[int | None] = mapped_column(sa.BigInteger)  # paise
    currency: Mapped[str] = mapped_column(sa.String(3), server_default="INR", nullable=False)
    current_start: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    current_end: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    charge_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    notes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    merchant: Mapped[Merchant | None] = relationship(back_populates="subscriptions")
    payment_events: Mapped[list[PaymentEvent]] = relationship(back_populates="subscription")


class PaymentEvent(Base, UUIDPKMixin):
    """Raw ingested Razorpay webhook event and the idempotency table.

    ``razorpay_event_id`` (from the ``x-razorpay-event-id`` header) carries a
    UNIQUE constraint: Razorpay retries delivery, so the DB is the source of
    truth for dedup.
    """

    __tablename__ = "payment_events"

    razorpay_event_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True
    )
    razorpay_subscription_id: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(sa.String(64))
    error_code: Mapped[str | None] = mapped_column(sa.String(64))
    error_description: Mapped[str | None] = mapped_column(sa.Text)
    amount: Mapped[int | None] = mapped_column(sa.BigInteger)  # paise
    currency: Mapped[str | None] = mapped_column(sa.String(3))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signature_verified: Mapped[bool] = mapped_column(
        sa.Boolean, server_default=sa.text("false"), nullable=False
    )
    processing_status: Mapped[EventProcessingStatus] = mapped_column(
        _enum(EventProcessingStatus, "event_processing_status"),
        server_default=EventProcessingStatus.PENDING.value,
        nullable=False,
    )
    received_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    razorpay_created_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        # Idempotency guarantee: one row per Razorpay event id.
        UniqueConstraint("razorpay_event_id", name="uq_payment_events_razorpay_event_id"),
    )

    subscription: Mapped[Subscription | None] = relationship(back_populates="payment_events")


class Diagnosis(Base, UUIDPKMixin):
    """Root-cause classification of a failed payment event."""

    __tablename__ = "diagnoses"

    payment_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("payment_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[DiagnosisCategory] = mapped_column(
        _enum(DiagnosisCategory, "diagnosis_category"), nullable=False
    )
    source: Mapped[DiagnosisSource] = mapped_column(
        _enum(DiagnosisSource, "diagnosis_source"), nullable=False
    )
    confidence: Mapped[float | None] = mapped_column(sa.Float)
    reasoning: Mapped[str | None] = mapped_column(sa.Text)  # LLM reasoning string
    llm_model: Mapped[str | None] = mapped_column(sa.String(64))
    llm_raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    payment_event: Mapped[PaymentEvent] = relationship()
    subscription: Mapped[Subscription] = relationship()


class RecoveryAction(Base, UUIDPKMixin, TimestampMixin):
    """A bounded recovery intervention chosen by the policy and executed."""

    __tablename__ = "recovery_actions"

    diagnosis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("diagnoses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_type: Mapped[ActionType] = mapped_column(
        _enum(ActionType, "action_type"), nullable=False
    )
    status: Mapped[ActionStatus] = mapped_column(
        _enum(ActionStatus, "action_status"),
        server_default=ActionStatus.PLANNED.value,
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("1"), nullable=False
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    razorpay_request: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    razorpay_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    razorpay_payment_link_id: Mapped[str | None] = mapped_column(sa.String(64))
    guardrail_decision: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        # Supports the Phase-4 poller:
        #   WHERE status='scheduled' AND scheduled_at <= now() ... FOR UPDATE SKIP LOCKED
        Index("ix_recovery_actions_status_scheduled_at", "status", "scheduled_at"),
    )

    diagnosis: Mapped[Diagnosis] = relationship()
    subscription: Mapped[Subscription] = relationship()


class RecoveryOutcome(Base, UUIDPKMixin, TimestampMixin):
    """Terminal result of a recovery episode (feeds the metrics)."""

    __tablename__ = "recovery_outcomes"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recovery_action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recovery_actions.id", ondelete="SET NULL"), index=True
    )
    triggering_payment_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_events.id", ondelete="SET NULL")
    )
    outcome: Mapped[OutcomeStatus] = mapped_column(
        _enum(OutcomeStatus, "outcome_status"), nullable=False
    )
    amount_recovered: Mapped[int] = mapped_column(
        sa.BigInteger, server_default=sa.text("0"), nullable=False
    )
    amount_at_risk: Mapped[int] = mapped_column(
        sa.BigInteger, server_default=sa.text("0"), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    subscription: Mapped[Subscription] = relationship()
    recovery_action: Mapped[RecoveryAction | None] = relationship()


class AuditLog(Base):
    """Append-only audit trail.

    Every money-moving action is written here *before* execution. Uses a
    monotonic ``BIGINT`` identity key for stable ordering.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    event_time: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True
    )
    actor: Mapped[AuditActor] = mapped_column(_enum(AuditActor, "audit_actor"), nullable=False)
    action: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True
    )
    payment_event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_events.id", ondelete="SET NULL")
    )
    diagnosis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("diagnoses.id", ondelete="SET NULL")
    )
    recovery_action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recovery_actions.id", ondelete="SET NULL")
    )
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
