from pathlib import Path

from streamlit.testing.v1 import AppTest

from fraudstream.dashboard import create_demo_snapshot


def test_demo_snapshot_runs_the_real_scoring_path_deterministically() -> None:
    first = create_demo_snapshot(
        event_count=500, seed=360, review_threshold=40, decline_threshold=70
    )
    second = create_demo_snapshot(
        event_count=500, seed=360, review_threshold=40, decline_threshold=70
    )
    assert first == second
    assert first.events_processed == 500
    assert first.reviews + first.declined == len(first.queue)
    assert first.queue


def test_dashboard_renders_metrics_queue_and_policy_tabs() -> None:
    dashboard_path = Path(__file__).parents[1] / "src/fraudstream/dashboard.py"
    app = AppTest.from_file(dashboard_path, default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value.endswith("FraudStream-360 Investigator Console")
    assert len(app.metric) == 6
    assert [tab.label for tab in app.tabs] == [
        "Investigation queue",
        "Risk signals",
        "Policy workload",
    ]
    assert app.dataframe
