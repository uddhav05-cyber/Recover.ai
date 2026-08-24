"""Act step: the recovery executor that ties the agent loop together.

Given an ingested (``pending``) ``payment_events`` row, this orchestrates the
full episode and is the *only* place a money-moving Razorpay call originates:

    Diagnose (rules/LLM)  ->  Decide (policy)  ->  Guardrail (deterministic gate)
      -> write the guardrail verdict to ``audit_log`` **before** any execution
      -> Act against Razorpay test-mode **only if the guardrail allows**
      -> record the response, update state, mark the event processed.

Two invariants from the build brief are enforced structurally here, not by
convention:

* **The LLM never triggers a Razorpay call.** The policy proposes and the
  guardrail disposes; execution branches on ``verdict.allowed`` alone.
* **Audit precedes execution.** For an externally-visible action (payment link)
  the "requested" audit row + ``executing`` status are *committed* before the
  HTTP call, so a crash mid-call still leaves a durable record of intent.

Razorpay is reached through the injected :class:`RecoveryClient` protocol, so
this module unit-tests against a fake with an explicit ``now`` (no hidden clock,
no live network) and swaps in the real client once test keys are configured.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.diagnosis import FailureSignal, LLMClassifier, diagnose
from app.agent.guardrail import GuardrailConfig, GuardrailContext, GuardrailVerdict, evaluate
from app.agent.policy import decide
from app.db.models import (
    AuditLog,
    Diagnosis,
    PaymentEvent,
    RecoveryAction,
    RecoveryOutcome,
    Subscription,
)
from app.enums import (
    ActionStatus,
    ActionType,
    AuditActor,
    DiagnosisCategory,
    EventProcessingStatus,
    OutcomeStatus,
    SubscriptionStatus,
)
from app.schemas.razorpay_webhook import WebhookEnvelope


class RecoveryClient(Protocol):
    """The Razorpay surface the executor needs (satisfied by ``RazorpayClient``)."""

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
class RecoveryResult:
    """Summary of one recovery episode (what the loop decided and did)."""

    payment_event_id: uuid.UUID
    diagnosis_category: DiagnosisCategory
    action_type: ActionType
    guardrail_allowed: bool
    guardrail_code: str
    action_status: ActionStatus
    scheduled_at: datetime | None = None
    razorpay_payment_link_id: str | None = None
    detail: str = ""


def _to_sub_status(value: str | None) -> SubscriptionStatus | None:
    if not value:
        return None
    try:
        return SubscriptionStatus(value)
    except ValueError:
        return None


async def _resolve_subscription(
    session: AsyncSession,
    *,
    razorpay_subscription_id: str,
    status: SubscriptionStatus | None,
    auth_attempts: int | None,
) -> Subscription:
    """Fetch the tracked subscription, creating/refreshing it from the webhook."""
    sub = await session.scalar(
        select(Subscription).where(
            Subscription.razorpay_subscription_id == razorpay_subscription_id
        )
    )
    if sub is None:
        sub = Subscription(
            razorpay_subscription_id=razorpay_subscription_id,
            status=status,
            auth_attempts=auth_attempts or 0,
        )
        session.add(sub)
        await session.flush()
        return sub

    # Refresh volatile fields from the latest webhook.
    if status is not None:
        sub.status = status
    if auth_attempts is not None:
        sub.auth_attempts = auth_attempts
    return sub


async def _retry_history(
    session: AsyncSession, subscription_id: uuid.UUID
) -> tuple[int, datetime | None]:
    """Prior retry count (bounds attempts) and last *executed* attempt (feeds cooldown).

    ``attempts`` counts every retry action ever chosen for the subscription, so the
    max-retries bound holds across rapid re-failures. ``last_attempt_at`` uses only
    ``executed_at`` — a scheduled-but-pending retry is a future intent, not a past
    charge, so it must not trip the cooldown window.
    """
    attempts = await session.scalar(
        select(func.count())
        .select_from(RecoveryAction)
        .where(
            RecoveryAction.subscription_id == subscription_id,
            RecoveryAction.action_type == ActionType.RETRY_CHARGE,
        )
    )
    last_attempt_at = await session.scalar(
        select(func.max(RecoveryAction.executed_at)).where(
            RecoveryAction.subscription_id == subscription_id,
            RecoveryAction.action_type == ActionType.RETRY_CHARGE,
        )
    )
    return int(attempts or 0), last_attempt_at


def _audit(
    *,
    actor: AuditActor,
    action: str,
    subscription_id: uuid.UUID | None = None,
    payment_event_id: uuid.UUID | None = None,
    diagnosis_id: uuid.UUID | None = None,
    recovery_action_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    return AuditLog(
        actor=actor,
        action=action,
        subscription_id=subscription_id,
        payment_event_id=payment_event_id,
        diagnosis_id=diagnosis_id,
        recovery_action_id=recovery_action_id,
        detail=detail,
    )


async def run_recovery(
    session: AsyncSession,
    payment_event: PaymentEvent,
    *,
    now: datetime,
    client: RecoveryClient | None = None,
    llm_classifier: LLMClassifier | None = None,
    config: GuardrailConfig | None = None,
) -> RecoveryResult:
    """Run the Diagnose -> Decide -> Guardrail -> Act loop for one failed event."""
    config = config or GuardrailConfig()

    # Capture primitives up front: ORM attributes may expire across the mid-episode
    # commit (payment-link path), so we never re-read them off an expired instance.
    event_id = payment_event.id
    amount = payment_event.amount
    currency = payment_event.currency or "INR"
    rzp_sub_id = payment_event.razorpay_subscription_id
    signal = FailureSignal(
        event_type=payment_event.event_type,
        error_code=payment_event.error_code,
        error_description=payment_event.error_description,
    )
    raw_payload = payment_event.payload

    # Idempotency: never re-process an event the loop has already handled.
    if payment_event.processing_status is not EventProcessingStatus.PENDING:
        return RecoveryResult(
            payment_event_id=event_id,
            diagnosis_category=DiagnosisCategory.OTHER,
            action_type=ActionType.ESCALATE_MANUAL,
            guardrail_allowed=False,
            guardrail_code="already_processed",
            action_status=ActionStatus.SKIPPED,
            detail=f"event already {payment_event.processing_status.value}",
        )

    # An action needs a subscription to act on; a payment-only event can't recover.
    if not rzp_sub_id:
        payment_event.processing_status = EventProcessingStatus.FAILED
        payment_event.processed_at = now
        session.add(
            _audit(
                actor=AuditActor.SYSTEM,
                action="recovery.skipped_no_subscription",
                payment_event_id=event_id,
                detail={"reason": "event has no razorpay_subscription_id"},
            )
        )
        await session.commit()
        return RecoveryResult(
            payment_event_id=event_id,
            diagnosis_category=DiagnosisCategory.OTHER,
            action_type=ActionType.ESCALATE_MANUAL,
            guardrail_allowed=False,
            guardrail_code="no_subscription",
            action_status=ActionStatus.SKIPPED,
            detail="no subscription on event",
        )

    # --- resolve subscription (status feeds the guardrail freeze) --------------
    sub_status: SubscriptionStatus | None = None
    auth_attempts: int | None = None
    try:
        env = WebhookEnvelope.model_validate(raw_payload)
        if env.subscription is not None:
            sub_status = _to_sub_status(env.subscription.status)
            auth_attempts = env.subscription.auth_attempts
    except Exception:  # noqa: BLE001 - malformed stored payload must not crash the loop
        pass

    subscription = await _resolve_subscription(
        session,
        razorpay_subscription_id=rzp_sub_id,
        status=sub_status,
        auth_attempts=auth_attempts,
    )
    subscription_id = subscription.id
    if payment_event.subscription_id is None:
        payment_event.subscription_id = subscription_id

    # --- Diagnose --------------------------------------------------------------
    dx = diagnose(signal, llm_classifier=llm_classifier)
    diagnosis = Diagnosis(
        payment_event_id=event_id,
        subscription_id=subscription_id,
        category=dx.category,
        source=dx.source,
        confidence=dx.confidence,
        reasoning=dx.reasoning,  # LLM reasoning string is always persisted
        llm_model=dx.llm_model,
        llm_raw=dx.llm_raw,
    )
    session.add(diagnosis)
    await session.flush()
    diagnosis_id = diagnosis.id
    session.add(
        _audit(
            actor=AuditActor.LLM if dx.source.value == "llm" else AuditActor.SYSTEM,
            action="diagnosis.recorded",
            subscription_id=subscription_id,
            payment_event_id=event_id,
            diagnosis_id=diagnosis_id,
            detail={
                "category": dx.category.value,
                "source": dx.source.value,
                "confidence": dx.confidence,
                "reasoning": dx.reasoning,
            },
        )
    )

    # --- Decide ----------------------------------------------------------------
    attempts_so_far, last_attempt_at = await _retry_history(session, subscription_id)
    decision = decide(
        dx.category, attempts_so_far=attempts_so_far, max_retries=config.max_retries
    )

    action = RecoveryAction(
        diagnosis_id=diagnosis_id,
        subscription_id=subscription_id,
        action_type=decision.action_type,
        status=ActionStatus.PLANNED,
        attempt_number=attempts_so_far + 1,
    )
    session.add(action)
    await session.flush()
    action_id = action.id

    # --- Guardrail (the final say) + audit BEFORE any execution ----------------
    ctx = GuardrailContext(
        now=now,
        subscription_status=subscription.status,
        mandate_cancelled=dx.category is DiagnosisCategory.MANDATE_REVOKED,
        retry_attempts_used=attempts_so_far,
        last_attempt_at=last_attempt_at,
    )
    verdict: GuardrailVerdict = evaluate(decision.action_type, ctx, config)
    action.guardrail_decision = verdict.audit_detail()
    session.add(
        _audit(
            actor=AuditActor.GUARDRAIL,
            action="guardrail.allow" if verdict.allowed else "guardrail.deny",
            subscription_id=subscription_id,
            payment_event_id=event_id,
            diagnosis_id=diagnosis_id,
            recovery_action_id=action_id,
            detail={**verdict.audit_detail(), "policy_rationale": decision.rationale},
        )
    )

    result_status: ActionStatus
    link_id: str | None = None

    if not verdict.allowed:
        # Gate said no: record and stop. No external call is ever attempted.
        action.status = ActionStatus.SKIPPED
        action.error = verdict.reason
        result_status = ActionStatus.SKIPPED

    elif decision.action_type is ActionType.RETRY_CHARGE:
        # Bounded, delayed retry: schedule it; the Phase-4 poller executes it.
        action.status = ActionStatus.SCHEDULED
        action.scheduled_at = now + decision.delay if decision.delay else now
        result_status = ActionStatus.SCHEDULED
        session.add(
            _audit(
                actor=AuditActor.SYSTEM,
                action="recovery.retry.scheduled",
                subscription_id=subscription_id,
                recovery_action_id=action_id,
                detail={"scheduled_at": action.scheduled_at.isoformat()},
            )
        )

    elif decision.action_type is ActionType.ESCALATE_MANUAL:
        action.status = ActionStatus.EXECUTED
        action.executed_at = now
        result_status = ActionStatus.EXECUTED
        session.add(
            RecoveryOutcome(
                subscription_id=subscription_id,
                recovery_action_id=action_id,
                triggering_payment_event_id=event_id,
                outcome=OutcomeStatus.ESCALATED,
                amount_at_risk=amount or 0,
                resolved_at=now,
            )
        )
        session.add(
            _audit(
                actor=AuditActor.SYSTEM,
                action="recovery.escalated",
                subscription_id=subscription_id,
                recovery_action_id=action_id,
            )
        )

    elif decision.action_type is ActionType.MARK_DEAD:
        action.status = ActionStatus.EXECUTED
        action.executed_at = now
        result_status = ActionStatus.EXECUTED
        session.add(
            RecoveryOutcome(
                subscription_id=subscription_id,
                recovery_action_id=action_id,
                triggering_payment_event_id=event_id,
                outcome=OutcomeStatus.DEAD,
                amount_at_risk=amount or 0,
                resolved_at=now,
            )
        )
        session.add(
            _audit(
                actor=AuditActor.SYSTEM,
                action="recovery.mark_dead",
                subscription_id=subscription_id,
                recovery_action_id=action_id,
            )
        )

    else:  # ActionType.SEND_PAYMENT_LINK — the externally-visible money move.
        link_id, result_status = await _execute_payment_link(
            session,
            now=now,
            client=client,
            action_id=action_id,
            subscription_id=subscription_id,
            event_id=event_id,
            amount=amount,
            currency=currency,
            rzp_sub_id=rzp_sub_id,
        )

    # Mark the event handled and finalize (payment-link path already committed the
    # pre-execution audit; this commits the terminal state atomically).
    processed = await session.get(PaymentEvent, event_id)
    if processed is not None:
        processed.processing_status = EventProcessingStatus.PROCESSED
        processed.processed_at = now
    await session.commit()

    return RecoveryResult(
        payment_event_id=event_id,
        diagnosis_category=dx.category,
        action_type=decision.action_type,
        guardrail_allowed=verdict.allowed,
        guardrail_code=verdict.code.value,
        action_status=result_status,
        scheduled_at=(now + decision.delay)
        if (verdict.allowed and decision.action_type is ActionType.RETRY_CHARGE and decision.delay)
        else None,
        razorpay_payment_link_id=link_id,
        detail=decision.rationale,
    )


async def _execute_payment_link(
    session: AsyncSession,
    *,
    now: datetime,
    client: RecoveryClient | None,
    action_id: uuid.UUID,
    subscription_id: uuid.UUID,
    event_id: uuid.UUID,
    amount: int | None,
    currency: str,
    rzp_sub_id: str,
) -> tuple[str | None, ActionStatus]:
    """Create a Razorpay payment link, committing the intent to audit *first*."""
    action = await session.get(RecoveryAction, action_id)
    assert action is not None

    if amount is None:
        action.status = ActionStatus.FAILED
        action.error = "cannot send payment link: unknown amount"
        session.add(
            _audit(
                actor=AuditActor.SYSTEM,
                action="razorpay.payment_link.failed",
                subscription_id=subscription_id,
                recovery_action_id=action_id,
                detail={"error": "unknown amount"},
            )
        )
        return None, ActionStatus.FAILED

    request_payload = {
        "amount": amount,
        "currency": currency,
        "description": "RecoverAI: complete your subscription payment",
        "notes": {"razorpay_subscription_id": rzp_sub_id, "recovery_action_id": str(action_id)},
    }
    action.status = ActionStatus.EXECUTING
    action.razorpay_request = request_payload
    session.add(
        _audit(
            actor=AuditActor.SYSTEM,
            action="razorpay.payment_link.requested",
            subscription_id=subscription_id,
            payment_event_id=event_id,
            recovery_action_id=action_id,
            detail=request_payload,
        )
    )
    # Durable record of intent BEFORE the side-effecting call.
    await session.commit()

    if client is None:
        # No client wired (e.g. no test keys yet): leave it planned for later.
        refetched = await session.get(RecoveryAction, action_id)
        assert refetched is not None
        refetched.status = ActionStatus.PLANNED
        refetched.error = "no Razorpay client configured; payment link deferred"
        return None, ActionStatus.PLANNED

    try:
        response = await client.create_payment_link(
            amount=amount,
            currency=currency,
            description=request_payload["description"],  # type: ignore[arg-type]
            notes=request_payload["notes"],  # type: ignore[arg-type]
        )
    except httpx.HTTPError as exc:
        failed = await session.get(RecoveryAction, action_id)
        assert failed is not None
        failed.status = ActionStatus.FAILED
        failed.error = f"razorpay payment_link error: {exc!r}"
        session.add(
            _audit(
                actor=AuditActor.SYSTEM,
                action="razorpay.payment_link.failed",
                subscription_id=subscription_id,
                recovery_action_id=action_id,
                detail={"error": str(exc)},
            )
        )
        return None, ActionStatus.FAILED

    executed = await session.get(RecoveryAction, action_id)
    assert executed is not None
    link_id = response.get("id")
    executed.status = ActionStatus.EXECUTED
    executed.executed_at = now
    executed.razorpay_response = response
    executed.razorpay_payment_link_id = link_id
    session.add(
        _audit(
            actor=AuditActor.SYSTEM,
            action="razorpay.payment_link.created",
            subscription_id=subscription_id,
            payment_event_id=event_id,
            recovery_action_id=action_id,
            detail={"payment_link_id": link_id, "short_url": response.get("short_url")},
        )
    )
    return link_id, ActionStatus.EXECUTED
