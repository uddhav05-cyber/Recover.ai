"""Synthetic batch generation and measurement for Phase 5 demos."""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.enums import DiagnosisCategory


class SyntheticOutcome(StrEnum):
    RECOVERED = "recovered"
    STILL_AT_RISK = "still_at_risk"
    ESCALATED = "escalated"
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class SyntheticCase:
    case_id: str
    category: DiagnosisCategory
    amount: int
    error_code: str
    error_description: str
    recover_on_retry: bool


@dataclass(frozen=True, slots=True)
class SyntheticResult:
    case: SyntheticCase
    outcome: SyntheticOutcome
    detail: str


@dataclass(frozen=True, slots=True)
class BatchMetrics:
    total_cases: int
    recovered_cases: int
    recovery_rate: float
    amount_recovered: int
    amount_at_risk: int
    exceptions: tuple[SyntheticResult, ...]


def generate_cases(count: int = 60, *, seed: int = 20260826) -> list[SyntheticCase]:
    """Generate a reproducible, balanced batch of realistic failed payments."""
    if count < 1:
        raise ValueError("count must be positive")

    rng = random.Random(seed)
    categories = tuple(DiagnosisCategory)
    descriptions = {
        DiagnosisCategory.INSUFFICIENT_FUNDS: ("BAD_REQUEST_ERROR", "insufficient funds"),
        DiagnosisCategory.EXPIRED_OR_INVALID_CARD: ("CARD_EXPIRED", "card is expired"),
        DiagnosisCategory.BANK_OR_GATEWAY_ERROR: (
            "GATEWAY_ERROR",
            "bank server temporarily unavailable",
        ),
        DiagnosisCategory.MANDATE_REVOKED: ("AUTHENTICATION_ERROR", "mandate revoked by customer"),
        DiagnosisCategory.OTHER: ("UNKNOWN_ERROR", "unrecognized payment failure"),
    }
    retry_rates = {
        DiagnosisCategory.INSUFFICIENT_FUNDS: 0.65,
        DiagnosisCategory.EXPIRED_OR_INVALID_CARD: 0.0,
        DiagnosisCategory.BANK_OR_GATEWAY_ERROR: 0.55,
        DiagnosisCategory.MANDATE_REVOKED: 0.0,
        DiagnosisCategory.OTHER: 0.35,
    }
    amounts = (29900, 49900, 79900, 99900, 149900)

    cases: list[SyntheticCase] = []
    for index in range(count):
        category = categories[index % len(categories)]
        error_code, description = descriptions[category]
        cases.append(
            SyntheticCase(
                case_id=f"synthetic_{index + 1:03d}_{uuid.uuid4().hex[:8]}",
                category=category,
                amount=rng.choice(amounts),
                error_code=error_code,
                error_description=description,
                recover_on_retry=rng.random() < retry_rates[category],
            )
        )
    return cases


def measure_results(results: list[SyntheticResult]) -> BatchMetrics:
    """Calculate demo metrics while retaining every unresolved exception."""
    recovered = [result for result in results if result.outcome is SyntheticOutcome.RECOVERED]
    exceptions = tuple(
        result for result in results if result.outcome is not SyntheticOutcome.RECOVERED
    )
    total_amount = sum(result.case.amount for result in results)
    return BatchMetrics(
        total_cases=len(results),
        recovered_cases=len(recovered),
        recovery_rate=len(recovered) / len(results) if results else 0.0,
        amount_recovered=sum(result.case.amount for result in recovered),
        amount_at_risk=total_amount - sum(result.case.amount for result in recovered),
        exceptions=exceptions,
    )
