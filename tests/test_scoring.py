from datetime import UTC, datetime, timedelta

import pytest

from fraudstream.models import TransactionEvent
from fraudstream.scoring import FraudScorer, RiskPolicy


def event(identifier: int, *, minutes: int = 0, **overrides: object) -> TransactionEvent:
    values = {
        "transaction_id": f"txn_{identifier}",
        "account_id": "acct_1",
        "merchant_id": "merchant_1",
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minutes),
        "amount": 25.0,
        "currency": "USD",
        "channel": "ecommerce",
        "latitude": 30.2672,
        "longitude": -97.7431,
        "device_id": "device_1",
    }
    values.update(overrides)
    return TransactionEvent(**values)


def test_low_risk_event_is_approved() -> None:
    assert FraudScorer().score(event(1)).action == "approve"


def test_velocity_and_spend_trigger_decline() -> None:
    scorer = FraudScorer()
    for number in range(1, 5):
        scorer.score(event(number, minutes=number, amount=500.0))
    decision = scorer.score(event(5, minutes=5, amount=1_200.0))
    assert decision.action == "decline"
    assert {"HIGH_10M_VELOCITY", "HIGH_10M_SPEND"} <= set(decision.reasons)


def test_impossible_travel_is_explainable() -> None:
    scorer = FraudScorer()
    scorer.score(event(1))
    decision = scorer.score(event(2, minutes=10, latitude=40.7128, longitude=-74.0060))
    assert decision.action == "review"
    assert "IMPOSSIBLE_TRAVEL" in decision.reasons


def test_action_thresholds_follow_configured_policy() -> None:
    scorer = FraudScorer(RiskPolicy(review_threshold=20, decline_threshold=50))
    assert scorer.score(event(1, amount=1_000.0)).action == "review"
    assert (
        scorer.score(event(2, minutes=1, amount=1_000.0, merchant_id="merchant_risky_1")).action
        == "decline"
    )


@pytest.mark.parametrize("thresholds", [(-1, 70), (70, 70), (80, 70), (40, 101)])
def test_invalid_policy_thresholds_are_rejected(thresholds: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="thresholds must satisfy"):
        RiskPolicy(*thresholds)
