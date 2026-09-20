from datetime import UTC, datetime, timedelta

import pytest

from fraudstream.investigation import build_snapshot, filter_queue
from fraudstream.models import TransactionEvent
from fraudstream.scoring import RiskDecision


def event(identifier: int, *, amount: float = 25.0, channel: str = "ecommerce") -> TransactionEvent:
    return TransactionEvent(
        transaction_id=f"txn_{identifier}",
        account_id=f"acct_{identifier}",
        merchant_id="merchant_1",
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=identifier),
        amount=amount,
        currency="USD",
        channel=channel,
        latitude=30.2672,
        longitude=-97.7431,
        device_id=f"device_{identifier}",
    )


def test_snapshot_builds_prioritized_queue_and_operations_metrics() -> None:
    events = [event(1), event(2, amount=1_500), event(3, amount=400, channel="mobile_wallet")]
    decisions = [
        RiskDecision("txn_1", 0, "approve", ()),
        RiskDecision("txn_2", 85, "decline", ("HIGH_VALUE", "NEW_DEVICE")),
        RiskDecision("txn_3", 45, "review", ("NEW_DEVICE",)),
    ]

    snapshot = build_snapshot(events, decisions)

    assert snapshot.events_processed == 3
    assert (snapshot.approved, snapshot.reviews, snapshot.declined) == (1, 1, 1)
    assert snapshot.approval_rate == pytest.approx(1 / 3)
    assert snapshot.alerted_amount == 1_900
    assert [item.transaction_id for item in snapshot.queue] == ["txn_2", "txn_3"]
    assert snapshot.queue[0].priority == "critical"
    assert snapshot.reason_counts == (("NEW_DEVICE", 2), ("HIGH_VALUE", 1))
    assert snapshot.channel_alert_counts == (("ecommerce", 1), ("mobile_wallet", 1))


def test_queue_filters_actions_and_score_without_reordering() -> None:
    events = [event(1), event(2)]
    decisions = [
        RiskDecision("txn_1", 75, "decline", ("HIGH_VALUE",)),
        RiskDecision("txn_2", 50, "review", ("NEW_DEVICE",)),
    ]
    queue = build_snapshot(events, decisions).queue

    assert filter_queue(queue, actions={"decline"}, minimum_score=40) == (queue[0],)
    assert filter_queue(queue, actions={"review", "decline"}, minimum_score=60) == (queue[0],)


def test_snapshot_rejects_unaligned_inputs() -> None:
    with pytest.raises(ValueError, match="same length"):
        build_snapshot([event(1)], [])
    with pytest.raises(ValueError, match="aligned"):
        build_snapshot([event(1)], [RiskDecision("different", 0, "approve", ())])


def test_empty_snapshot_has_safe_zero_rates() -> None:
    snapshot = build_snapshot([], [])
    assert snapshot.events_processed == 0
    assert snapshot.approval_rate == 0
    assert snapshot.queue == ()
