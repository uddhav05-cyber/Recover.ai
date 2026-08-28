from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_dashboard_summary_contract(aclient: AsyncClient) -> None:
    response = await aclient.get("/api/dashboard/summary")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "failed_payments",
        "amount_recovered_paise",
        "amount_at_risk_paise",
        "active_actions",
    }
    assert all(isinstance(value, int) for value in body.values())


@pytest.mark.asyncio
async def test_dashboard_overview_contract(aclient: AsyncClient) -> None:
    response = await aclient.get("/api/dashboard/overview")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"funnel", "subscriptions", "exceptions"}
    assert set(body["funnel"]) == {"failed", "actioned", "recovered"}
    assert isinstance(body["subscriptions"], list)
    assert isinstance(body["exceptions"], list)


@pytest.mark.asyncio
async def test_auth_login_returns_demo_bearer_token(aclient: AsyncClient) -> None:
    response = await aclient.post(
        "/api/auth/login",
        json={"email": "demo@recover.ai", "password": "demo"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"] == {"email": "demo@recover.ai", "name": "demo"}
    assert body["access_token"].startswith("demo_token_")


@pytest.mark.asyncio
async def test_phase_six_dashboard_contracts(aclient: AsyncClient) -> None:
    subscriptions = await aclient.get("/api/subscriptions?skip=0&limit=8")
    metrics = await aclient.get("/api/recovery-metrics")
    exceptions = await aclient.get("/api/exceptions?skip=0&limit=8")

    assert subscriptions.status_code == 200
    subscriptions_body = subscriptions.json()
    assert subscriptions_body["skip"] == 0
    assert subscriptions_body["limit"] == 8
    assert subscriptions_body["total"] >= len(subscriptions_body["items"])
    assert len(subscriptions_body["items"]) <= 8

    assert metrics.status_code == 200
    metrics_body = metrics.json()
    assert metrics_body["total_subscriptions"] >= 0
    assert metrics_body["failed_events"] >= 0
    assert 0.0 <= metrics_body["recovery_rate"] <= 1.0
    assert isinstance(metrics_body["funnel"], list)

    assert exceptions.status_code == 200
    exceptions_body = exceptions.json()
    assert exceptions_body["skip"] == 0
    assert exceptions_body["limit"] == 8
    assert exceptions_body["total"] >= len(exceptions_body["items"])
    assert len(exceptions_body["items"]) <= 8