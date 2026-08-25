"""Phase 4 scheduler tests: due-retry claiming (concurrency-safe) + execution.

These drive :mod:`app.worker.retry_scheduler`. Most run against the rolled-back
``db_session`` with a fake Razorpay client. The load-bearing exception is
:func:`test_concurrent_claim_never_double_executes`, which uses *two* real
connections against Postgres to prove ``SELECT ... FOR UPDATE SKIP LOCKED`` never
hands the same scheduled retry to two workers — the Phase-4 guarantee.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.db.models import (
    AuditLog,
    Diagnosis,
    PaymentEvent,
    RecoveryAction,
    RecoveryOutcome,
    Subscription,
)
from app.db.session import make_async_engine
from app.enums import (
    ActionStatus,
    ActionType,
    DiagnosisCategory,
    DiagnosisSource,
    OutcomeStatus,
    SubscriptionStatus,
)
from app.worker.retry_scheduler import claim_due_retries, execute_retry, run_due_retries

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
PAST = NOW - timedelta(hours=1)
FUTURE = NOW + timedelta(hours=1)


class _FakeClient:
    """Stand-in for RazorpayClient: canned subscription status + payment link."""

    def __init__(
        self,
        *,
        status: str = "halted",
        fetch_error: Exception | None = None,
        link_response: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.fetch_error = fetch_error
        self.link_response = link_response or {
            "id": "plink_retry",
            "short_url": "https://rzp.io/i/retry",
        }
        self.fetch_calls: list[str] = []
        self.link_calls: list[dict[str, Any]] = []

    async def fetch_subscription(self, subscription_id: str) -> dict[str, Any]:
        self.fetch_calls.append(subscription_id)
        if self.fetch_error is not None:
            raise self.fetch_error
        return {"id": subscription_id, "status": self.status}

    async def create_payment_link(
        self,
        *,
        amount: int,
        currency: str = "INR",
        description: str | None = None,
        customer: dict[str, Any] | None = None,
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.link_calls.append({"amount": amount})
        return self.link_response


async def _seed_retry(
    session: AsyncSession,
    *,
    scheduled_at: datetime = PAST,
    status: ActionStatus = ActionStatus.SCHEDULED,
    action_type: ActionType = ActionType.RETRY_CHARGE,
    category: DiagnosisCategory = DiagnosisCategory.INSUFFICIENT_FUNDS,
    amount: int = 79900,
    prior_attempts: int = 0,
    sub_status: SubscriptionStatus = SubscriptionStatus.HALTED,
    nonce: str | None = None,
) -> tuple[RecoveryAction, Subscription, Diagnosis, PaymentEvent]:
    """Seed a subscription + diagnosis + a due retry (plus optional prior failures).

    ``prior_attempts`` seeds that many already-FAILED retry rows, so the retry
    history count (which bounds attempts) reflects an in-progress ladder.
    """
    nonce = nonce or uuid.uuid4().hex[:8]
    sub = Subscription(
        razorpay_subscription_id=f"sub_{nonce}", status=sub_status, amount=amount
    )
    session.add(sub)
    await session.flush()
    pe = PaymentEvent(
        razorpay_event_id=f"evt_{nonce}",
        event_type="payment.failed",
        razorpay_subscription_id=sub.razorpay_subscription_id,
        subscription_id=sub.id,
        amount=amount,
        currency="INR",
        payload={},
        error_description="insufficient funds",
    )
    session.add(pe)
    await session.flush()
    dx = Diagnosis(
        payment_event_id=pe.id,
        subscription_id=sub.id,
        category=category,
        source=DiagnosisSource.RULES,
        confidence=0.97,
        reasoning="seed",
    )
    session.add(dx)
    await session.flush()

    for i in range(prior_attempts):
        session.add(
            RecoveryAction(
                diagnosis_id=dx.id,
                subscription_id=sub.id,
                action_type=ActionType.RETRY_CHARGE,
                status=ActionStatus.FAILED,
                attempt_number=i + 1,
                executed_at=PAST,
            )
        )
    action = RecoveryAction(
        diagnosis_id=dx.id,
        subscription_id=sub.id,
        action_type=action_type,
        status=status,
        attempt_number=prior_attempts + 1,
        scheduled_at=scheduled_at,
    )
    session.add(action)
    await session.flush()
    return action, sub, dx, pe


# --- claim: only due, scheduled retries -------------------------------------


async def test_claim_only_returns_due_scheduled_retries(db_session: AsyncSession) -> None:
    due, _, _, _ = await _seed_retry(db_session, scheduled_at=PAST, nonce="due1")
    future, _, _, _ = await _seed_retry(db_session, scheduled_at=FUTURE, nonce="fut1")
    planned, _, _, _ = await _seed_retry(
        db_session, scheduled_at=PAST, status=ActionStatus.PLANNED, nonce="pln1"
    )

    claimed = await claim_due_retries(db_session, now=NOW, limit=1000)

    assert due.id in claimed
    assert future.id not in claimed  # not yet due
    assert planned.id not in claimed  # not in the 'scheduled' state

    refreshed = await db_session.get(RecoveryAction, due.id)
    assert refreshed is not None
    assert refreshed.status is ActionStatus.EXECUTING  # claimed


# --- execute: recovery when the mandate is active again ---------------------


async def test_execute_retry_recovers_when_subscription_active(db_session: AsyncSession) -> None:
    action, sub, _, _ = await _seed_retry(db_session, amount=79900, nonce="rec1")
    client = _FakeClient(status="active")

    results = await run_due_retries(db_session, now=NOW, client=client, limit=1000)
    mine = next(r for r in results if r.action_id == action.id)

    assert mine.recovered is True
    assert mine.action_status is ActionStatus.EXECUTED
    assert mine.outcome is OutcomeStatus.RECOVERED
    assert sub.razorpay_subscription_id in client.fetch_calls  # re-checked live status

    refreshed = await db_session.get(RecoveryAction, action.id)
    assert refreshed is not None
    assert refreshed.status is ActionStatus.EXECUTED

    outcome = await db_session.scalar(
        select(RecoveryOutcome).where(RecoveryOutcome.recovery_action_id == action.id)
    )
    assert outcome is not None
    assert outcome.outcome is OutcomeStatus.RECOVERED
    assert outcome.amount_recovered == 79900

    audits = (
        await db_session.scalars(
            select(AuditLog.action).where(AuditLog.recovery_action_id == action.id)
        )
    ).all()
    assert "recovery.retry.attempted" in audits
    assert "recovery.retry.recovered" in audits


# --- execute: still failing, within bounds -> schedule the next retry -------


async def test_failed_retry_schedules_next_bounded_retry(db_session: AsyncSession) -> None:
    # First attempt (attempt_number=1); still halted after the backoff.
    action, _, _, _ = await _seed_retry(db_session, prior_attempts=0, amount=50000, nonce="nxt1")
    client = _FakeClient(status="halted")

    results = await run_due_retries(db_session, now=NOW, client=client, limit=1000)
    mine = next(r for r in results if r.action_id == action.id)

    assert mine.recovered is False
    assert mine.follow_up_action_type is ActionType.RETRY_CHARGE

    orig = await db_session.get(RecoveryAction, action.id)
    assert orig is not None
    assert orig.status is ActionStatus.FAILED

    assert mine.follow_up_action_id is not None
    follow = await db_session.get(RecoveryAction, mine.follow_up_action_id)
    assert follow is not None
    assert follow.status is ActionStatus.SCHEDULED
    assert follow.attempt_number == 2
    # Funds backoff, second entry: 24h.
    assert follow.scheduled_at == NOW + timedelta(hours=24)


# --- execute: retries exhausted -> payment link (real Razorpay call) --------


async def test_failed_retry_sends_payment_link_when_exhausted(db_session: AsyncSession) -> None:
    # Two prior failures + this one (attempt 3) = 3 retries used == max_retries.
    action, _, _, _ = await _seed_retry(db_session, prior_attempts=2, amount=60000, nonce="exh1")
    client = _FakeClient(status="halted")

    results = await run_due_retries(db_session, now=NOW, client=client, limit=1000)
    mine = next(r for r in results if r.action_id == action.id)

    assert mine.recovered is False
    assert mine.follow_up_action_type is ActionType.SEND_PAYMENT_LINK
    assert len(client.link_calls) == 1
    assert client.link_calls[0]["amount"] == 60000

    assert mine.follow_up_action_id is not None
    follow = await db_session.get(RecoveryAction, mine.follow_up_action_id)
    assert follow is not None
    assert follow.status is ActionStatus.EXECUTED
    assert follow.razorpay_payment_link_id == "plink_retry"


# --- audit precedes execution, even on a transport error --------------------


async def test_transport_error_leaves_pre_execution_audit(db_session: AsyncSession) -> None:
    action, _, _, _ = await _seed_retry(db_session, nonce="err1")
    client = _FakeClient(fetch_error=httpx.HTTPError("razorpay down"))

    results = await run_due_retries(db_session, now=NOW, client=client, limit=1000)
    mine = next(r for r in results if r.action_id == action.id)

    assert mine.action_status is ActionStatus.FAILED
    a = await db_session.get(RecoveryAction, action.id)
    assert a is not None
    assert a.status is ActionStatus.FAILED
    assert "razorpay down" in (a.error or "")

    audits = (
        await db_session.scalars(
            select(AuditLog.action).where(AuditLog.recovery_action_id == action.id)
        )
    ).all()
    # The "attempted" row was committed before the (failing) fetch — durable intent.
    assert "recovery.retry.attempted" in audits
    assert "recovery.retry.failed" in audits


# --- no client wired -> defer -----------------------------------------------


async def test_no_client_defers_retry(db_session: AsyncSession) -> None:
    action, _, _, _ = await _seed_retry(db_session, nonce="def1")

    claimed = await claim_due_retries(db_session, now=NOW, limit=1000)
    assert action.id in claimed

    res = await execute_retry(db_session, action.id, now=NOW, client=None)
    assert res.action_status is ActionStatus.PLANNED

    a = await db_session.get(RecoveryAction, action.id)
    assert a is not None
    assert a.status is ActionStatus.PLANNED
    assert "deferred" in (a.error or "")


# --- concurrency: FOR UPDATE SKIP LOCKED never double-claims ----------------


async def test_concurrent_claim_never_double_executes() -> None:
    """Two workers claiming concurrently must partition due rows, never share one.

    Real Postgres, two independent transactions, ``FOR UPDATE SKIP LOCKED``. The
    rows are scheduled in the year 2000 so only this test's rows are due at
    ``now_2000`` (no real retry is scheduled then); cleanup is scoped to a
    per-run nonce so nothing else is touched.
    """
    engine = make_async_engine(poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    nonce = uuid.uuid4().hex[:12]
    rzp_sub_id = f"sub_cc_{nonce}"
    event_id = f"evt_cc_{nonce}"
    now_2000 = datetime(2000, 1, 2, tzinfo=UTC)
    sched_2000 = datetime(2000, 1, 1, tzinfo=UTC)
    n_rows = 8

    mine: set[uuid.UUID] = set()
    sub_id: uuid.UUID | None = None
    try:
        async with maker() as s:
            sub = Subscription(
                razorpay_subscription_id=rzp_sub_id,
                status=SubscriptionStatus.HALTED,
                amount=1000,
            )
            s.add(sub)
            await s.flush()
            sub_id = sub.id
            pe = PaymentEvent(
                razorpay_event_id=event_id,
                event_type="payment.failed",
                razorpay_subscription_id=rzp_sub_id,
                subscription_id=sub.id,
                amount=1000,
                currency="INR",
                payload={},
            )
            s.add(pe)
            await s.flush()
            dx = Diagnosis(
                payment_event_id=pe.id,
                subscription_id=sub.id,
                category=DiagnosisCategory.INSUFFICIENT_FUNDS,
                source=DiagnosisSource.RULES,
            )
            s.add(dx)
            await s.flush()
            for _ in range(n_rows):
                a = RecoveryAction(
                    diagnosis_id=dx.id,
                    subscription_id=sub.id,
                    action_type=ActionType.RETRY_CHARGE,
                    status=ActionStatus.SCHEDULED,
                    scheduled_at=sched_2000,
                )
                s.add(a)
                await s.flush()
                mine.add(a.id)
            await s.commit()

        # Two workers race to claim the same due rows.
        async with maker() as s1, maker() as s2:
            r1, r2 = await asyncio.gather(
                claim_due_retries(s1, now=now_2000, limit=10_000),
                claim_due_retries(s2, now=now_2000, limit=10_000),
            )

        c1, c2 = set(r1) & mine, set(r2) & mine
        assert c1.isdisjoint(c2)  # no seeded row claimed by both workers
        assert c1 | c2 == mine  # every seeded row claimed by exactly one worker
    finally:
        async with maker() as s:
            if sub_id is not None:
                await s.execute(delete(AuditLog).where(AuditLog.subscription_id == sub_id))
                await s.execute(
                    delete(RecoveryOutcome).where(RecoveryOutcome.subscription_id == sub_id)
                )
                await s.execute(
                    delete(RecoveryAction).where(RecoveryAction.subscription_id == sub_id)
                )
                await s.execute(delete(Diagnosis).where(Diagnosis.subscription_id == sub_id))
            await s.execute(
                delete(PaymentEvent).where(PaymentEvent.razorpay_subscription_id == rzp_sub_id)
            )
            await s.execute(
                delete(Subscription).where(Subscription.razorpay_subscription_id == rzp_sub_id)
            )
            await s.commit()
        await engine.dispose()
