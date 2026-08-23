"""Razorpay integration: async REST client + webhook signature verification.

We use an async ``httpx`` client (not the official synchronous ``razorpay``
SDK) so Razorpay calls never block the FastAPI event loop. All usage is
test-mode only.
"""

from __future__ import annotations

import hashlib
import hmac
from types import TracebackType
from typing import Any

import httpx

from app.config import Settings, get_settings

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verify Razorpay's ``X-Razorpay-Signature`` header.

    Razorpay signs the *raw* request body with HMAC-SHA256 using the webhook
    secret. Compared in constant time to avoid a timing side channel.
    """
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


class RazorpayClient:
    """Thin async wrapper over the Razorpay REST API (HTTP Basic auth)."""

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        *,
        base_url: str = RAZORPAY_API_BASE,
        timeout: float = 10.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            auth=(key_id, key_secret),
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> RazorpayClient:
        """Build a client from app settings; raises if credentials are unset."""
        settings = settings or get_settings()
        if not settings.razorpay_key_id or not settings.razorpay_key_secret:
            raise RuntimeError(
                "Razorpay credentials missing: set RAZORPAY_KEY_ID and "
                "RAZORPAY_KEY_SECRET in backend/.env"
            )
        return cls(settings.razorpay_key_id, settings.razorpay_key_secret)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> RazorpayClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = await self._client.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp.json()

    # --- Plans / customers / subscriptions ---------------------------------
    async def create_plan(
        self,
        *,
        period: str,
        interval: int,
        item: dict[str, Any],
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"period": period, "interval": interval, "item": item}
        if notes:
            payload["notes"] = notes
        return await self._request("POST", "/plans", json=payload)

    async def create_customer(
        self,
        *,
        name: str,
        email: str,
        contact: str,
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "email": email,
            "contact": contact,
            "fail_existing": 0,
        }
        if notes:
            payload["notes"] = notes
        return await self._request("POST", "/customers", json=payload)

    async def create_subscription(
        self,
        *,
        plan_id: str,
        total_count: int,
        customer_notify: int = 1,
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "plan_id": plan_id,
            "total_count": total_count,
            "customer_notify": customer_notify,
        }
        if notes:
            payload["notes"] = notes
        return await self._request("POST", "/subscriptions", json=payload)

    async def fetch_subscription(self, subscription_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/subscriptions/{subscription_id}")

    async def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/payments/{payment_id}")

    async def create_payment_link(
        self,
        *,
        amount: int,
        currency: str = "INR",
        description: str | None = None,
        customer: dict[str, Any] | None = None,
        notes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"amount": amount, "currency": currency}
        if description:
            payload["description"] = description
        if customer:
            payload["customer"] = customer
        if notes:
            payload["notes"] = notes
        return await self._request("POST", "/payment_links", json=payload)
