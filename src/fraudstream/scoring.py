"""Stateful, explainable risk scoring for an ordered event stream."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta

from fraudstream.models import TransactionEvent


@dataclass(frozen=True, slots=True)
class RiskDecision:
    transaction_id: str
    score: int
    action: str
    reasons: tuple[str, ...]


class FraudScorer:
    """Scores transactions using account velocity, device, location, and merchant signals."""

    def __init__(self) -> None:
        self._recent: dict[str, deque[TransactionEvent]] = defaultdict(deque)
        self._known_devices: dict[str, set[str]] = defaultdict(set)
        self._last_event: dict[str, TransactionEvent] = {}

    def score(self, event: TransactionEvent) -> RiskDecision:
        recent = self._recent[event.account_id]
        cutoff = event.occurred_at - timedelta(minutes=10)
        while recent and recent[0].occurred_at < cutoff:
            recent.popleft()

        score = 0
        reasons: list[str] = []
        if len(recent) >= 4:
            score += 30
            reasons.append("HIGH_10M_VELOCITY")
        if sum(item.amount for item in recent) + event.amount >= 2_500:
            score += 25
            reasons.append("HIGH_10M_SPEND")
        if event.amount >= 1_000:
            score += 20
            reasons.append("HIGH_VALUE")
        if (
            self._known_devices[event.account_id]
            and event.device_id not in self._known_devices[event.account_id]
        ):
            score += 20
            reasons.append("NEW_DEVICE")
        if event.merchant_id.startswith("merchant_risky"):
            score += 35
            reasons.append("WATCHLIST_MERCHANT")

        previous = self._last_event.get(event.account_id)
        if previous is not None:
            hours = max(
                (event.occurred_at - previous.occurred_at).total_seconds() / 3_600, 1 / 3_600
            )
            distance = _haversine_miles(
                previous.latitude, previous.longitude, event.latitude, event.longitude
            )
            if distance / hours > 600:
                score += 45
                reasons.append("IMPOSSIBLE_TRAVEL")

        score = min(score, 100)
        action = "decline" if score >= 70 else "review" if score >= 40 else "approve"
        recent.append(event)
        self._known_devices[event.account_id].add(event.device_id)
        self._last_event[event.account_id] = event
        return RiskDecision(event.transaction_id, score, action, tuple(reasons))


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_miles = 3_958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_miles * math.asin(math.sqrt(a))
