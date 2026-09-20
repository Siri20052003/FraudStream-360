from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from fraudstream.api import ScoreRequest, ScoringService, create_app
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
    }


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
