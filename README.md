# FraudStream-360

[![CI](https://github.com/Siri20052003/FraudStream-360/actions/workflows/ci.yml/badge.svg)](https://github.com/Siri20052003/FraudStream-360/actions/workflows/ci.yml)

FraudStream-360 is an original, production-oriented financial fraud intelligence project. It
models the first milliseconds of a card decision: validate an incoming transaction, update
account behavior state, calculate an explainable risk score, and route the payment to approve,
review, or decline.

## Current vertical slice

- Deterministic synthetic transaction stream with card-testing, account-takeover,
  impossible-travel, and merchant-abuse scenarios
- Strict timezone, amount, channel, currency, identity, and geospatial validation
- Stateful 10-minute velocity and spend windows
- New-device, high-value, risky-merchant, and travel-speed signals
- Auditable reason codes and bounded risk actions
- Streaming precision, recall, F1, alert-rate, confusion-matrix, and attack-pattern evaluation
- Capacity-aware threshold calibration with an auditable precision/recall operating table
- JSON Lines output for downstream streaming and analytics work
- Automated tests, linting, Docker packaging, and GitHub Actions smoke validation

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
ruff check .
pytest -q
fraudstream --count 1000 --seed 360 \
  --metrics-output data/evaluation.json \
  --calibration-output data/calibration.json \
  --max-alert-rate 0.05
```

The generated stream is written to `data/scored_transactions.jsonl`. Synthetic fraud labels are
retained only for evaluation; the scorer never reads them when making a decision.

The optional evaluation report treats both `review` and `decline` decisions as alerts by default
(`--alert-threshold 40`). It reports operational workload alongside classification quality and
breaks recall down by attack pattern, making blind spots visible before a policy is deployed.

Calibration evaluates score thresholds from 0 to 100 in a single streaming pass. It recommends
the threshold with the highest fraud recall that keeps alerts within the specified investigator
queue capacity, then uses precision and F1 to break ties. `data/calibration.json` retains every
candidate's confusion matrix and operating metrics so the chosen policy is reproducible and can
be reviewed before deployment. Add `--minimum-precision` when the operation has a firm alert
quality requirement.

## Design

See the [architecture notes](docs/architecture.md) for the event and decision flow. The roadmap
adds durable streaming transport, offline feature computation, model training, monitoring, and an
investigator dashboard while preserving the contracts established here.

## Data ethics

All records are synthetic. No personal, banking, or cardholder data is used. Scenario labels are
generated from explicit behavioral patterns rather than protected demographic attributes.

## License

MIT
