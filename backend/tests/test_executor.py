"""Act-step tests: the end-to-end recovery loop (executor).

These drive :func:`app.agent.executor.run_recovery` against the rolled-back
``db_session`` and a fake Razorpay client, asserting the persisted diagnosis /
action / outcome / audit rows. The load-bearing cases:

* the guardrail overrides the policy (a cancelled mandate blocks a proposed retry),
* the audit "requested" row is committed *before* the payment-link call, so it
  survives even when that call fails,
* retry attempts are counted from history and bounded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.executor import run_recovery
from app.agent.guardrail import GuardrailCode
from app.db.models import (
    AuditLog,
    Diagnosis,
    PaymentEvent,
    RecoveryAction,
    RecoveryOutcome,
)
from app.enums import (
    ActionStatus,
    ActionType,
    DiagnosisCategory,
    EventProcessingStatus,
    OutcomeStatus,
)

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class _FakeClient:
    """Stand-in for RazorpayClient.create_payment_link (records calls / can fail)."""

    def __init__(
        self, *, response: dict[str, object] | None = None, error: Exception | None = None
    ) -> None:
        self.response = response or {"id": "plink_test", "short_url": "https://rzp.io/i/test"}
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def create_payment_link(
        self,
        *,
        amount: int,
        currency: str = "INR",
        description: str | None = None,
        customer: dict[str, object] | None = None,
        notes: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.calls.append({"amount": amount, "currency": currency, "notes": notes})
        if self.error is not None:
            raise self.error
        return self.response


async def _make_event(
    session: AsyncSession,
    *,
    error_code: str | None = "BAD_REQUEST_ERROR",
    error_description: str | None = None,
    amount: int | None = 79900,
    currency: str = "INR",
    sub_status: str = "halted",
    event_id: str | None = None,
    payment_id: str | None = None,
    rzp_sub_id: str | None = None,
    processing_status: EventProcessingStatus = EventProcessingStatus.PENDING,
) -> PaymentEvent:
    event_id = event_id or f"evt_exec_{uuid4().hex}"
    payment_id = payment_id or f"pay_exec_{uuid4().hex}"
    rzp_sub_id = rzp_sub_id or f"sub_exec_{uuid4().hex}"
    payload = {
        "entity": "event",
        "account_id": "acc_TEST",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": amount,
                    "currency": currency,
                    "status": "failed",
                    "error_code": error_code,
                    "error_description": error_description,
                }
            },
            "subscription": {
                "entity": {"id": rzp_sub_id, "status": sub_status, "auth_attempts": 1}
            },
        },
        "created_at": 1690000000,
    }
    event = PaymentEvent(
        razorpay_event_id=event_id,
        event_type="payment.failed",
        razorpay_subscription_id=rzp_sub_id,
        razorpay_payment_id=payment_id,
        error_code=error_code,
        error_description=error_description,
        amount=amount,
        currency=currency,
        payload=payload,
        signature_verified=True,
        processing_status=processing_status,
    )
    session.add(event)
    await session.flush()
    return event


# --- insufficient funds -> bounded, scheduled retry -------------------------


async def test_insufficient_funds_schedules_bounded_retry(db_session: AsyncSession) -> None:
    event = await _make_event(db_session, error_description="insufficient funds")

    res = await run_recovery(db_session, event, now=NOW)

    assert res.diagnosis_category is DiagnosisCategory.INSUFFICIENT_FUNDS
    assert res.action_type is ActionType.RETRY_CHARGE
    assert res.guardrail_allowed is True
    assert res.action_status is ActionStatus.SCHEDULED
    assert res.scheduled_at == NOW + timedelta(hours=6)

    dx = await db_session.scalar(select(Diagnosis).where(Diagnosis.payment_event_id == event.id))
    assert dx is not None
    assert dx.category is DiagnosisCategory.INSUFFICIENT_FUNDS
    assert dx.reasoning  # the reasoning string is persisted

    diagnosis = await db_session.scalar(
        select(Diagnosis).where(Diagnosis.payment_event_id == event.id)
    )
    assert diagnosis is not None
    action = await db_session.scalar(
        select(RecoveryAction).where(RecoveryAction.diagnosis_id == diagnosis.id)
    )
    assert action is not None
    assert action.status is ActionStatus.SCHEDULED
    assert action.scheduled_at == NOW + timedelta(hours=6)
    assert action.guardrail_decision is not None
    assert action.guardrail_decision["allowed"] is True

    pe = await db_session.get(PaymentEvent, event.id)
    assert pe is not None
    assert pe.processing_status is EventProcessingStatus.PROCESSED

    audit_actions = (
        await db_session.scalars(
            select(AuditLog.action).where(AuditLog.payment_event_id == event.id)
        )
    ).all()
    assert "diagnosis.recorded" in audit_actions
    assert "guardrail.allow" in audit_actions


# --- expired card -> payment link via Razorpay ------------------------------


async def test_expired_card_creates_payment_link(db_session: AsyncSession) -> None:
    event = await _make_event(
        db_session, error_description="the card is expired", amount=50000
    )
    client = _FakeClient(response={"id": "plink_abc", "short_url": "https://rzp.io/i/abc"})

    res = await run_recovery(db_session, event, now=NOW, client=client)

    assert res.action_type is ActionType.SEND_PAYMENT_LINK
    assert res.action_status is ActionStatus.EXECUTED
    assert res.razorpay_payment_link_id == "plink_abc"
    assert len(client.calls) == 1
    assert client.calls[0]["amount"] == 50000

    diagnosis = await db_session.scalar(
        select(Diagnosis).where(Diagnosis.payment_event_id == event.id)
    )
    assert diagnosis is not None
    action = await db_session.scalar(
        select(RecoveryAction).where(RecoveryAction.diagnosis_id == diagnosis.id)
    )
    assert action is not None
    assert action.status is ActionStatus.EXECUTED
    assert action.razorpay_payment_link_id == "plink_abc"
    assert action.razorpay_response == {"id": "plink_abc", "short_url": "https://rzp.io/i/abc"}
    assert action.razorpay_request is not None
    assert action.razorpay_request["amount"] == 50000

    audit_actions = (
        await db_session.scalars(
            select(AuditLog.action).where(AuditLog.recovery_action_id == action.id)
        )
    ).all()
    assert "razorpay.payment_link.requested" in audit_actions
    assert "razorpay.payment_link.created" in audit_actions


# --- mandate revoked -> escalate + terminal outcome -------------------------


async def test_mandate_revoked_escalates_and_records_outcome(db_session: AsyncSession) -> None:
    event = await _make_event(
        db_session, error_description="the mandate was revoked by customer", amount=99900
    )

    res = await run_recovery(db_session, event, now=NOW)

    assert res.diagnosis_category is DiagnosisCategory.MANDATE_REVOKED
    assert res.action_type is ActionType.ESCALATE_MANUAL
    # Escalation is terminal-safe, so the guardrail allows it even though the
    # mandate is revoked (a retry/link would have been frozen).
    assert res.guardrail_allowed is True
    assert res.action_status is ActionStatus.EXECUTED

    outcome = await db_session.scalar(
        select(RecoveryOutcome).where(RecoveryOutcome.subscription_id == event.subscription_id)
    )
    assert outcome is not None
    assert outcome.outcome is OutcomeStatus.ESCALATED
    assert outcome.amount_at_risk == 99900


# --- the guardrail overrides the policy (the centerpiece guarantee) ---------


async def test_guardrail_freezes_retry_when_subscription_cancelled(
    db_session: AsyncSession,
) -> None:
    # Insufficient funds would normally schedule a retry; a cancelled mandate
    # must block it — the policy proposes, the guardrail disposes.
    event = await _make_event(
        db_session, error_description="insufficient funds", sub_status="cancelled"
    )

    res = await run_recovery(db_session, event, now=NOW)

    assert res.diagnosis_category is DiagnosisCategory.INSUFFICIENT_FUNDS
    assert res.action_type is ActionType.RETRY_CHARGE  # policy still proposed a retry
    assert res.guardrail_allowed is False  # ...but the gate vetoed it
    assert res.guardrail_code == GuardrailCode.POST_CANCELLATION_FREEZE.value
    assert res.action_status is ActionStatus.SKIPPED

    diagnosis = await db_session.scalar(
        select(Diagnosis).where(Diagnosis.payment_event_id == event.id)
    )
    assert diagnosis is not None
    action = await db_session.scalar(
        select(RecoveryAction).where(RecoveryAction.diagnosis_id == diagnosis.id)
    )
    assert action is not None
    assert action.status is ActionStatus.SKIPPED
    assert action.error is not None


# --- audit precedes execution, even on failure ------------------------------


async def test_payment_link_failure_leaves_pre_execution_audit(db_session: AsyncSession) -> None:
    event = await _make_event(db_session, error_description="the card is expired", amount=12345)
    client = _FakeClient(error=httpx.HTTPError("simulated razorpay outage"))

    res = await run_recovery(db_session, event, now=NOW, client=client)

    assert res.action_type is ActionType.SEND_PAYMENT_LINK
    assert res.action_status is ActionStatus.FAILED
    assert res.razorpay_payment_link_id is None

    diagnosis = await db_session.scalar(
        select(Diagnosis).where(Diagnosis.payment_event_id == event.id)
    )
    assert diagnosis is not None
    action = await db_session.scalar(
        select(RecoveryAction).where(RecoveryAction.diagnosis_id == diagnosis.id)
    )
    assert action is not None
    assert action.status is ActionStatus.FAILED
    assert "simulated razorpay outage" in (action.error or "")

    audit_actions = (
        await db_session.scalars(
            select(AuditLog.action).where(AuditLog.recovery_action_id == action.id)
        )
    ).all()
    # The "requested" row was committed before the (failing) call — durable intent.
    assert "razorpay.payment_link.requested" in audit_actions
    assert "razorpay.payment_link.failed" in audit_actions


async def test_payment_link_deferred_without_client(db_session: AsyncSession) -> None:
    event = await _make_event(db_session, error_description="invalid card", amount=5000)

    res = await run_recovery(db_session, event, now=NOW, client=None)

    assert res.action_type is ActionType.SEND_PAYMENT_LINK
    assert res.action_status is ActionStatus.PLANNED

    diagnosis = await db_session.scalar(
        select(Diagnosis).where(Diagnosis.payment_event_id == event.id)
    )
    assert diagnosis is not None
    action = await db_session.scalar(
        select(RecoveryAction).where(RecoveryAction.diagnosis_id == diagnosis.id)
    )
    assert action is not None
    assert action.status is ActionStatus.PLANNED
    assert "deferred" in (action.error or "")


# --- retry attempts are counted from history + bounded ----------------------


async def test_retry_attempt_count_increments_across_events(db_session: AsyncSession) -> None:
    ev1 = await _make_event(
        db_session, event_id="evt_1", payment_id="pay_1", error_description="insufficient funds"
    )
    r1 = await run_recovery(db_session, ev1, now=NOW)
    assert r1.action_status is ActionStatus.SCHEDULED
    assert r1.scheduled_at == NOW + timedelta(hours=6)

    # Same subscription, a later failure -> second retry backs off further.
    ev2 = await _make_event(
        db_session,
        event_id="evt_2",
        payment_id="pay_2",
        rzp_sub_id=ev1.razorpay_subscription_id,
        error_description="insufficient funds",
    )
    r2 = await run_recovery(db_session, ev2, now=NOW)
    assert r2.action_status is ActionStatus.SCHEDULED
    assert r2.scheduled_at == NOW + timedelta(hours=24)

    actions = (
        await db_session.scalars(
            select(RecoveryAction)
            .where(RecoveryAction.subscription_id == ev1.subscription_id)
            .order_by(RecoveryAction.attempt_number)
        )
    ).all()
    assert [a.attempt_number for a in actions] == [1, 2]


# --- idempotency ------------------------------------------------------------


async def test_already_processed_event_is_skipped(db_session: AsyncSession) -> None:
    event = await _make_event(
        db_session,
        error_description="insufficient funds",
        processing_status=EventProcessingStatus.PROCESSED,
    )

    res = await run_recovery(db_session, event, now=NOW)

    assert res.guardrail_code == "already_processed"
    assert res.action_status is ActionStatus.SKIPPED
    # Nothing was diagnosed or acted on.
    assert (
        await db_session.scalar(
            select(Diagnosis).where(Diagnosis.payment_event_id == event.id)
        )
        is None
    )
