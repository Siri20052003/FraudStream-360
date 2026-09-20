"""Interactive fraud-operations dashboard built on deterministic demo data."""

from __future__ import annotations

from fraudstream.investigation import InvestigationSnapshot, build_snapshot, filter_queue
from fraudstream.scoring import FraudScorer, RiskPolicy
from fraudstream.simulator import generate_transactions


def create_demo_snapshot(
    *, event_count: int, seed: int, review_threshold: int, decline_threshold: int
) -> InvestigationSnapshot:
    """Run the real scoring path and return a dashboard-ready operations snapshot."""
    events = list(generate_transactions(event_count, seed=seed))
    scorer = FraudScorer(RiskPolicy(review_threshold, decline_threshold))
    decisions = [scorer.score(event) for event in events]
    return build_snapshot(events, decisions)


def main() -> None:
    """Render the Streamlit application; imports stay optional for API-only installs."""
    import streamlit as st

    st.set_page_config(page_title="FraudStream-360", page_icon="🛡️", layout="wide")
    st.title("🛡️ FraudStream-360 Investigator Console")
    st.caption("Synthetic transaction intelligence · Explainable decisions · No customer data")

    with st.sidebar:
        st.header("Simulation controls")
        event_count = st.slider("Transaction volume", 250, 5_000, 1_000, step=250)
        seed = st.number_input("Scenario seed", min_value=1, max_value=1_000_000, value=360)
        review_threshold = st.slider("Review threshold", 1, 89, 40)
        decline_threshold = st.slider(
            "Decline threshold", review_threshold + 1, 100, max(70, review_threshold + 1)
        )
        st.info("Threshold changes rerun the same event stream for a fair policy comparison.")

    snapshot = create_demo_snapshot(
        event_count=event_count,
        seed=int(seed),
        review_threshold=review_threshold,
        decline_threshold=decline_threshold,
    )

    columns = st.columns(5)
    columns[0].metric("Transactions", f"{snapshot.events_processed:,}")
    columns[1].metric("Approval rate", f"{snapshot.approval_rate:.1%}")
    columns[2].metric("Review queue", f"{snapshot.reviews:,}")
    columns[3].metric("Declined", f"{snapshot.declined:,}")
    columns[4].metric("Alerted value", f"${snapshot.alerted_amount:,.0f}")

    queue_tab, signals_tab, policy_tab = st.tabs(
        ["Investigation queue", "Risk signals", "Policy workload"]
    )
    with queue_tab:
        filter_columns = st.columns(2)
        actions = set(
            filter_columns[0].multiselect(
                "Actions", ["review", "decline"], default=["review", "decline"]
            )
        )
        minimum_score = filter_columns[1].slider("Minimum risk score", 0, 100, 40)
        visible_queue = filter_queue(snapshot.queue, actions=actions, minimum_score=minimum_score)
        st.caption(f"{len(visible_queue):,} prioritized alerts match the current filters")
        st.dataframe(
            [item.to_record() for item in visible_queue],
            use_container_width=True,
            hide_index=True,
            column_config={
                "amount": st.column_config.NumberColumn("amount", format="$%.2f"),
                "occurred_at": st.column_config.DatetimeColumn(
                    "occurred_at", format="YYYY-MM-DD HH:mm:ss"
                ),
                "risk_score": st.column_config.ProgressColumn(
                    "risk_score", min_value=0, max_value=100, format="%d"
                ),
            },
        )

    with signals_tab:
        left, right = st.columns(2)
        left.subheader("Alert drivers")
        left.bar_chart(dict(snapshot.reason_counts), horizontal=True)
        right.subheader("Alerts by channel")
        right.bar_chart(dict(snapshot.channel_alert_counts))
        st.caption(
            "Signal counts are decision explanations, not ground-truth labels. "
            "Analysts can trace every queued alert back to its triggered controls."
        )

    with policy_tab:
        st.subheader("Decision distribution")
        st.bar_chart(
            {
                "approve": snapshot.approved,
                "review": snapshot.reviews,
                "decline": snapshot.declined,
            }
        )
        alert_rate = (snapshot.reviews + snapshot.declined) / snapshot.events_processed
        st.metric("Investigator alert rate", f"{alert_rate:.2%}")
        st.warning(
            "This console uses deterministic synthetic scenarios for portfolio demonstration. "
            "Production actions require calibrated thresholds, durable state, and human oversight."
        )


if __name__ == "__main__":
    main()
