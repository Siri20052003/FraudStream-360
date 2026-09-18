# Architecture

```mermaid
flowchart LR
    A[Deterministic event simulator] --> B[Validated transaction contract]
    B --> C[Stateful scoring engine]
    C --> D{Risk policy}
    D -->|0-39| E[Approve]
    D -->|40-69| F[Manual review]
    D -->|70-100| G[Decline]
    C --> H[Explainability reason codes]
    E & F & G & H --> I[Scored JSONL event log]
```

The first vertical slice keeps transport concerns separate from fraud logic. Events enter a
strict domain contract; the stateful scorer maintains only the recent account history required
for velocity, spend, device, location, and merchant checks. A later Kafka-compatible adapter can
replace the simulator without changing the scoring API.

## Decision contract

Every decision contains a bounded 0–100 score, an operational action, and machine-readable reason
codes. This makes outcomes suitable for investigator queues, audit logs, and future dashboards.

