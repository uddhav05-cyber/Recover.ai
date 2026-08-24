"""Decide step: map a diagnosis to a *proposed* recovery action.

This is the business-policy layer. It is deliberately a plain, inspectable
function (not buried in an LLM prompt) so it can be reasoned about and unit
tested. It proposes; it does not enforce limits — the :mod:`app.agent.guardrail`
module is the deterministic gate that can still veto what this proposes.

Escalating backoff is used for retryable causes: insufficient funds gets a
longer schedule (balance may replenish over days), transient bank/gateway
errors get a shorter one (retry sooner).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.enums import ActionType, DiagnosisCategory

DEFAULT_MAX_RETRIES = 3

# Per-attempt backoff schedules (0-based attempt index). The last entry is
# reused if attempts exceed the schedule length.
_FUNDS_BACKOFF: tuple[timedelta, ...] = (
    timedelta(hours=6),
    timedelta(hours=24),
    timedelta(hours=72),
)
_GATEWAY_BACKOFF: tuple[timedelta, ...] = (
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """A proposed recovery action for a diagnosed failure."""

    action_type: ActionType
    delay: timedelta | None  # None ⇒ act now (no scheduling); set ⇒ schedule after delay
    rationale: str


def _backoff(schedule: tuple[timedelta, ...], attempts_so_far: int) -> timedelta:
    return schedule[min(attempts_so_far, len(schedule) - 1)]


def decide(
    category: DiagnosisCategory,
    *,
    attempts_so_far: int = 0,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> PolicyDecision:
    """Propose an action for a diagnosis given how many retries have been used."""
    if category is DiagnosisCategory.INSUFFICIENT_FUNDS:
        if attempts_so_far < max_retries:
            delay = _backoff(_FUNDS_BACKOFF, attempts_so_far)
            return PolicyDecision(
                ActionType.RETRY_CHARGE,
                delay,
                f"insufficient funds: retry #{attempts_so_far + 1} after {delay} "
                "(balance may replenish)",
            )
        return PolicyDecision(
            ActionType.SEND_PAYMENT_LINK,
            None,
            "insufficient funds: retries exhausted ⇒ send a payment link",
        )

    if category is DiagnosisCategory.BANK_OR_GATEWAY_ERROR:
        if attempts_so_far < max_retries:
            delay = _backoff(_GATEWAY_BACKOFF, attempts_so_far)
            return PolicyDecision(
                ActionType.RETRY_CHARGE,
                delay,
                f"transient bank/gateway error: retry #{attempts_so_far + 1} after {delay}",
            )
        return PolicyDecision(
            ActionType.ESCALATE_MANUAL,
            None,
            "bank/gateway error persists after retries ⇒ escalate for manual review",
        )

    if category is DiagnosisCategory.EXPIRED_OR_INVALID_CARD:
        return PolicyDecision(
            ActionType.SEND_PAYMENT_LINK,
            None,
            "expired/invalid card: re-charging the same mandate cannot succeed ⇒ "
            "send a payment link to capture new card details",
        )

    if category is DiagnosisCategory.MANDATE_REVOKED:
        return PolicyDecision(
            ActionType.ESCALATE_MANUAL,
            None,
            "mandate revoked: no automated charge is permitted ⇒ escalate",
        )

    # DiagnosisCategory.OTHER and any unmapped category.
    return PolicyDecision(
        ActionType.ESCALATE_MANUAL,
        None,
        "unclassified failure ⇒ escalate for manual review",
    )
