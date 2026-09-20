"""HTTP boundary for low-latency, explainable transaction scoring."""

from __future__ import annotations

import os
from collections import OrderedDict
from datetime import datetime
from threading import Lock
from typing import Literal

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from fraudstream.models import TransactionEvent
from fraudstream.scoring import FraudScorer, RiskDecision, RiskPolicy


class ScoreRequest(BaseModel):
    """Public request contract; simulation-only fraud labels are intentionally excluded."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    transaction_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=128)
    merchant_id: str = Field(min_length=1, max_length=128)
    occurred_at: datetime
    amount: float = Field(gt=0, le=10_000_000)
    currency: Literal["USD"]
    channel: Literal["card_present", "ecommerce", "mobile_wallet"]
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    device_id: str = Field(min_length=1, max_length=128)

    def to_event(self) -> TransactionEvent:
        return TransactionEvent(**self.model_dump())


class ScoreResponse(BaseModel):
    transaction_id: str
    risk_score: int = Field(ge=0, le=100)
    action: Literal["approve", "review", "decline"]
    reasons: list[str]
    idempotent_replay: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok", "ready"]
    service: str
    version: str
    events_processed: int | None = None
    review_threshold: int | None = None
    decline_threshold: int | None = None


class DuplicateTransactionError(ValueError):
    """A transaction identifier was reused with different event content."""


class ScoringService:
    """Serialize state transitions and retain a bounded idempotency window."""

    def __init__(self, scorer: FraudScorer, *, idempotency_capacity: int = 10_000) -> None:
        if idempotency_capacity < 1:
            raise ValueError("idempotency_capacity must be positive")
        self.scorer = scorer
        self._capacity = idempotency_capacity
        self._results: OrderedDict[str, tuple[ScoreRequest, RiskDecision]] = OrderedDict()
        self._lock = Lock()
        self.events_processed = 0

    def score(self, request: ScoreRequest) -> tuple[RiskDecision, bool]:
        with self._lock:
            existing = self._results.get(request.transaction_id)
            if existing is not None:
                prior_request, decision = existing
                if prior_request != request:
                    raise DuplicateTransactionError(
                        "transaction_id already exists with different event data"
                    )
                self._results.move_to_end(request.transaction_id)
                return decision, True

            decision = self.scorer.score(request.to_event())
            self._results[request.transaction_id] = (request, decision)
            self.events_processed += 1
            if len(self._results) > self._capacity:
                self._results.popitem(last=False)
            return decision, False


def _integer_setting(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        return int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def create_app(*, policy: RiskPolicy | None = None) -> FastAPI:
    selected_policy = policy or RiskPolicy(
        review_threshold=_integer_setting("FRAUDSTREAM_REVIEW_THRESHOLD", 40),
        decline_threshold=_integer_setting("FRAUDSTREAM_DECLINE_THRESHOLD", 70),
    )
    service = ScoringService(FraudScorer(selected_policy))
    app = FastAPI(
        title="FraudStream-360 Scoring API",
        version="0.1.0",
        description="Stateful, explainable payment-risk decisions for validated events.",
    )

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    def liveness() -> HealthResponse:
        return HealthResponse(status="ok", service="fraudstream-360", version=app.version)

    @app.get("/health/ready", response_model=HealthResponse, tags=["health"])
    def readiness() -> HealthResponse:
        return HealthResponse(
            status="ready",
            service="fraudstream-360",
            version=app.version,
            events_processed=service.events_processed,
            review_threshold=selected_policy.review_threshold,
            decline_threshold=selected_policy.decline_threshold,
        )

    @app.post(
        "/v1/transactions/score",
        response_model=ScoreResponse,
        status_code=status.HTTP_200_OK,
        tags=["scoring"],
    )
    def score_transaction(request: ScoreRequest) -> ScoreResponse:
        try:
            decision, replay = service.score(request)
        except DuplicateTransactionError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
            ) from error
        return ScoreResponse(
            transaction_id=decision.transaction_id,
            risk_score=decision.score,
            action=decision.action,
            reasons=list(decision.reasons),
            idempotent_replay=replay,
        )

    return app


app = create_app()
