"""Read models for an investigator-facing fraud operations dashboard."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from fraudstream.models import TransactionEvent
from fraudstream.scoring import RiskDecision


@dataclass(frozen=True, slots=True)
class QueueItem:
    """One actionable alert, ordered for investigator triage."""

    transaction_id: str
    occurred_at: datetime
    account_id: str
    merchant_id: str
    amount: float
    channel: str
    risk_score: int
    action: str
    reasons: tuple[str, ...]

    @property
    def priority(self) -> str:
        if self.action == "decline" or self.risk_score >= 80:
            return "critical"
        if self.risk_score >= 55:
            return "high"
        return "standard"

    def to_record(self) -> dict[str, object]:
        return {
            "priority": self.priority,
            "risk_score": self.risk_score,
            "action": self.action,
            "amount": self.amount,
            "occurred_at": self.occurred_at,
            "transaction_id": self.transaction_id,
            "account_id": self.account_id,
            "merchant_id": self.merchant_id,
            "channel": self.channel,
            "risk_signals": ", ".join(self.reasons) or "NONE",
        }


@dataclass(frozen=True, slots=True)
class InvestigationSnapshot:
    """A deterministic operational view derived from scored events."""

    events_processed: int
    approved: int
    reviews: int
    declined: int
    approval_rate: float
    alerted_amount: float
    queue: tuple[QueueItem, ...]
    reason_counts: tuple[tuple[str, int], ...]
    channel_alert_counts: tuple[tuple[str, int], ...]


def build_snapshot(
    events: Sequence[TransactionEvent], decisions: Sequence[RiskDecision]
) -> InvestigationSnapshot:
    """Join events to decisions and compute a queue plus workload indicators."""
    if len(events) != len(decisions):
        raise ValueError("events and decisions must have the same length")

    action_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    channel_alert_counts: Counter[str] = Counter()
    queue: list[QueueItem] = []
    alerted_amount = 0.0

    for event, decision in zip(events, decisions, strict=True):
        if event.transaction_id != decision.transaction_id:
            raise ValueError("events and decisions must be aligned by transaction_id")
        action_counts[decision.action] += 1
        if decision.action == "approve":
            continue

        alerted_amount += event.amount
        reason_counts.update(decision.reasons)
        channel_alert_counts[event.channel] += 1
        queue.append(
            QueueItem(
                transaction_id=event.transaction_id,
                occurred_at=event.occurred_at,
                account_id=event.account_id,
                merchant_id=event.merchant_id,
                amount=event.amount,
                channel=event.channel,
                risk_score=decision.score,
                action=decision.action,
                reasons=decision.reasons,
            )
        )

    queue.sort(key=lambda item: (item.risk_score, item.occurred_at), reverse=True)
    event_count = len(events)
    return InvestigationSnapshot(
        events_processed=event_count,
        approved=action_counts["approve"],
        reviews=action_counts["review"],
        declined=action_counts["decline"],
        approval_rate=action_counts["approve"] / event_count if event_count else 0.0,
        alerted_amount=round(alerted_amount, 2),
        queue=tuple(queue),
        reason_counts=_rank_counts(reason_counts),
        channel_alert_counts=_rank_counts(channel_alert_counts),
    )


def filter_queue(
    queue: Iterable[QueueItem], *, actions: set[str], minimum_score: int
) -> tuple[QueueItem, ...]:
    """Apply analyst-selected queue controls without changing queue priority."""
    return tuple(
        item for item in queue if item.action in actions and item.risk_score >= minimum_score
    )


def _rank_counts(counts: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
