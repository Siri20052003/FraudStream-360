"""Capacity-aware calibration for fraud alert score thresholds."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from fraudstream.evaluation import EvaluationReport, EvaluationTracker
from fraudstream.models import TransactionEvent
from fraudstream.scoring import RiskDecision


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Candidate performance and the best capacity-feasible operating point."""

    selected_threshold: int
    max_alert_rate: float
    minimum_precision: float
    selected: EvaluationReport
    candidates: tuple[EvaluationReport, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable calibration artifact."""
        return asdict(self)


class ThresholdCalibrator:
    """Evaluate many thresholds in one pass over an ordered labeled stream."""

    def __init__(self, thresholds: Iterable[int] = range(0, 101, 5)) -> None:
        unique_thresholds = tuple(sorted(set(thresholds)))
        if not unique_thresholds:
            raise ValueError("at least one threshold is required")
        if any(not isinstance(value, int) or not 0 <= value <= 100 for value in unique_thresholds):
            raise ValueError("thresholds must be integers between zero and 100")
        self._trackers = tuple(EvaluationTracker(value) for value in unique_thresholds)

    def update(self, event: TransactionEvent, decision: RiskDecision) -> None:
        """Update every candidate using one independently produced risk decision."""
        for tracker in self._trackers:
            tracker.update(event, decision)

    def recommend(
        self,
        *,
        max_alert_rate: float = 0.05,
        minimum_precision: float = 0.0,
    ) -> CalibrationReport:
        """Maximize recall within queue capacity, then prefer precision and fewer alerts."""
        if not 0 <= max_alert_rate <= 1:
            raise ValueError("max_alert_rate must be between zero and one")
        if not 0 <= minimum_precision <= 1:
            raise ValueError("minimum_precision must be between zero and one")

        candidates = tuple(tracker.report() for tracker in self._trackers)
        if not candidates[0].events:
            raise ValueError("cannot calibrate an empty event stream")
        feasible = tuple(
            point
            for point in candidates
            if point.alert_rate <= max_alert_rate and point.precision >= minimum_precision
        )
        if not feasible:
            raise ValueError("no threshold satisfies the alert-rate and precision constraints")

        selected = max(
            feasible,
            key=lambda point: (
                point.recall,
                point.precision,
                point.f1_score,
                -point.alert_rate,
                point.threshold,
            ),
        )
        return CalibrationReport(
            selected_threshold=selected.threshold,
            max_alert_rate=max_alert_rate,
            minimum_precision=minimum_precision,
            selected=selected,
            candidates=candidates,
        )
