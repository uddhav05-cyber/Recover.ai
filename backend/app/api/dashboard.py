"""Read-only metrics for the recovery dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaymentEvent, RecoveryAction, RecoveryOutcome, Subscription
from app.db.session import get_session
from app.enums import ActionStatus, OutcomeStatus

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def summary(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Return the aggregate counters used by the first dashboard view."""
    failed_payments = await session.scalar(
        select(func.count(PaymentEvent.id)).where(PaymentEvent.event_type == "payment.failed")
    )
    recovered = await session.scalar(
        select(func.coalesce(func.sum(RecoveryOutcome.amount_recovered), 0)).where(
            RecoveryOutcome.outcome == OutcomeStatus.RECOVERED
        )
    )
    at_risk = await session.scalar(
        select(func.coalesce(func.sum(RecoveryOutcome.amount_at_risk), 0)).where(
            RecoveryOutcome.outcome == OutcomeStatus.STILL_AT_RISK
        )
    )
    active_actions = await session.scalar(
        select(func.count(RecoveryAction.id)).where(
            RecoveryAction.status.in_(
                (ActionStatus.PLANNED, ActionStatus.SCHEDULED, ActionStatus.EXECUTING)
            )
        )
    )

    return {
        "failed_payments": int(failed_payments or 0),
        "amount_recovered_paise": int(recovered or 0),
        "amount_at_risk_paise": int(at_risk or 0),
        "active_actions": int(active_actions or 0),
    }


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Return the dashboard's funnel, recent subscriptions, and exceptions."""
    failed = await session.scalar(
        select(func.count(PaymentEvent.id)).where(PaymentEvent.event_type == "payment.failed")
    )
    actioned = await session.scalar(select(func.count(RecoveryAction.id)))
    recovered_count = await session.scalar(
        select(func.count(RecoveryOutcome.id)).where(
            RecoveryOutcome.outcome == OutcomeStatus.RECOVERED
        )
    )

    subscriptions = (
        await session.scalars(
            select(Subscription).order_by(Subscription.updated_at.desc()).limit(20)
        )
    ).all()
    exceptions = (
        await session.execute(
            select(RecoveryOutcome, Subscription.razorpay_subscription_id)
            .join(Subscription, Subscription.id == RecoveryOutcome.subscription_id)
            .where(RecoveryOutcome.outcome != OutcomeStatus.RECOVERED)
            .order_by(RecoveryOutcome.created_at.desc())
            .limit(20)
        )
    ).all()

    return {
        "funnel": {
            "failed": int(failed or 0),
            "actioned": int(actioned or 0),
            "recovered": int(recovered_count or 0),
        },
        "subscriptions": [
            {
                "id": str(subscription.id),
                "razorpay_subscription_id": subscription.razorpay_subscription_id,
                "status": subscription.status.value if subscription.status else "unknown",
                "amount_paise": subscription.amount or 0,
                "updated_at": subscription.updated_at.isoformat(),
            }
            for subscription in subscriptions
        ],
        "exceptions": [
            {
                "id": str(outcome.id),
                "subscription_id": subscription_id,
                "outcome": outcome.outcome.value,
                "amount_at_risk_paise": outcome.amount_at_risk,
                "resolved_at": outcome.resolved_at.isoformat() if outcome.resolved_at else None,
            }
            for outcome, subscription_id in exceptions
        ],
    }