"""Guardrail-engine tests — the deterministic safety gate (submission centerpiece).

The guardrail, never the LLM, has final say on whether a money-moving action
runs. These tests exhaustively pin its three invariants and their precedence:

1. post-cancellation freeze (mandate revoked / subscription cancelled|expired|completed),
2. max retry attempts,
3. cooldown window between retries,

plus config overrides and the audit-detail serialization.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.agent.guardrail import (
    GuardrailCode,
    GuardrailConfig,
    GuardrailContext,
    evaluate,
)
from app.enums import ActionType, SubscriptionStatus

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _ctx(**overrides: object) -> GuardrailContext:
    base: dict[str, object] = {"now": NOW}
    base.update(overrides)
    return GuardrailContext(**base)  # type: ignore[arg-type]


# --- happy path: retries allowed within limits ------------------------------


def test_first_retry_allowed() -> None:
    v = evaluate(ActionType.RETRY_CHARGE, _ctx(retry_attempts_used=0))
    assert v.allowed is True
    assert v.code is GuardrailCode.OK


def test_retry_allowed_below_max_with_cooldown_elapsed() -> None:
    v = evaluate(
        ActionType.RETRY_CHARGE,
        _ctx(retry_attempts_used=2, last_attempt_at=NOW - timedelta(hours=2)),
    )
    assert v.allowed is True
    assert v.code is GuardrailCode.OK


# --- max retries ------------------------------------------------------------


def test_retry_denied_at_max() -> None:
    v = evaluate(ActionType.RETRY_CHARGE, _ctx(retry_attempts_used=3))
    assert v.allowed is False
    assert v.code is GuardrailCode.MAX_RETRIES_EXCEEDED


def test_retry_denied_above_max() -> None:
    v = evaluate(ActionType.RETRY_CHARGE, _ctx(retry_attempts_used=4))
    assert v.allowed is False
    assert v.code is GuardrailCode.MAX_RETRIES_EXCEEDED


# --- cooldown ---------------------------------------------------------------


def test_retry_denied_during_cooldown() -> None:
    v = evaluate(
        ActionType.RETRY_CHARGE,
        _ctx(retry_attempts_used=1, last_attempt_at=NOW - timedelta(minutes=30)),
    )
    assert v.allowed is False
    assert v.code is GuardrailCode.COOLDOWN_ACTIVE


def test_retry_allowed_exactly_at_cooldown_boundary() -> None:
    # elapsed == cooldown is NOT "< cooldown", so the retry is permitted.
    v = evaluate(
        ActionType.RETRY_CHARGE,
        _ctx(retry_attempts_used=1, last_attempt_at=NOW - timedelta(hours=1)),
    )
    assert v.allowed is True
    assert v.code is GuardrailCode.OK


def test_max_retries_checked_before_cooldown() -> None:
    # Both would deny; the max-retries verdict must win (it's terminal, not "wait").
    v = evaluate(
        ActionType.RETRY_CHARGE,
        _ctx(retry_attempts_used=3, last_attempt_at=NOW),
    )
    assert v.code is GuardrailCode.MAX_RETRIES_EXCEEDED


# --- post-cancellation freeze -----------------------------------------------


def test_retry_frozen_by_mandate_cancellation() -> None:
    v = evaluate(ActionType.RETRY_CHARGE, _ctx(mandate_cancelled=True))
    assert v.allowed is False
    assert v.code is GuardrailCode.POST_CANCELLATION_FREEZE


def test_payment_link_frozen_by_mandate_cancellation() -> None:
    v = evaluate(ActionType.SEND_PAYMENT_LINK, _ctx(mandate_cancelled=True))
    assert v.allowed is False
    assert v.code is GuardrailCode.POST_CANCELLATION_FREEZE


def test_retry_frozen_when_subscription_cancelled() -> None:
    v = evaluate(
        ActionType.RETRY_CHARGE,
        _ctx(subscription_status=SubscriptionStatus.CANCELLED),
    )
    assert v.code is GuardrailCode.POST_CANCELLATION_FREEZE


def test_retry_frozen_when_subscription_expired() -> None:
    v = evaluate(
        ActionType.RETRY_CHARGE,
        _ctx(subscription_status=SubscriptionStatus.EXPIRED),
    )
    assert v.code is GuardrailCode.POST_CANCELLATION_FREEZE


def test_retry_frozen_when_subscription_completed() -> None:
    v = evaluate(
        ActionType.RETRY_CHARGE,
        _ctx(subscription_status=SubscriptionStatus.COMPLETED),
    )
    assert v.code is GuardrailCode.POST_CANCELLATION_FREEZE


def test_escalate_manual_allowed_when_frozen() -> None:
    v = evaluate(ActionType.ESCALATE_MANUAL, _ctx(mandate_cancelled=True))
    assert v.allowed is True
    assert v.code is GuardrailCode.OK


def test_mark_dead_allowed_when_frozen() -> None:
    v = evaluate(
        ActionType.MARK_DEAD,
        _ctx(subscription_status=SubscriptionStatus.CANCELLED),
    )
    assert v.allowed is True
    assert v.code is GuardrailCode.OK


def test_freeze_takes_precedence_over_max_retries() -> None:
    # Even with retries exhausted, a cancelled mandate reports the freeze reason.
    v = evaluate(
        ActionType.RETRY_CHARGE,
        _ctx(mandate_cancelled=True, retry_attempts_used=99),
    )
    assert v.code is GuardrailCode.POST_CANCELLATION_FREEZE


def test_active_subscription_is_not_frozen() -> None:
    v = evaluate(
        ActionType.RETRY_CHARGE,
        _ctx(subscription_status=SubscriptionStatus.ACTIVE, retry_attempts_used=0),
    )
    assert v.allowed is True


# --- non-retry actions are exempt from retry limits -------------------------


def test_payment_link_ignores_retry_limits() -> None:
    # Only RETRY_CHARGE is subject to max/cooldown; a link with high counts is fine.
    v = evaluate(
        ActionType.SEND_PAYMENT_LINK,
        _ctx(retry_attempts_used=99, last_attempt_at=NOW),
    )
    assert v.allowed is True
    assert v.code is GuardrailCode.OK


# --- config overrides -------------------------------------------------------


def test_custom_max_retries_override() -> None:
    cfg = GuardrailConfig(max_retries=1)
    # 1 attempt used would pass under the default (3) but not under max_retries=1.
    v = evaluate(ActionType.RETRY_CHARGE, _ctx(retry_attempts_used=1), cfg)
    assert v.allowed is False
    assert v.code is GuardrailCode.MAX_RETRIES_EXCEEDED


def test_custom_cooldown_shorter_allows_sooner() -> None:
    cfg = GuardrailConfig(cooldown=timedelta(minutes=5))
    v = evaluate(
        ActionType.RETRY_CHARGE,
        _ctx(retry_attempts_used=1, last_attempt_at=NOW - timedelta(minutes=10)),
        cfg,
    )
    assert v.allowed is True


def test_custom_cooldown_longer_denies() -> None:
    cfg = GuardrailConfig(cooldown=timedelta(hours=6))
    v = evaluate(
        ActionType.RETRY_CHARGE,
        _ctx(retry_attempts_used=1, last_attempt_at=NOW - timedelta(hours=2)),
        cfg,
    )
    assert v.allowed is False
    assert v.code is GuardrailCode.COOLDOWN_ACTIVE


# --- audit serialization ----------------------------------------------------


def test_audit_detail_is_serializable_and_uses_string_code() -> None:
    v = evaluate(ActionType.RETRY_CHARGE, _ctx(retry_attempts_used=3))
    detail = v.audit_detail()
    assert detail == {
        "allowed": False,
        "code": "max_retries_exceeded",
        "reason": v.reason,
    }
    # code must be a plain string in the audit payload, not the enum object.
    assert isinstance(detail["code"], str)
