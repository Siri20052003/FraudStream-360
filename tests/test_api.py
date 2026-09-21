from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from fraudstream.api import ScoreRequest, ScoringService, create_app
from fraudstream.cases import CaseStore
from fraudstream.scoring import FraudScorer, RiskPolicy


def payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "transaction_id": "txn_api_1",
        "account_id": "acct_api_1",
        "merchant_id": "merchant_1",
        "occurred_at": "2026-09-20T13:00:00Z",
        "amount": 75.0,
        "currency": "USD",
        "channel": "ecommerce",
        "latitude": 30.2672,
        "longitude": -97.7431,
        "device_id": "device_1",
    }
    values.update(overrides)
    return values


def test_health_endpoints_expose_policy_and_progress() -> None:
    client = TestClient(create_app(policy=RiskPolicy(review_threshold=35, decline_threshold=80)))

    assert client.get("/health/live").json()["status"] == "ok"
    ready = client.get("/health/ready").json()

    assert ready["status"] == "ready"
    assert ready["events_processed"] == 0
    assert ready["review_threshold"] == 35
    assert ready["decline_threshold"] == 80


def test_scoring_contract_returns_explainable_decision() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/v1/transactions/score",
        json=payload(merchant_id="merchant_risky_9", amount=1_500.0),
    )

    assert response.status_code == 200
    assert response.json() == {
        "transaction_id": "txn_api_1",
        "risk_score": 55,
        "action": "review",
        "reasons": ["HIGH_VALUE", "WATCHLIST_MERCHANT"],
        "idempotent_replay": False,
        "case_id": "case_txn_api_1",
    }


def test_alert_opens_case_and_supports_investigator_workflow(tmp_path) -> None:
    store = CaseStore(tmp_path / "cases.db")
    client = TestClient(create_app(case_store=store))
    score = client.post(
        "/v1/transactions/score",
        json=payload(merchant_id="merchant_risky_9", amount=1_500.0),
    )
    case_id = score.json()["case_id"]

    cases = client.get("/v1/cases", params={"case_status": "new"}).json()
    assert len(cases) == 1
    assert cases[0]["case_id"] == case_id
    assert cases[0]["sla_state"] == "on_track"

    assigned = client.patch(
        f"/v1/cases/{case_id}/assignment",
        json={"assigned_to": "analyst-a", "actor": "queue-lead"},
    )
    assert assigned.json()["assigned_to"] == "analyst-a"
    assert (
        client.patch(
            f"/v1/cases/{case_id}/status",
            json={"status": "in_progress", "actor": "analyst-a"},
        ).status_code
        == 200
    )
    closed = client.patch(
        f"/v1/cases/{case_id}/status",
        json={
            "status": "closed",
            "actor": "analyst-a",
            "disposition": "confirmed_fraud",
            "note": "Confirmed by customer.",
        },
    )
    assert closed.json()["disposition"] == "confirmed_fraud"
    assert len(client.get(f"/v1/cases/{case_id}/history").json()) == 4


def test_case_api_rejects_invalid_transition_and_unknown_case() -> None:
    client = TestClient(create_app())
    assert client.get("/v1/cases/case_missing/history").status_code == 404

    response = client.post(
        "/v1/transactions/score",
        json=payload(merchant_id="merchant_risky_9", amount=1_500.0),
    )
    case_id = response.json()["case_id"]
    invalid = client.patch(
        f"/v1/cases/{case_id}/status",
        json={"status": "closed", "actor": "analyst-a"},
    )
    assert invalid.status_code == 409
    assert "requires a disposition" in invalid.json()["detail"]


def test_exact_retry_is_idempotent_but_changed_payload_conflicts() -> None:
    client = TestClient(create_app())
    request = payload()

    first = client.post("/v1/transactions/score", json=request)
    replay = client.post("/v1/transactions/score", json=request)
    conflict = client.post("/v1/transactions/score", json={**request, "amount": 76.0})

    assert first.json()["idempotent_replay"] is False
    assert replay.json()["idempotent_replay"] is True
    assert conflict.status_code == 409
    assert client.get("/health/ready").json()["events_processed"] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("currency", "EUR"), ("amount", -1), ("latitude", 91), ("unexpected", "value")],
)
def test_invalid_request_is_rejected(field: str, value: object) -> None:
    assert (
        TestClient(create_app())
        .post("/v1/transactions/score", json={**payload(), field: value})
        .status_code
        == 422
    )


def test_naive_timestamp_is_rejected_by_domain_contract() -> None:
    response = TestClient(create_app()).post(
        "/v1/transactions/score", json=payload(occurred_at="2026-09-20T13:00:00")
    )
    assert response.status_code == 422
    assert "timezone-aware" in response.json()["detail"]


def test_service_rejects_invalid_idempotency_capacity() -> None:
    with pytest.raises(ValueError, match="positive"):
        ScoringService(FraudScorer(), idempotency_capacity=0)


def test_request_datetime_is_normalized_by_pydantic() -> None:
    request = ScoreRequest.model_validate(payload())
    assert request.occurred_at == datetime(2026, 9, 20, 13, 0, tzinfo=UTC)
