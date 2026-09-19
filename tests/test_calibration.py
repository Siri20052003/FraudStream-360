from datetime import UTC, datetime

import pytest

from fraudstream.calibration import ThresholdCalibrator
from fraudstream.models import TransactionEvent
from fraudstream.scoring import RiskDecision


def sample(identifier: str, score: int, *, is_fraud: bool) -> tuple[TransactionEvent, RiskDecision]:
    event = TransactionEvent(
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
        fraud_pattern="card_testing" if is_fraud else "legitimate",
    )
    action = "decline" if score >= 70 else "review" if score >= 40 else "approve"
    return event, RiskDecision(identifier, score, action, ())


def test_recommends_highest_recall_point_within_queue_capacity() -> None:
    calibrator = ThresholdCalibrator((20, 40, 60))
    observations = (
        sample("fraud-high", 80, is_fraud=True),
        sample("fraud-mid", 45, is_fraud=True),
        sample("legitimate-mid", 30, is_fraud=False),
        *(sample(f"legitimate-{index}", 0, is_fraud=False) for index in range(7)),
    )
    for event, decision in observations:
        calibrator.update(event, decision)

    report = calibrator.recommend(max_alert_rate=0.2)

    assert report.selected_threshold == 40
    assert report.selected.alerts == 2
    assert report.selected.recall == report.selected.precision == 1.0
    assert [point.threshold for point in report.candidates] == [20, 40, 60]


def test_precision_constraint_can_select_a_stricter_threshold() -> None:
    calibrator = ThresholdCalibrator((20, 40, 60))
    for observation in (
        sample("fraud", 80, is_fraud=True),
        sample("false-alert", 45, is_fraud=False),
        sample("legitimate", 0, is_fraud=False),
    ):
        calibrator.update(*observation)

    assert calibrator.recommend(max_alert_rate=1.0, minimum_precision=0.75).selected_threshold == 60


@pytest.mark.parametrize(
    ("thresholds", "message"),
    [((), "at least one"), ((-1,), "between zero and 100"), ((40.5,), "integers")],
)
def test_rejects_invalid_candidate_thresholds(thresholds: tuple[int, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ThresholdCalibrator(thresholds)


def test_rejects_empty_stream_and_invalid_constraints() -> None:
    calibrator = ThresholdCalibrator((40,))
    with pytest.raises(ValueError, match="empty"):
        calibrator.recommend()
    with pytest.raises(ValueError, match="max_alert_rate"):
        calibrator.recommend(max_alert_rate=1.1)
    with pytest.raises(ValueError, match="minimum_precision"):
        calibrator.recommend(minimum_precision=-0.1)
