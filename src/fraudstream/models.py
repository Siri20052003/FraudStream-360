"""Validated domain contracts for the transaction stream."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class TransactionEvent:
    """Canonical event accepted by the fraud decision service."""

    transaction_id: str
    account_id: str
    merchant_id: str
    occurred_at: datetime
    amount: float
    currency: str
    channel: str
    latitude: float
    longitude: float
    device_id: str
    is_fraud: bool = False
    fraud_pattern: str = "legitimate"

    def __post_init__(self) -> None:
        if not self.transaction_id or not self.account_id or not self.merchant_id:
            raise ValueError("transaction, account, and merchant identifiers are required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if self.amount <= 0:
            raise ValueError("amount must be positive")
        if self.currency != "USD":
            raise ValueError("v1 accepts USD transactions only")
        if self.channel not in {"card_present", "ecommerce", "mobile_wallet"}:
            raise ValueError(f"unsupported channel: {self.channel}")
        if not -90 <= self.latitude <= 90 or not -180 <= self.longitude <= 180:
            raise ValueError("invalid transaction coordinates")

    def to_dict(self) -> dict[str, Any]:
        record = asdict(self)
        record["occurred_at"] = self.occurred_at.astimezone(UTC).isoformat()
        return record

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> TransactionEvent:
        values = dict(record)
        values["occurred_at"] = datetime.fromisoformat(str(values["occurred_at"]))
        return cls(**values)
