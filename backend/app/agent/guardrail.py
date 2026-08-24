"""Guardrail engine: the deterministic safety gate for every recovery action.

This module — never the LLM — has the final say on whether a money-moving action
runs. It is intentionally pure and side-effect free: it reads an explicit
:class:`GuardrailContext` (including ``now``, so there is no hidden clock) and
returns a :class:`GuardrailVerdict`. The executor (Act step) must:

1. call :func:`evaluate`,
2. write the verdict to ``audit_log`` *before* doing anything, and
3. proceed to the Razorpay API **only** if ``verdict.allowed``.

Enforced invariants:

* **Post-cancellation freeze** — once a mandate is revoked / the subscription is
  cancelled or expired, no automated recovery (retry or payment link) is
  permitted; only manual escalation or marking the episode dead.
* **Max retry attempts** — a bounded number of ``retry_charge`` attempts.
* **Cooldown window** — a minimum spacing between retry attempts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from app.enums import ActionType, SubscriptionStatus

# Actions that are always safe: they move no money via the mandate.
_TERMINAL_SAFE_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.ESCALATE_MANUAL, ActionType.MARK_DEAD}
)

# Subscription states in which the mandate can no longer authorise a charge.
_FROZEN_STATES: frozenset[SubscriptionStatus] = frozenset(
    {SubscriptionStatus.CANCELLED, SubscriptionStatus.EXPIRED, SubscriptionStatus.COMPLETED}
)


class GuardrailCode(StrEnum):
    """Machine-readable reason a verdict was reached (for audit + dashboards)."""

    OK = "ok"
    POST_CANCELLATION_FREEZE = "post_cancellation_freeze"
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
    COOLDOWN_ACTIVE = "cooldown_active"


@dataclass(frozen=True, slots=True)
class GuardrailConfig:
    """Tunable bounds. Defaults are conservative and overridable per call/test."""

    max_retries: int = 3
    cooldown: timedelta = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class GuardrailContext:
    """Everything the guardrail needs, passed in explicitly (no hidden reads)."""

    now: datetime
    subscription_status: SubscriptionStatus | None = None
    mandate_cancelled: bool = False
    retry_attempts_used: int = 0
    last_attempt_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class GuardrailVerdict:
    """The gate's decision on a single proposed action."""

    allowed: bool
    code: GuardrailCode
    reason: str

    def audit_detail(self) -> dict[str, Any]:
        """Serializable form for the ``audit_log.detail`` column."""
        return {"allowed": self.allowed, "code": self.code.value, "reason": self.reason}


def evaluate(
    action_type: ActionType,
    ctx: GuardrailContext,
    config: GuardrailConfig | None = None,
) -> GuardrailVerdict:
    """Decide whether ``action_type`` may execute in the given context."""
    config = config or GuardrailConfig()

    frozen = ctx.mandate_cancelled or ctx.subscription_status in _FROZEN_STATES
    if frozen and action_type not in _TERMINAL_SAFE_ACTIONS:
        return GuardrailVerdict(
            allowed=False,
            code=GuardrailCode.POST_CANCELLATION_FREEZE,
            reason=(
                "mandate cancelled/expired: automated recovery is frozen; only "
                "manual escalation or mark-dead is permitted"
            ),
        )

    if action_type is ActionType.RETRY_CHARGE:
        if ctx.retry_attempts_used >= config.max_retries:
            return GuardrailVerdict(
                allowed=False,
                code=GuardrailCode.MAX_RETRIES_EXCEEDED,
                reason=(
                    f"retry blocked: {ctx.retry_attempts_used} attempts used "
                    f">= max {config.max_retries}"
                ),
            )
        if ctx.last_attempt_at is not None:
            elapsed = ctx.now - ctx.last_attempt_at
            if elapsed < config.cooldown:
                return GuardrailVerdict(
                    allowed=False,
                    code=GuardrailCode.COOLDOWN_ACTIVE,
                    reason=(
                        f"retry blocked: {elapsed} since last attempt < "
                        f"cooldown {config.cooldown}"
                    ),
                )

    return GuardrailVerdict(
        allowed=True, code=GuardrailCode.OK, reason="within guardrail limits"
    )
