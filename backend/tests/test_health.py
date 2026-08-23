from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_contract(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert isinstance(body["database"]["connected"], bool)
    # 200 exactly when the database answered the ping.
    assert (resp.status_code == 200) == (body["database"]["connected"] is True)


def test_root_banner(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "RecoverAI"
