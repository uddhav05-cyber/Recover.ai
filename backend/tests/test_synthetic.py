from app.enums import DiagnosisCategory
from app.synthetic import (
    SyntheticOutcome,
    SyntheticResult,
    generate_cases,
    measure_results,
)


def test_generate_cases_is_reproducible_and_balanced() -> None:
    first = generate_cases(60, seed=7)
    second = generate_cases(60, seed=7)

    assert [(case.category, case.amount, case.recover_on_retry) for case in first] == [
        (case.category, case.amount, case.recover_on_retry) for case in second
    ]
    assert {case.category for case in first} == set(DiagnosisCategory)
    assert len(first) == 60


def test_measure_results_reports_recovery_and_exceptions() -> None:
    cases = generate_cases(2, seed=7)
    results = [
        SyntheticResult(cases[0], SyntheticOutcome.RECOVERED, "retry recovered"),
        SyntheticResult(cases[1], SyntheticOutcome.STILL_AT_RISK, "payment link pending"),
    ]

    metrics = measure_results(results)

    assert metrics.total_cases == 2
    assert metrics.recovered_cases == 1
    assert metrics.recovery_rate == 0.5
    assert metrics.amount_recovered == cases[0].amount
    assert metrics.amount_at_risk == cases[1].amount
    assert metrics.exceptions == (results[1],)
