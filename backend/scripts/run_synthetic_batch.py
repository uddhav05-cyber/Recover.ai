"""Run a deterministic Phase-5 recovery batch against the local database.

Usage (from backend/):
    uv run python -m scripts.run_synthetic_batch --count 60

This uses a simulated Razorpay client, so no live payment action is performed.
The real Diagnose -> Decide -> Guardrail -> Act code still writes the audit trail;
due retries are then executed through the Phase-4 worker.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.agent.executor import run_recovery
from app.db.models import PaymentEvent, RecoveryAction
from app.db.session import SessionLocal
from app.enums import EventProcessingStatus
from app.synthetic import (
    SyntheticCase,
    SyntheticOutcome,
    SyntheticResult,
    generate_cases,
    measure_results,
)
from app.worker.retry_scheduler import run_due_retries


class SimulatedRazorpayClient:
    def __init__(self, cases: dict[str, SyntheticCase]) -> None:
        self.cases = cases

    async def create_payment_link(self, **kwargs: Any) -> dict[str, Any]:
        return {"id": f"plink_{kwargs['notes']['recovery_action_id']}", "short_url": "https://rzp.io/i/demo"}

    async def fetch_subscription(self, subscription_id: str) -> dict[str, Any]:
        case = next(case for case in self.cases.values() if case.case_id in subscription_id)
        return {"id": subscription_id, "status": "active" if case.recover_on_retry else "halted"}


async def run_batch(cases: list[SyntheticCase]) -> list[SyntheticResult]:
    now = datetime.now(UTC).replace(microsecond=0)
    client = SimulatedRazorpayClient({case.case_id: case for case in cases})
    scheduled: dict[str, Any] = {}

    async with SessionLocal() as session:
        for case in cases:
            event = PaymentEvent(
                razorpay_event_id=case.case_id,
                event_type="payment.failed",
                razorpay_subscription_id=f"sub_{case.case_id}",
                amount=case.amount,
                currency="INR",
                error_code=case.error_code,
                error_description=case.error_description,
                payload={"event": "payment.failed", "synthetic_case_id": case.case_id},
                signature_verified=True,
                processing_status=EventProcessingStatus.PENDING,
            )
            session.add(event)
            await session.flush()
            result = await run_recovery(session, event, now=now, client=client)
            if result.action_type.value == "retry_charge" and result.scheduled_at is not None:
                action = await session.scalar(
                    select(RecoveryAction)
                    .where(
                        RecoveryAction.subscription_id == event.subscription_id,
                        RecoveryAction.scheduled_at == result.scheduled_at,
                    )
                    .order_by(RecoveryAction.created_at.desc())
                )
                if action is None:
                    raise RuntimeError(f"scheduled action missing for {case.case_id}")
                scheduled[case.case_id] = action.id

        due = await run_due_retries(
            session,
            now=now + timedelta(days=8),
            client=client,
            limit=len(scheduled) + 1,
        )
        results: list[SyntheticResult] = []
        for case in cases:
            if case.case_id in scheduled:
                retry = next(
                    item for item in due if item.action_id == scheduled[case.case_id]
                )
                if retry.recovered:
                    results.append(SyntheticResult(case, SyntheticOutcome.RECOVERED, retry.detail))
                else:
                    results.append(
                        SyntheticResult(case, SyntheticOutcome.STILL_AT_RISK, retry.detail)
                    )
            elif case.category.value == "mandate_revoked":
                results.append(SyntheticResult(case, SyntheticOutcome.ESCALATED, "mandate revoked"))
            else:
                results.append(
                    SyntheticResult(case, SyntheticOutcome.STILL_AT_RISK, "payment link pending")
                )
        return results


def print_report(metrics: Any) -> None:
    print(f"Cases processed: {metrics.total_cases}")
    print(f"Recovered:       {metrics.recovered_cases} ({metrics.recovery_rate:.1%})")
    print(f"Amount recovered: Rs {metrics.amount_recovered / 100:,.2f}")
    print(f"Still at risk:    Rs {metrics.amount_at_risk / 100:,.2f}")
    print(f"Exceptions:       {len(metrics.exceptions)}")
    for exception in metrics.exceptions:
        print(f"- {exception.case.case_id}: {exception.outcome.value} - {exception.detail}")


async def main(count: int, seed: int) -> None:
    results = await run_batch(generate_cases(count, seed=seed))
    print_report(measure_results(results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--seed", type=int, default=20260826)
    args = parser.parse_args()
    asyncio.run(main(args.count, args.seed))
