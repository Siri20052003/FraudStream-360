from dataclasses import replace
from datetime import UTC, datetime

import pytest

from fraudstream.evaluation import EvaluationTracker
from fraudstream.models import TransactionEvent
from fraudstream.scoring import RiskDecision


def event(identifier: str, *, is_fraud: bool, pattern: str = "legitimate") -> TransactionEvent:
    return TransactionEvent(
        transaction_id=identifier,
        account_id="acct_1",
        merchant_id="merchant_1",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        amount=25.0,
        currency="USD",
        channel="ecommerce",
        latitude=30.2672,
        longitude=-97.7431,
        device_id="device_1",
        is_fraud=is_fraud,
        fraud_pattern=pattern,
    )


def decision(identifier: str, score: int) -> RiskDecision:
    return RiskDecision(identifier, score, "review" if score >= 40 else "approve", ())


def test_report_calculates_confusion_matrix_and_rates() -> None:
    tracker = EvaluationTracker(threshold=40)
    samples = [
        (event("tp", is_fraud=True, pattern="account_takeover"), decision("tp", 80)),
        (event("fn", is_fraud=True, pattern="account_takeover"), decision("fn", 20)),
        (event("fp", is_fraud=False), decision("fp", 45)),
        (event("tn", is_fraud=False), decision("tn", 5)),
    ]
    for labeled_event, risk_decision in samples:
        tracker.update(labeled_event, risk_decision)

    report = tracker.report()
    assert (report.true_positives, report.false_positives) == (1, 1)
    assert (report.true_negatives, report.false_negatives) == (1, 1)
    assert report.precision == report.recall == report.f1_score == 0.5
    assert report.alert_rate == report.fraud_rate == 0.5
    assert report.detection_by_pattern["account_takeover"] == {
        "events": 2,
        "detected": 1,
        "recall": 0.5,
    }


def test_empty_report_is_safe_and_threshold_is_inclusive() -> None:
    tracker = EvaluationTracker(threshold=40)
    assert tracker.report().f1_score == 0.0
    labeled_event = event("boundary", is_fraud=True, pattern="merchant_abuse")
    tracker.update(labeled_event, decision("boundary", 40))
    assert tracker.report().true_positives == 1


def test_rejects_invalid_threshold_and_mismatched_decision() -> None:
    with pytest.raises(ValueError):
        EvaluationTracker(threshold=101)
    tracker = EvaluationTracker()
    labeled_event = event("event", is_fraud=False)
    with pytest.raises(ValueError):
        tracker.update(labeled_event, replace(decision("other", 5), transaction_id="other"))
