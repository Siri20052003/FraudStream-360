"""Deterministic payment stream with behavior-based fraud scenarios."""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from fraudstream.models import TransactionEvent

CITY_COORDINATES = (
    (30.2672, -97.7431),  # Austin
    (29.7604, -95.3698),  # Houston
    (32.7767, -96.7970),  # Dallas
    (40.7128, -74.0060),  # New York
    (34.0522, -118.2437),  # Los Angeles
)
CHANNELS = ("card_present", "ecommerce", "mobile_wallet")


def generate_transactions(
    count: int = 1_000,
    *,
    seed: int = 42,
    fraud_rate: float = 0.04,
    start_at: datetime | None = None,
) -> Iterator[TransactionEvent]:
    """Yield reproducible chronological events with four explainable attack patterns."""
    if count < 1:
        raise ValueError("count must be positive")
    if not 0 <= fraud_rate <= 1:
        raise ValueError("fraud_rate must be between zero and one")

    rng = random.Random(seed)
    cursor = start_at or datetime(2026, 1, 1, tzinfo=UTC)
    account_locations: dict[str, tuple[float, float]] = {}
    previous_account: str | None = None

    for index in range(count):
        cursor += timedelta(seconds=rng.randint(2, 45))
        account_id = f"acct_{rng.randint(1, max(20, count // 10)):05d}"
        location = account_locations.setdefault(account_id, rng.choice(CITY_COORDINATES[:3]))
        amount = round(max(1.0, rng.lognormvariate(3.7, 0.85)), 2)
        channel = rng.choices(CHANNELS, weights=(45, 35, 20), k=1)[0]
        pattern = "legitimate"

        if rng.random() < fraud_rate:
            pattern = rng.choice(
                ("card_testing", "account_takeover", "impossible_travel", "merchant_abuse")
            )
            if pattern == "card_testing":
                account_id = previous_account or account_id
                amount = round(rng.uniform(0.75, 4.99), 2)
                channel = "ecommerce"
            elif pattern == "account_takeover":
                amount = round(rng.uniform(900, 4_500), 2)
                channel = "mobile_wallet"
            elif pattern == "impossible_travel":
                account_id = previous_account or account_id
                location = rng.choice(CITY_COORDINATES[3:])
            else:
                amount = round(rng.uniform(250, 1_200), 2)

        previous_account = account_id
        latitude, longitude = location
        yield TransactionEvent(
            transaction_id=f"txn_{seed}_{index:08d}",
            account_id=account_id,
            merchant_id=(
                "merchant_risky_001"
                if pattern == "merchant_abuse"
                else f"merchant_{rng.randint(1, 200):04d}"
            ),
            occurred_at=cursor,
            amount=amount,
            currency="USD",
            channel=channel,
            latitude=latitude,
            longitude=longitude,
            device_id=(
                f"new_device_{index}"
                if pattern == "account_takeover"
                else f"device_{account_id[-5:]}"
            ),
            is_fraud=pattern != "legitimate",
            fraud_pattern=pattern,
        )
