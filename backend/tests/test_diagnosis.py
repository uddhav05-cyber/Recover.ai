"""Diagnose-step tests: the deterministic rules table + LLM-fallback wiring.

These are pure/synchronous (no DB, no network) — the diagnosis engine reads a
:class:`FailureSignal` and returns a :class:`DiagnosisResult`.
"""

from __future__ import annotations

from app.agent.diagnosis import (
    DiagnosisResult,
    FailureSignal,
    diagnose,
    diagnose_from_rules,
)
from app.enums import DiagnosisCategory, DiagnosisSource


def _signal(
    code: str | None = None, desc: str | None = None, event: str = "payment.failed"
) -> FailureSignal:
    return FailureSignal(event_type=event, error_code=code, error_description=desc)


# --- rule matching, one per category --------------------------------------


def test_insufficient_funds_matched() -> None:
    result = diagnose_from_rules(
        _signal("BAD_REQUEST_ERROR", "payment failed due to insufficient funds")
    )
    assert result is not None
    assert result.category is DiagnosisCategory.INSUFFICIENT_FUNDS
    assert result.source is DiagnosisSource.RULES
    assert result.confidence == 0.97


def test_expired_card_matched() -> None:
    result = diagnose_from_rules(_signal("GATEWAY_ERROR", "the card is expired"))
    assert result is not None
    assert result.category is DiagnosisCategory.EXPIRED_OR_INVALID_CARD


def test_do_not_honour_maps_to_card() -> None:
    result = diagnose_from_rules(_signal(desc="do not honour"))
    assert result is not None
    assert result.category is DiagnosisCategory.EXPIRED_OR_INVALID_CARD


def test_bank_gateway_error_matched() -> None:
    result = diagnose_from_rules(
        _signal("GATEWAY_ERROR", "issuer bank timed out, please try again")
    )
    assert result is not None
    assert result.category is DiagnosisCategory.BANK_OR_GATEWAY_ERROR


def test_mandate_revoked_matched() -> None:
    result = diagnose_from_rules(_signal(desc="the mandate was revoked by customer"))
    assert result is not None
    assert result.category is DiagnosisCategory.MANDATE_REVOKED
    assert result.confidence == 0.98


# --- normalization + precedence --------------------------------------------


def test_matching_is_case_insensitive() -> None:
    result = diagnose_from_rules(_signal(desc="INSUFFICIENT FUNDS"))
    assert result is not None
    assert result.category is DiagnosisCategory.INSUFFICIENT_FUNDS


def test_error_code_participates_in_match() -> None:
    # The keyword lives only in the code, not the description.
    result = diagnose_from_rules(_signal(code="INSUFFICIENT_FUNDS", desc="declined"))
    assert result is not None
    assert result.category is DiagnosisCategory.INSUFFICIENT_FUNDS


def test_mandate_takes_precedence_over_funds() -> None:
    # Both signals present; mandate revocation is higher-stakes and checked first.
    result = diagnose_from_rules(_signal(desc="mandate revoked; also insufficient funds"))
    assert result is not None
    assert result.category is DiagnosisCategory.MANDATE_REVOKED


def test_funds_takes_precedence_over_card() -> None:
    # "card declined" (card) + "insufficient funds" (funds): funds is the real cause.
    result = diagnose_from_rules(_signal(desc="card declined: insufficient funds"))
    assert result is not None
    assert result.category is DiagnosisCategory.INSUFFICIENT_FUNDS


# --- event overrides --------------------------------------------------------


def test_subscription_cancelled_event_overrides_to_mandate_revoked() -> None:
    result = diagnose_from_rules(_signal(event="subscription.cancelled"))
    assert result is not None
    assert result.category is DiagnosisCategory.MANDATE_REVOKED


# --- abstention (ambiguous) -------------------------------------------------


def test_no_signal_returns_none() -> None:
    assert diagnose_from_rules(_signal()) is None


def test_unrecognized_text_returns_none() -> None:
    assert diagnose_from_rules(_signal(desc="something totally unmapped happened")) is None


# --- diagnose(): rules -> LLM -> OTHER --------------------------------------


def test_diagnose_uses_rules_first_and_skips_llm() -> None:
    def _classifier(_: FailureSignal) -> DiagnosisResult:
        raise AssertionError("LLM must not be consulted when a rule matches")

    result = diagnose(_signal(desc="insufficient funds"), llm_classifier=_classifier)
    assert result.category is DiagnosisCategory.INSUFFICIENT_FUNDS
    assert result.source is DiagnosisSource.RULES


def test_diagnose_falls_back_to_llm_when_ambiguous() -> None:
    sentinel = DiagnosisResult(
        DiagnosisCategory.EXPIRED_OR_INVALID_CARD,
        DiagnosisSource.LLM,
        0.71,
        "llm decided",
        llm_model="gemini-2.5-flash",
    )

    def _classifier(_: FailureSignal) -> DiagnosisResult:
        return sentinel

    result = diagnose(_signal(desc="totally opaque decline"), llm_classifier=_classifier)
    assert result is sentinel
    assert result.source is DiagnosisSource.LLM


def test_diagnose_defaults_to_other_without_classifier() -> None:
    result = diagnose(_signal(desc="totally opaque decline"))
    assert result.category is DiagnosisCategory.OTHER
    assert result.source is DiagnosisSource.RULES
    assert result.confidence == 0.30
