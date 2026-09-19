"""Streaming evaluation metrics for labeled synthetic transactions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from fraudstream.models import TransactionEvent
from fraudstream.scoring import RiskDecision


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Operational and classification metrics at a fixed alert threshold."""

    threshold: int
    events: int
    fraud_events: int
    alerts: int
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    precision: float
    recall: float
    f1_score: float
    alert_rate: float
    fraud_rate: float
    detection_by_pattern: dict[str, dict[str, float | int]]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable metrics payload."""
        return asdict(self)


class EvaluationTracker:
    """Accumulate metrics without retaining the full transaction stream in memory."""

    def __init__(self, threshold: int = 40) -> None:
        if not 0 <= threshold <= 100:
            raise ValueError("threshold must be between zero and 100")
        self.threshold = threshold
        self._events = 0
        self._fraud_events = 0
        self._alerts = 0
        self._true_positives = 0
        self._false_positives = 0
        self._true_negatives = 0
        self._false_negatives = 0
        self._pattern_totals: dict[str, int] = defaultdict(int)
        self._pattern_detections: dict[str, int] = defaultdict(int)

    def update(self, event: TransactionEvent, decision: RiskDecision) -> None:
        """Record one labeled event and its independently produced decision."""
        if decision.transaction_id != event.transaction_id:
            raise ValueError("event and decision transaction identifiers do not match")

        alerted = decision.score >= self.threshold
        self._events += 1
        self._alerts += int(alerted)
        self._fraud_events += int(event.is_fraud)

        if event.is_fraud:
            self._pattern_totals[event.fraud_pattern] += 1
            self._pattern_detections[event.fraud_pattern] += int(alerted)

        if event.is_fraud and alerted:
            self._true_positives += 1
        elif event.is_fraud:
            self._false_negatives += 1
        elif alerted:
            self._false_positives += 1
        else:
            self._true_negatives += 1

    def report(self) -> EvaluationReport:
        """Build a point-in-time report with safe zero-denominator handling."""
        precision = _safe_divide(self._true_positives, self._alerts)
        recall = _safe_divide(self._true_positives, self._fraud_events)
        f1_score = _safe_divide(2 * precision * recall, precision + recall)
        by_pattern = {
            pattern: {
                "events": total,
                "detected": self._pattern_detections[pattern],
                "recall": round(_safe_divide(self._pattern_detections[pattern], total), 4),
            }
            for pattern, total in sorted(self._pattern_totals.items())
        }
        return EvaluationReport(
            threshold=self.threshold,
            events=self._events,
            fraud_events=self._fraud_events,
            alerts=self._alerts,
            true_positives=self._true_positives,
            false_positives=self._false_positives,
            true_negatives=self._true_negatives,
            false_negatives=self._false_negatives,
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1_score=round(f1_score, 4),
            alert_rate=round(_safe_divide(self._alerts, self._events), 4),
            fraud_rate=round(_safe_divide(self._fraud_events, self._events), 4),
            detection_by_pattern=by_pattern,
        )


def _safe_divide(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0
