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