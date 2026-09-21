"""Small, repeatable HTTP load probe for a running FraudStream API."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor


def submit(base_url: str, sequence: int) -> tuple[float, str]:
    payload = {
        "transaction_id": f"load_{sequence}",
        "account_id": f"account_{sequence % 50}",
        "merchant_id": "merchant_risky_load" if sequence % 10 == 0 else "merchant_load",
        "occurred_at": "2026-09-21T14:00:00Z",
        "amount": 1500 if sequence % 10 == 0 else 45 + sequence % 100,
        "currency": "USD",
        "channel": "ecommerce",
        "latitude": 30.2672,
        "longitude": -97.7431,
        "device_id": f"device_{sequence % 75}",
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/transactions/score",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Request-ID": f"load-{sequence}"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
    return (time.perf_counter() - started) * 1000, result["action"]


def percentile(values: list[float], fraction: float) -> float:
    return sorted(values)[min(int(len(values) * fraction), len(values) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=20)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("requests and concurrency must be positive")

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda sequence: submit(args.url, sequence), range(args.requests)))
    elapsed = time.perf_counter() - started
    latencies = [latency for latency, _ in results]
    report = {
        "requests": len(results),
        "concurrency": args.concurrency,
        "requests_per_second": round(len(results) / elapsed, 2),
        "latency_ms": {
            "median": round(statistics.median(latencies), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "max": round(max(latencies), 2),
        },
        "actions": dict(Counter(action for _, action in results)),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
