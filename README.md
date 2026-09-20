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
- Real-time FastAPI scoring with strict contracts, idempotent retries, and health probes
- Interactive investigator console with a prioritized queue, explainable risk signals,
  channel mix, policy workload, and analyst filters
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

Start the scoring service with the optional API dependencies:

```bash
pip install -e '.[api]'
FRAUDSTREAM_REVIEW_THRESHOLD=40 FRAUDSTREAM_DECLINE_THRESHOLD=70 \
  uvicorn fraudstream.api:app --host 0.0.0.0 --port 8000
curl http://localhost:8000/health/ready
```

Or run the non-root, health-checked API container:

```bash
docker build -f Dockerfile.api -t fraudstream-api .
docker run --rm -p 8000:8000 fraudstream-api
```

Launch the investigator console against a deterministic synthetic transaction scenario:

```bash
pip install -e '.[dashboard]'
streamlit run src/fraudstream/dashboard.py
```

Or run its non-root, health-checked container:

```bash
docker build -f Dockerfile.dashboard -t fraudstream-dashboard .
docker run --rm -p 8501:8501 fraudstream-dashboard
```

Open `http://localhost:8501` to tune review and decline thresholds, compare workload, inspect
the risk-sorted alert queue, filter operational actions, and trace alerts to machine-readable
decision signals. The console calls the same simulator and stateful scorer used by the CLI; it
does not rely on hand-authored dashboard totals or expose synthetic ground-truth fraud labels to
the investigator view.

Interactive OpenAPI documentation is available at `http://localhost:8000/docs`. Submit a
transaction to `POST /v1/transactions/score`; exact retries are served idempotently, while reuse
of a transaction ID with changed data returns `409 Conflict`. Ground-truth fraud labels are not
accepted by the public API, preventing evaluation data from leaking into live decisions.

The in-memory scorer is intended for a single API worker because velocity and travel signals are
stateful. The service serializes state transitions for safe concurrent requests and bounds its
idempotency cache. A durable state store and partitioned event transport are the next scaling step.

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
adds durable streaming transport, offline feature computation, model training, and monitoring
while preserving the contracts established here.

## Data ethics

All records are synthetic. No personal, banking, or cardholder data is used. Scenario labels are
generated from explicit behavioral patterns rather than protected demographic attributes.

## License

MIT
