"""Command line entry point for reproducible stream simulation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fraudstream.evaluation import EvaluationTracker
from fraudstream.scoring import FraudScorer
from fraudstream.simulator import generate_transactions


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate and score a payment event stream")
    parser.add_argument("--count", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fraud-rate", type=float, default=0.04)
    parser.add_argument("--output", type=Path, default=Path("data/scored_transactions.jsonl"))
    parser.add_argument(
        "--metrics-output",
        type=Path,
        help="Optional JSON path for labeled simulation performance metrics",
    )
    parser.add_argument("--alert-threshold", type=int, default=40)
    args = parser.parse_args()

    scorer = FraudScorer()
    evaluator = EvaluationTracker(threshold=args.alert_threshold)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    action_counts = {"approve": 0, "review": 0, "decline": 0}
    with args.output.open("w", encoding="utf-8") as stream:
        for event in generate_transactions(args.count, seed=args.seed, fraud_rate=args.fraud_rate):
            decision = scorer.score(event)
            evaluator.update(event, decision)
            action_counts[decision.action] += 1
            stream.write(
                json.dumps(
                    {
                        **event.to_dict(),
                        "risk_score": decision.score,
                        "action": decision.action,
                        "reasons": decision.reasons,
                    }
                )
                + "\n"
            )
    summary = {"events": args.count, "actions": action_counts, "output": str(args.output)}
    if args.metrics_output:
        args.metrics_output.parent.mkdir(parents=True, exist_ok=True)
        report = evaluator.report().to_dict()
        args.metrics_output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        summary["metrics_output"] = str(args.metrics_output)
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
