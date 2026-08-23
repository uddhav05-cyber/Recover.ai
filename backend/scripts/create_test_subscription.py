"""Create a Razorpay test-mode plan and subscription.

Usage (from backend/):
    uv run python -m scripts.create_test_subscription

Requires RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET in backend/.env (test mode).
Prints the subscription id and its ``short_url`` — open that URL to authorize
the mandate, then use the Razorpay Dashboard (test mode) to simulate a failed
charge and fire a ``payment.failed`` webhook (Phase 2).
"""

from __future__ import annotations

import asyncio
import json

from app.integrations.razorpay import RazorpayClient


async def main() -> None:
    async with RazorpayClient.from_settings() as rzp:
        plan = await rzp.create_plan(
            period="monthly",
            interval=1,
            item={
                "name": "RecoverAI Test Plan",
                "amount": 49900,  # paise (₹499.00)
                "currency": "INR",
            },
            notes={"source": "recoverai-test"},
        )
        print(f"Created plan:         {plan['id']}")

        subscription = await rzp.create_subscription(
            plan_id=plan["id"],
            total_count=12,
            customer_notify=1,
            notes={"source": "recoverai-test"},
        )
        print(f"Created subscription: {subscription['id']}")
        print(f"Authorize at:         {subscription.get('short_url')}")
        print("\nFull subscription entity:")
        print(json.dumps(subscription, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
