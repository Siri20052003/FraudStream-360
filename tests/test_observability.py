import json
import logging

from fraudstream.observability import JsonFormatter, ServiceMetrics


def test_prometheus_metrics_include_requests_decisions_and_histogram() -> None:
    metrics = ServiceMetrics()
    metrics.observe_request("POST", "/v1/transactions/score", 200, 0.012)
    metrics.observe_decision("review")

    rendered = metrics.render()

    assert 'method="POST",route="/v1/transactions/score",status="200"} 1' in rendered
    assert 'fraudstream_decisions_total{action="review"} 1' in rendered
    assert "fraudstream_http_request_duration_seconds_count 1" in rendered
    assert 'bucket{le="0.025"} 1' in rendered


def test_json_formatter_emits_correlation_and_request_fields() -> None:
    record = logging.LogRecord(
        "fraudstream.service",
        logging.INFO,
        __file__,
        1,
        "http_request_completed",
        (),
        None,
    )
    record.request_id = "request-123"
    record.method = "GET"
    record.path = "/health/live"
    record.status_code = 200
    record.duration_ms = 1.25

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "http_request_completed"
    assert payload["request_id"] == "request-123"
    assert payload["duration_ms"] == 1.25
