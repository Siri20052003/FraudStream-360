"""Dependency-free HTTP telemetry for the scoring service."""

from __future__ import annotations

import json
import logging
from collections import Counter
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)


class ServiceMetrics:
    """Thread-safe request and decision counters rendered in Prometheus text format."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: Counter[tuple[str, str, int]] = Counter()
        self._decisions: Counter[str] = Counter()
        self._latencies: list[float] = []

    def observe_request(self, method: str, route: str, status_code: int, seconds: float) -> None:
        with self._lock:
            self._requests[(method, route, status_code)] += 1
            self._latencies.append(seconds)

    def observe_decision(self, action: str) -> None:
        with self._lock:
            self._decisions[action] += 1

    def render(self) -> str:
        with self._lock:
            requests = self._requests.copy()
            decisions = self._decisions.copy()
            latencies = tuple(self._latencies)

        lines = [
            "# HELP fraudstream_http_requests_total HTTP requests processed.",
            "# TYPE fraudstream_http_requests_total counter",
        ]
        for (method, route, status_code), count in sorted(requests.items()):
            labels = f'method="{method}",route="{route}",status="{status_code}"'
            lines.append(f"fraudstream_http_requests_total{{{labels}}} {count}")
        lines.extend(
            [
                "# HELP fraudstream_decisions_total Fraud decisions by action.",
                "# TYPE fraudstream_decisions_total counter",
            ]
        )
        for action, count in sorted(decisions.items()):
            lines.append(f'fraudstream_decisions_total{{action="{action}"}} {count}')
        lines.extend(
            [
                "# HELP fraudstream_http_request_duration_seconds Request latency.",
                "# TYPE fraudstream_http_request_duration_seconds histogram",
            ]
        )
        for boundary in _LATENCY_BUCKETS:
            count = sum(value <= boundary for value in latencies)
            lines.append(
                f'fraudstream_http_request_duration_seconds_bucket{{le="{boundary:g}"}} {count}'
            )
        lines.append(
            f'fraudstream_http_request_duration_seconds_bucket{{le="+Inf"}} {len(latencies)}'
        )
        lines.append(f"fraudstream_http_request_duration_seconds_sum {sum(latencies):.9f}")
        lines.append(f"fraudstream_http_request_duration_seconds_count {len(latencies)}")
        return "\n".join(lines) + "\n"


class JsonFormatter(logging.Formatter):
    """Emit compact, machine-readable service logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        for field in ("request_id", "method", "path", "status_code", "duration_ms"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


class TelemetryMiddleware(BaseHTTPMiddleware):
    """Attach correlation IDs and record one structured completion event per request."""

    def __init__(self, app: Any, *, metrics: ServiceMetrics, logger: logging.Logger) -> None:
        super().__init__(app)
        self.metrics = metrics
        self.logger = logger

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        started = perf_counter()
        response = await call_next(request)
        duration = perf_counter() - started
        route = getattr(request.scope.get("route"), "path", request.url.path)
        self.metrics.observe_request(request.method, route, response.status_code, duration)
        response.headers["X-Request-ID"] = request_id
        self.logger.info(
            "http_request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": route,
                "status_code": response.status_code,
                "duration_ms": round(duration * 1000, 3),
            },
        )
        return response


def service_logger() -> logging.Logger:
    logger = logging.getLogger("fraudstream.service")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
