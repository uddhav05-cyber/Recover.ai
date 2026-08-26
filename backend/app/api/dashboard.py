"""Read-only metrics for the recovery dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaymentEvent, RecoveryAction, RecoveryOutcome
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