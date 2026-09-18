from datetime import UTC, datetime

import pytest

from fraudstream.models import TransactionEvent


def valid_event(**overrides: object) -> TransactionEvent:
    values = {
        "transaction_id": "txn_1",
        "account_id": "acct_1",
        "merchant_id": "merchant_1",
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC),
        "amount": 25.0,
        "currency": "USD",
        "channel": "ecommerce",
        "latitude": 30.2,
        "longitude": -97.7,
        "device_id": "device_1",
    }
    values.update(overrides)
    return TransactionEvent(**values)


def test_event_round_trip() -> None:
    event = valid_event()
    assert TransactionEvent.from_dict(event.to_dict()) == event


@pytest.mark.parametrize("field,value", [("amount", 0), ("latitude", 91), ("channel", "atm")])
def test_invalid_event_is_rejected(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        valid_event(**{field: value})
