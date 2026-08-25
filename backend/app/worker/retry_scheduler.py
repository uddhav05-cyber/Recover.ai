"""Phase 4 — the delayed-retry scheduler (background worker).

A ``retry_charge`` action chosen in Phase 3 is not executed inline; it is
*scheduled* (``status='scheduled'`` with ``scheduled_at`` in the future) so a
bounded backoff can elapse. This module is the worker that later executes those
due retries — safely under concurrency.

**Concurrency safety (the centerpiece of Phase 4).**
Due retries are claimed with ``SELECT ... FOR UPDATE SKIP LOCKED``. A worker locks
a batch of ``scheduled`` rows and flips them to ``executing`` in the *same*
transaction; a second worker's ``SKIP LOCKED`` select passes over the locked
rows, and once the first worker commits they are no longer ``scheduled`` — so a
scheduled retry is executed by exactly one worker, never twice.

**What "executing a retry" means (honest, test-mode only).**
Razorpay Subscriptions exposes no public "force-charge this mandate now" API;
Razorpay itself re-attempts halted subscriptions on its own schedule. So a due
retry *re-checks the live subscription* (a real ``GET /subscriptions/:id``): if
the mandate is active again the charge recovered; if it is still halted the retry
did not land, and we fall back through the same Decide -> Guardrail -> Act ladder
(:func:`app.agent.executor.apply_decision`) — the next bounded retry, a payment
link once retries are exhausted, or escalation. This never fabricates an API
Razorpay does not expose.

**Audit precedes execution.** The ``recovery.retry.attempted`` audit row is
committed *before* the Razorpay fetch, mirroring the executor's invariant.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.executor import _audit, _retry_history, _to_sub_status, apply_decision
from app.agent.guardrail import GuardrailConfig
from app.db.models import Diagnosis, PaymentEvent, RecoveryAction, RecoveryOutcome, Subscription
from app.enums import ActionStatus, ActionType, AuditActor, OutcomeStatus, SubscriptionStatus

logger = logging.getLogger(__name__)

# Subscription states in which the mandate can charge again -> the retry recovered.
_RECOVERED_STATES: frozenset[SubscriptionStatus] = frozenset(
    {
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.AUTHENTICATED,
        SubscriptionStatus.COMPLETED,
    }
)


class SubscriptionClient(Protocol):
    """The Razorpay surface the scheduler needs (satisfied by ``RazorpayClient``)."""

    async def fetch_subscription(self, subscription_id: str) -> dict[str, Any]: ...

    async def create_payment_link(
        self,
        *,
        amount: int,
        currency: str = "INR",
        description: str | None = None,
        customer: dict[str, Any] | None = None,
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RetryExecution:
    """Summary of one due-retry execution."""

    action_id: uuid.UUID
    recovered: bool
    action_status: ActionStatus
    outcome: OutcomeStatus | None = None
    follow_up_action_id: uuid.UUID | None = None
    follow_up_action_type: ActionType | None = None
    detail: str = ""


async def claim_due_retries(
    session: AsyncSession, *, now: datetime, limit: int = 10
) -> list[uuid.UUID]:
    """Atomically claim due scheduled retries; returns the claimed action ids.

    The claim is the concurrency-safe heart of the worker: it locks matching rows
    with ``FOR UPDATE SKIP LOCKED`` and flips them to ``executing`` in the same
    transaction. After the commit those rows are no longer ``scheduled``, so a
    concurrent worker can never pick them up — each due retry is claimed once.
    """
    stmt = (
        select(RecoveryAction)
        .where(
            RecoveryAction.status == ActionStatus.SCHEDULED,
            RecoveryAction.action_type == ActionType.RETRY_CHARGE,
            RecoveryAction.scheduled_at <= now,
        )
        .order_by(RecoveryAction.scheduled_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = (await session.scalars(stmt)).all()
    claimed: list[uuid.UUID] = []
    for action in rows:
        action.status = ActionStatus.EXECUTING
        claimed.append(action.id)
    await session.commit()
    return claimed


async def execute_retry(
    session: AsyncSession,
    action_id: uuid.UUID,
    *,
    now: datetime,
    client: SubscriptionClient | None,
    config: GuardrailConfig | None = None,
) -> RetryExecution:
    """Execute one claimed retry: re-check the subscription, recover or re-ladder."""
    config = config or GuardrailConfig()

    action = await session.get(RecoveryAction, action_id)
    assert action is not None
    diagnosis_id = action.diagnosis_id
    subscription_id = action.subscription_id
    attempt_number = action.attempt_number

    diagnosis = await session.get(Diagnosis, diagnosis_id)
    assert diagnosis is not None
    category = diagnosis.category
    triggering_event_id = diagnosis.payment_event_id

    subscription = await session.get(Subscription, subscription_id)
    assert subscription is not None
    rzp_sub_id = subscription.razorpay_subscription_id

    # The failed charge amount (what we stand to recover) lives on the trigger event.
    event = await session.get(PaymentEvent, triggering_event_id)
    amount = event.amount if event is not None else None
    currency = (event.currency if event is not None else None) or "INR"

    # Audit BEFORE the external call: durable intent even if we crash mid-call.
    action.razorpay_request = {"op": "fetch_subscription", "subscription_id": rzp_sub_id}
    session.add(
        _audit(
            actor=AuditActor.WORKER,
            action="recovery.retry.attempted",
            subscription_id=subscription_id,
            payment_event_id=triggering_event_id,
            recovery_action_id=action_id,
            detail={"attempt_number": attempt_number, "razorpay_subscription_id": rzp_sub_id},
        )
    )
    await session.commit()

    if client is None:
        # No client wired (e.g. no test keys yet): hand the retry back for later.
        deferred = await session.get(RecoveryAction, action_id)
        assert deferred is not None
        deferred.status = ActionStatus.PLANNED
        deferred.error = "no Razorpay client configured; retry deferred"
        await session.commit()
        return RetryExecution(
            action_id=action_id,
            recovered=False,
            action_status=ActionStatus.PLANNED,
            detail="deferred: no Razorpay client",
        )

    try:
        sub_data = await client.fetch_subscription(rzp_sub_id)
    except httpx.HTTPError as exc:
        failed = await session.get(RecoveryAction, action_id)
        assert failed is not None
        failed.status = ActionStatus.FAILED
        failed.executed_at = now
        failed.error = f"razorpay fetch_subscription error: {exc!r}"
        session.add(
            _audit(
                actor=AuditActor.WORKER,
                action="recovery.retry.failed",
                subscription_id=subscription_id,
                payment_event_id=triggering_event_id,
                recovery_action_id=action_id,
                detail={"error": str(exc)},
            )
        )
        await session.commit()
        return RetryExecution(
            action_id=action_id,
            recovered=False,
            action_status=ActionStatus.FAILED,
            detail="fetch error; left for a later poll",
        )

    raw_status = sub_data.get("status")
    sub_status = _to_sub_status(raw_status if isinstance(raw_status, str) else None)

    current = await session.get(RecoveryAction, action_id)
    assert current is not None
    sub_row = await session.get(Subscription, subscription_id)
    if sub_row is not None and sub_status is not None:
        sub_row.status = sub_status  # keep our mirror of Razorpay state fresh

    if sub_status in _RECOVERED_STATES:
        current.status = ActionStatus.EXECUTED
        current.executed_at = now
        current.razorpay_response = sub_data
        session.add(
            RecoveryOutcome(
                subscription_id=subscription_id,
                recovery_action_id=action_id,
                triggering_payment_event_id=triggering_event_id,
                outcome=OutcomeStatus.RECOVERED,
                amount_recovered=amount or 0,
                resolved_at=now,
            )
        )
        session.add(
            _audit(
                actor=AuditActor.WORKER,
                action="recovery.retry.recovered",
                subscription_id=subscription_id,
                payment_event_id=triggering_event_id,
                recovery_action_id=action_id,
                detail={"status": raw_status, "amount_recovered": amount or 0},
            )
        )
        await session.commit()
        return RetryExecution(
            action_id=action_id,
            recovered=True,
            action_status=ActionStatus.EXECUTED,
            outcome=OutcomeStatus.RECOVERED,
            detail=f"recovered (subscription status={raw_status})",
        )

    # Not recovered: record this attempt as failed, then re-enter the bounded ladder.
    current.status = ActionStatus.FAILED
    current.executed_at = now
    current.razorpay_response = sub_data
    current.error = f"retry did not recover; subscription status={raw_status}"
    session.add(
        _audit(
            actor=AuditActor.WORKER,
            action="recovery.retry.failed",
            subscription_id=subscription_id,
            payment_event_id=triggering_event_id,
            recovery_action_id=action_id,
            detail={"status": raw_status},
        )
    )
    await session.flush()

    # Re-decide from the canonical retry count (all retry rows to date), reusing the
    # executor's Decide -> Guardrail -> Act core so behaviour never diverges.
    #
    # last_attempt_at is deliberately None here. This attempt just executed at
    # ``now``, but the *next* retry is SCHEDULED at ``now + backoff`` — and in this
    # re-ladder path attempts_so_far >= 1, so the backoff is >= 24h (funds) / >= 2h
    # (gateway), always exceeding the 1h cooldown. Spacing is therefore enforced by
    # the schedule, not the cooldown floor (which exists to stop *immediate*
    # re-execution on the webhook path). Passing ``now`` would make the guardrail
    # deny the next step as COOLDOWN_ACTIVE and stall recovery with no follow-up
    # scheduled; the max-retries bound (attempts_so_far) still terminates the ladder.
    attempts_so_far, _ = await _retry_history(session, subscription_id)
    applied = await apply_decision(
        session,
        now=now,
        client=client,
        config=config,
        diagnosis_id=diagnosis_id,
        subscription_id=subscription_id,
        category=category,
        subscription_status=sub_status,
        attempts_so_far=attempts_so_far,
        last_attempt_at=None,
        amount=amount,
        currency=currency,
        rzp_sub_id=rzp_sub_id,
        triggering_event_id=triggering_event_id,
    )
    await session.commit()

    return RetryExecution(
        action_id=action_id,
        recovered=False,
        action_status=ActionStatus.FAILED,
        outcome=_outcome_for(applied.action_type, applied.status),
        follow_up_action_id=applied.action_id,
        follow_up_action_type=applied.action_type,
        detail=applied.rationale,
    )


def _outcome_for(action_type: ActionType, status: ActionStatus) -> OutcomeStatus | None:
    """Terminal outcome recorded by :func:`apply_decision` for this follow-up, if any."""
    if status is ActionStatus.EXECUTED and action_type is ActionType.ESCALATE_MANUAL:
        return OutcomeStatus.ESCALATED
    if status is ActionStatus.EXECUTED and action_type is ActionType.MARK_DEAD:
        return OutcomeStatus.DEAD
    return None


async def run_due_retries(
    session: AsyncSession,
    *,
    now: datetime,
    client: SubscriptionClient | None,
    config: GuardrailConfig | None = None,
    limit: int = 10,
) -> list[RetryExecution]:
    """Claim and execute one batch of due retries; returns a summary per retry."""
    claimed = await claim_due_retries(session, now=now, limit=limit)
    results: list[RetryExecution] = []
    for action_id in claimed:
        results.append(
            await execute_retry(session, action_id, now=now, client=client, config=config)
        )
    return results


async def poll_forever(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    client: SubscriptionClient | None,
    config: GuardrailConfig | None = None,
    interval: float = 30.0,
    limit: int = 10,
    clock: Callable[[], datetime] | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """Poll for due retries forever (until ``stop`` is set), one batch per interval.

    A per-iteration error is logged and swallowed so a transient DB/Razorpay
    blip never kills the worker; the next tick tries again.
    """
    tick = clock or (lambda: datetime.now(UTC))
    while stop is None or not stop.is_set():
        try:
            async with session_factory() as session:
                batch = await run_due_retries(
                    session, now=tick(), client=client, config=config, limit=limit
                )
            if batch:
                recovered = sum(1 for r in batch if r.recovered)
                logger.info(
                    "retry poll: executed %d due retries (%d recovered)",
                    len(batch),
                    recovered,
                )
        except Exception:  # noqa: BLE001 - a poll error must not kill the loop
            logger.exception("retry poll iteration failed")

        if stop is not None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass
        else:
            await asyncio.sleep(interval)
