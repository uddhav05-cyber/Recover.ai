"""Diagnose step: classify a failed payment into a fixed root-cause category.

Strategy (per the build brief): a deterministic **rules table** handles the
clear-cut cases from the Razorpay decline code + description; only genuinely
ambiguous events fall through to the LLM. Keeping the category set tiny
(:class:`~app.enums.DiagnosisCategory`) is intentional.

Everything here is pure and synchronous so it unit-tests without a DB or network.
The LLM classifier is injected as an optional callable (wired in a later step,
once Vertex/Gemini credentials are available) — its absence just means unresolved
events default to ``OTHER`` for manual review.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.enums import DiagnosisCategory, DiagnosisSource


@dataclass(frozen=True, slots=True)
class DiagnosisResult:
    """Outcome of diagnosing one failed payment event."""

    category: DiagnosisCategory
    source: DiagnosisSource
    confidence: float
    reasoning: str
    llm_model: str | None = None
    llm_raw: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FailureSignal:
    """The minimal signal the diagnosis reads. Decoupled from the ORM row so the
    rules engine is trivially testable."""

    event_type: str
    error_code: str | None = None
    error_description: str | None = None


# Ordered rules: the first category with any keyword present in the normalized
# "<error_code> <error_description>" haystack wins. Order matters — more specific
# / higher-stakes causes (mandate revocation) are checked before generic ones.
_RULES: tuple[tuple[DiagnosisCategory, float, tuple[str, ...]], ...] = (
    (
        DiagnosisCategory.MANDATE_REVOKED,
        0.98,
        (
            "mandate revoked",
            "mandate cancel",
            "mandate_cancelled",
            "mandate_revoked",
            "authorization revoked",
            "authorisation revoked",
            "emandate cancel",
            "nach cancel",
            "revoked by customer",
        ),
    ),
    (
        DiagnosisCategory.INSUFFICIENT_FUNDS,
        0.97,
        (
            "insufficient funds",
            "insufficient balance",
            "insufficient_funds",
            "not enough balance",
            "low balance",
            "insufficient",
        ),
    ),
    (
        DiagnosisCategory.EXPIRED_OR_INVALID_CARD,
        0.95,
        (
            "card is expired",
            "expired card",
            "card_expired",
            "expired",
            "invalid card",
            "invalid_card",
            "incorrect card",
            "card declined",
            "card_declined",
            "invalid cvv",
            "invalid expiry",
            "do not honour",
            "do not honor",
        ),
    ),
    (
        DiagnosisCategory.BANK_OR_GATEWAY_ERROR,
        0.90,
        (
            "gateway",
            "server_error",
            "issuer",
            "acquirer",
            "bank",
            "network error",
            "timed out",
            "timeout",
            "try again",
            "temporarily",
            "service unavailable",
        ),
    ),
)

# Events that are themselves an explicit signal, independent of any decline text.
_EVENT_OVERRIDES: dict[str, tuple[DiagnosisCategory, float, str]] = {
    "subscription.cancelled": (
        DiagnosisCategory.MANDATE_REVOKED,
        0.90,
        "subscription.cancelled event ⇒ mandate no longer authorises charges",
    ),
}

# Classifier signature for the (later) LLM fallback.
LLMClassifier = Callable[[FailureSignal], DiagnosisResult]


def diagnose_from_rules(signal: FailureSignal) -> DiagnosisResult | None:
    """Return a rules-based diagnosis, or ``None`` if nothing matched (ambiguous)."""
    event = (signal.event_type or "").strip().lower()
    override = _EVENT_OVERRIDES.get(event)
    if override is not None:
        category, confidence, reasoning = override
        return DiagnosisResult(category, DiagnosisSource.RULES, confidence, reasoning)

    haystack = f"{signal.error_code or ''} {signal.error_description or ''}".lower()
    if not haystack.strip():
        return None

    for category, confidence, needles in _RULES:
        for needle in needles:
            if needle in haystack:
                return DiagnosisResult(
                    category,
                    DiagnosisSource.RULES,
                    confidence,
                    f"matched '{needle}' in decline info ⇒ {category.value}",
                )
    return None


def diagnose(
    signal: FailureSignal, *, llm_classifier: LLMClassifier | None = None
) -> DiagnosisResult:
    """Full diagnosis: rules first, LLM for ambiguous cases, else ``OTHER``.

    The LLM is consulted only when the rules table abstains. When no classifier
    is supplied (e.g. credentials not configured), the event is conservatively
    marked ``OTHER`` so it routes to manual review rather than a guessed action.
    """
    ruled = diagnose_from_rules(signal)
    if ruled is not None:
        return ruled

    if llm_classifier is not None:
        return llm_classifier(signal)

    return DiagnosisResult(
        DiagnosisCategory.OTHER,
        DiagnosisSource.RULES,
        0.30,
        "no rule matched and no LLM classifier available ⇒ defaulting to 'other' (manual review)",
    )
