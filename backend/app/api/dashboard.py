"""Read-only metrics for the recovery dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    Diagnosis,
    PaymentEvent,
    RecoveryAction,
    RecoveryOutcome,
    Subscription,
)
from app.db.session import get_session
from app.enums import ActionStatus, DiagnosisCategory, OutcomeStatus
from app.schemas.dashboard import AuthLoginRequest, AuthLoginResponse

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.post("/auth/login")
async def auth_login(request: AuthLoginRequest) -> AuthLoginResponse:
    """Stub Firebase Auth: return a demo token."""
    return AuthLoginResponse(
        access_token=f"demo_token_{request.email.replace('@', '_')}",
        user={"email": request.email, "name": request.email.split("@")[0]},
    )


@router.get("/dashboard/summary")
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


@router.get("/dashboard/overview")
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


@router.get("/subscriptions")
async def list_subscriptions(
    session: AsyncSession = Depends(get_session),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """List subscriptions with recovery status and pagination."""
    latest_outcome = (
        select(RecoveryOutcome.outcome)
        .where(RecoveryOutcome.subscription_id == Subscription.id)
        .order_by(RecoveryOutcome.resolved_at.desc().nullslast(), RecoveryOutcome.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    stmt = select(Subscription, latest_outcome.label("recovery_status")).offset(skip).limit(limit)
    rows = (await session.execute(stmt)).all()

    subscriptions_data: list[dict[str, Any]] = []
    for sub, recovery_status in rows:
        subscriptions_data.append(
            {
                "id": str(sub.id),
                "razorpay_subscription_id": sub.razorpay_subscription_id,
                "status": sub.status.value if sub.status else None,
                "amount_paise": sub.amount or 0,
                "currency": sub.currency,
                "recovery_status": recovery_status.value if recovery_status else "no_failures",
                "updated_at": sub.updated_at.isoformat(),
            }
        )

    total = await session.scalar(select(func.count()).select_from(Subscription))
    return {"items": subscriptions_data, "total": total, "skip": skip, "limit": limit}


@router.get("/recovery-metrics")
async def get_recovery_metrics(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get aggregate recovery metrics and funnel."""
    total_subs = await session.scalar(select(func.count()).select_from(Subscription))
    failed_events = await session.scalar(
        select(func.count())
        .select_from(PaymentEvent)
        .where(PaymentEvent.event_type == "payment.failed")
    )

    recovered = await session.scalar(
        select(func.count()).select_from(RecoveryOutcome).where(
            RecoveryOutcome.outcome == OutcomeStatus.RECOVERED
        )
    )
    escalated = await session.scalar(
        select(func.count()).select_from(RecoveryOutcome).where(
            RecoveryOutcome.outcome == OutcomeStatus.ESCALATED
        )
    )
    dead = await session.scalar(
        select(func.count()).select_from(RecoveryOutcome).where(
            RecoveryOutcome.outcome == OutcomeStatus.DEAD
        )
    )
    at_risk = await session.scalar(
        select(func.count()).select_from(RecoveryOutcome).where(
            RecoveryOutcome.outcome == OutcomeStatus.STILL_AT_RISK
        )
    )

    amount_recovered = await session.scalar(
        select(func.coalesce(func.sum(RecoveryOutcome.amount_recovered), 0)).where(
            RecoveryOutcome.outcome == OutcomeStatus.RECOVERED
        )
    )
    amount_at_risk = await session.scalar(
        select(func.coalesce(func.sum(RecoveryOutcome.amount_at_risk), 0)).where(
            RecoveryOutcome.outcome == OutcomeStatus.STILL_AT_RISK
        )
    )

    total_recovered = int(recovered or 0)
    recovery_rate = (total_recovered / int(failed_events or 1)) if failed_events else 0.0

    return {
        "total_subscriptions": int(total_subs or 0),
        "failed_events": int(failed_events or 0),
        "recovered_count": total_recovered,
        "recovery_rate": recovery_rate,
        "amount_recovered_paise": int(amount_recovered or 0),
        "amount_at_risk_paise": int(amount_at_risk or 0),
        "funnel": [
            {"stage": "failed", "count": int(failed_events or 0), "amount_paise": 0},
            {
                "stage": "recovered",
                "count": total_recovered,
                "amount_paise": int(amount_recovered or 0),
            },
            {"stage": "escalated", "count": int(escalated or 0), "amount_paise": 0},
            {"stage": "dead", "count": int(dead or 0), "amount_paise": 0},
            {
                "stage": "at_risk",
                "count": int(at_risk or 0),
                "amount_paise": int(amount_at_risk or 0),
            },
        ],
    }


@router.get("/exceptions")
async def list_exceptions(
    session: AsyncSession = Depends(get_session),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    category: DiagnosisCategory | None = Query(None),
    outcome_filter: OutcomeStatus | None = Query(None, alias="outcome"),
) -> dict[str, Any]:
    """List unresolved cases, optionally filtered by category or outcome."""
    exception_outcomes = [OutcomeStatus.STILL_AT_RISK, OutcomeStatus.ESCALATED]
    stmt = (
        select(
            RecoveryOutcome,
            Subscription.razorpay_subscription_id,
            PaymentEvent.amount,
            RecoveryAction.executed_at,
            RecoveryAction.error,
            Diagnosis.category,
        )
        .join(Subscription, Subscription.id == RecoveryOutcome.subscription_id)
        .join(RecoveryAction, RecoveryAction.id == RecoveryOutcome.recovery_action_id, isouter=True)
        .join(Diagnosis, Diagnosis.id == RecoveryAction.diagnosis_id, isouter=True)
        .join(
            PaymentEvent,
            PaymentEvent.id == RecoveryOutcome.triggering_payment_event_id,
            isouter=True,
        )
        .where(
            RecoveryOutcome.outcome == outcome_filter
            if outcome_filter is not None
            else RecoveryOutcome.outcome.in_(exception_outcomes)
        )
        .order_by(RecoveryOutcome.resolved_at.desc())
        .offset(skip)
        .limit(limit)
    )
    if category is not None:
        stmt = stmt.where(Diagnosis.category == category)
    rows = (await session.execute(stmt)).all()

    exceptions_data: list[dict[str, Any]] = []
    for recovery_outcome, subscription_id, amount, executed_at, action_error, category in rows:
        category = category or DiagnosisCategory.OTHER

        exceptions_data.append(
            {
                "id": str(recovery_outcome.id),
                "subscription_id": str(recovery_outcome.subscription_id),
                "razorpay_subscription_id": subscription_id,
                "category": category.value,
                "outcome": recovery_outcome.outcome.value,
                "amount_paise": amount or 0,
                "amount_at_risk_paise": recovery_outcome.amount_at_risk,
                "last_action_at": (
                    executed_at.isoformat() if executed_at else None
                ),
                "last_action_detail": action_error or "pending",
            }
        )

    total_stmt = (
        select(func.count())
        .select_from(RecoveryOutcome)
        .join(RecoveryAction, RecoveryAction.id == RecoveryOutcome.recovery_action_id, isouter=True)
        .join(Diagnosis, Diagnosis.id == RecoveryAction.diagnosis_id, isouter=True)
        .where(
            RecoveryOutcome.outcome == outcome_filter
            if outcome_filter is not None
            else RecoveryOutcome.outcome.in_(exception_outcomes)
        )
    )
    if category is not None:
        total_stmt = total_stmt.where(Diagnosis.category == category)
    total = await session.scalar(total_stmt)

    return {"items": exceptions_data, "total": total, "skip": skip, "limit": limit}


@router.get("/exceptions/{outcome_id}/audit")
async def exception_audit(
    outcome_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the ordered audit trail for one recovery exception."""
    outcome = await session.get(RecoveryOutcome, outcome_id)
    if outcome is None:
        return {"items": [], "total": 0}

    logs = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.subscription_id == outcome.subscription_id)
            .order_by(AuditLog.event_time.asc(), AuditLog.id.asc())
        )
    ).all()
    return {
        "items": [
            {
                "id": log.id,
                "event_time": log.event_time.isoformat(),
                "actor": log.actor.value,
                "action": log.action,
                "detail": log.detail,
            }
            for log in logs
        ],
        "total": len(logs),
    }