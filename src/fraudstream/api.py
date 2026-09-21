"""HTTP boundary for low-latency, explainable transaction scoring."""

from __future__ import annotations

import os
from collections import OrderedDict
from datetime import datetime
from threading import Lock
from typing import Literal

from fastapi import FastAPI, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from fraudstream.cases import (
    CaseEvent,
    CaseNotFoundError,
    CaseRecord,
    CaseStatus,
    CaseStore,
    Disposition,
    InvalidCaseTransitionError,
)
from fraudstream.investigation import QueueItem
from fraudstream.models import TransactionEvent
from fraudstream.observability import ServiceMetrics, TelemetryMiddleware, service_logger
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
    case_id: str | None = None


class CaseResponse(BaseModel):
    case_id: str
    transaction_id: str
    priority: str
    risk_score: int
    action: str
    amount: float
    reasons: list[str]
    status: CaseStatus
    assigned_to: str | None
    disposition: Disposition | None
    opened_at: datetime
    due_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    sla_state: Literal["on_track", "breached", "met"]

    @classmethod
    def from_record(cls, record: CaseRecord) -> CaseResponse:
        return cls(
            **{
                **{
                    field: getattr(record, field)
                    for field in cls.model_fields
                    if field != "sla_state"
                },
                "reasons": list(record.reasons),
                "sla_state": record.sla_state(),
            }
        )


class AssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    assigned_to: str = Field(min_length=1, max_length=128)
    actor: str = Field(min_length=1, max_length=128)


class TransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: CaseStatus
    actor: str = Field(min_length=1, max_length=128)
    disposition: Disposition | None = None
    note: str | None = Field(default=None, max_length=1_000)


class CaseEventResponse(BaseModel):
    event_id: int
    case_id: str
    event_type: str
    actor: str
    occurred_at: datetime
    details: dict[str, object]

    @classmethod
    def from_event(cls, event: CaseEvent) -> CaseEventResponse:
        return cls(**{field: getattr(event, field) for field in cls.model_fields})


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

    def __init__(
        self,
        scorer: FraudScorer,
        *,
        case_store: CaseStore | None = None,
        idempotency_capacity: int = 10_000,
    ) -> None:
        if idempotency_capacity < 1:
            raise ValueError("idempotency_capacity must be positive")
        self.scorer = scorer
        self.case_store = case_store or CaseStore()
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


def create_app(*, policy: RiskPolicy | None = None, case_store: CaseStore | None = None) -> FastAPI:
    selected_policy = policy or RiskPolicy(
        review_threshold=_integer_setting("FRAUDSTREAM_REVIEW_THRESHOLD", 40),
        decline_threshold=_integer_setting("FRAUDSTREAM_DECLINE_THRESHOLD", 70),
    )
    service = ScoringService(FraudScorer(selected_policy), case_store=case_store)
    metrics = ServiceMetrics()
    app = FastAPI(
        title="FraudStream-360 Scoring API",
        version="0.1.0",
        description="Stateful, explainable payment-risk decisions for validated events.",
    )
    app.add_middleware(
        TelemetryMiddleware,
        metrics=metrics,
        logger=service_logger(),
    )

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    def liveness() -> HealthResponse:
        return HealthResponse(status="ok", service="fraudstream-360", version=app.version)

    @app.get("/health/ready", response_model=HealthResponse, tags=["health"])
    def readiness() -> HealthResponse:
        if not service.case_store.ping():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
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
        case_id = None
        if decision.action != "approve":
            event = request.to_event()
            case = service.case_store.create_from_alert(
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
            case_id = case.case_id
        if not replay:
            metrics.observe_decision(decision.action)
        return ScoreResponse(
            transaction_id=decision.transaction_id,
            risk_score=decision.score,
            action=decision.action,
            reasons=list(decision.reasons),
            idempotent_replay=replay,
            case_id=case_id,
        )

    @app.get("/v1/cases", response_model=list[CaseResponse], tags=["investigations"])
    def list_cases(
        case_status: CaseStatus | None = None, assigned_to: str | None = None
    ) -> list[CaseResponse]:
        return [
            CaseResponse.from_record(case)
            for case in service.case_store.list_cases(status=case_status, assigned_to=assigned_to)
        ]

    @app.patch(
        "/v1/cases/{case_id}/assignment",
        response_model=CaseResponse,
        tags=["investigations"],
    )
    def assign_case(case_id: str, request: AssignmentRequest) -> CaseResponse:
        try:
            case = service.case_store.assign(case_id, request.assigned_to, actor=request.actor)
        except CaseNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except (InvalidCaseTransitionError, ValueError) as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return CaseResponse.from_record(case)

    @app.patch(
        "/v1/cases/{case_id}/status",
        response_model=CaseResponse,
        tags=["investigations"],
    )
    def transition_case(case_id: str, request: TransitionRequest) -> CaseResponse:
        try:
            case = service.case_store.transition(
                case_id,
                request.status,
                actor=request.actor,
                disposition=request.disposition,
                note=request.note,
            )
        except CaseNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except InvalidCaseTransitionError as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        return CaseResponse.from_record(case)

    @app.get(
        "/v1/cases/{case_id}/history",
        response_model=list[CaseEventResponse],
        tags=["investigations"],
    )
    def case_history(case_id: str) -> list[CaseEventResponse]:
        try:
            events = service.case_store.history(case_id)
        except CaseNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        return [CaseEventResponse.from_event(event) for event in events]

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> Response:
        return Response(metrics.render(), media_type="text/plain; version=0.0.4")

    return app


app = create_app(case_store=CaseStore(os.getenv("FRAUDSTREAM_CASE_DB", "data/cases.db")))
