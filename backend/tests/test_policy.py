"""Decide-step tests: diagnosis -> proposed recovery action, with backoff.

The policy only *proposes*; the guardrail (tested separately) can still veto.
These tests pin the action type, the scheduling delay, and the retries->fallback
transition for each category.
"""

from __future__ import annotations

from datetime import timedelta

from app.agent.policy import decide
from app.enums import ActionType, DiagnosisCategory

# --- insufficient funds: escalating retry, then payment link ----------------


def test_funds_first_retry_uses_short_backoff() -> None:
    d = decide(DiagnosisCategory.INSUFFICIENT_FUNDS, attempts_so_far=0)
    assert d.action_type is ActionType.RETRY_CHARGE
    assert d.delay == timedelta(hours=6)


def test_funds_second_retry_backs_off_further() -> None:
    d = decide(DiagnosisCategory.INSUFFICIENT_FUNDS, attempts_so_far=1)
    assert d.action_type is ActionType.RETRY_CHARGE
    assert d.delay == timedelta(hours=24)


def test_funds_third_retry_backs_off_furthest() -> None:
    d = decide(DiagnosisCategory.INSUFFICIENT_FUNDS, attempts_so_far=2)
    assert d.action_type is ActionType.RETRY_CHARGE
    assert d.delay == timedelta(hours=72)


def test_funds_after_max_retries_sends_payment_link() -> None:
    d = decide(DiagnosisCategory.INSUFFICIENT_FUNDS, attempts_so_far=3)
    assert d.action_type is ActionType.SEND_PAYMENT_LINK
    assert d.delay is None


# --- bank/gateway: escalating retry, then manual escalation -----------------


def test_gateway_first_retry_uses_short_backoff() -> None:
    d = decide(DiagnosisCategory.BANK_OR_GATEWAY_ERROR, attempts_so_far=0)
    assert d.action_type is ActionType.RETRY_CHARGE
    assert d.delay == timedelta(minutes=30)


def test_gateway_backoff_progression() -> None:
    second = decide(DiagnosisCategory.BANK_OR_GATEWAY_ERROR, attempts_so_far=1)
    third = decide(DiagnosisCategory.BANK_OR_GATEWAY_ERROR, attempts_so_far=2)
    assert second.delay == timedelta(hours=2)
    assert third.delay == timedelta(hours=6)


def test_gateway_after_max_retries_escalates() -> None:
    d = decide(DiagnosisCategory.BANK_OR_GATEWAY_ERROR, attempts_so_far=3)
    assert d.action_type is ActionType.ESCALATE_MANUAL
    assert d.delay is None


# --- non-retryable causes act immediately -----------------------------------


def test_expired_card_sends_payment_link_immediately() -> None:
    d = decide(DiagnosisCategory.EXPIRED_OR_INVALID_CARD)
    assert d.action_type is ActionType.SEND_PAYMENT_LINK
    assert d.delay is None


def test_expired_card_never_retries_even_at_zero_attempts() -> None:
    # Re-charging the same dead card can't succeed, regardless of attempt count.
    d = decide(DiagnosisCategory.EXPIRED_OR_INVALID_CARD, attempts_so_far=0)
    assert d.action_type is ActionType.SEND_PAYMENT_LINK


def test_mandate_revoked_escalates() -> None:
    d = decide(DiagnosisCategory.MANDATE_REVOKED)
    assert d.action_type is ActionType.ESCALATE_MANUAL
    assert d.delay is None


def test_other_escalates() -> None:
    d = decide(DiagnosisCategory.OTHER)
    assert d.action_type is ActionType.ESCALATE_MANUAL
    assert d.delay is None


# --- backoff clamping + max_retries override --------------------------------


def test_backoff_clamps_beyond_schedule_length() -> None:
    # attempts_so_far past the 3-entry schedule reuses the final (longest) delay.
    d = decide(DiagnosisCategory.INSUFFICIENT_FUNDS, attempts_so_far=5, max_retries=10)
    assert d.action_type is ActionType.RETRY_CHARGE
    assert d.delay == timedelta(hours=72)


def test_max_retries_override_is_respected() -> None:
    # With max_retries=1, a single prior attempt already exhausts retries.
    d = decide(DiagnosisCategory.INSUFFICIENT_FUNDS, attempts_so_far=1, max_retries=1)
    assert d.action_type is ActionType.SEND_PAYMENT_LINK
