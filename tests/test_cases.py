from datetime import UTC, datetime, timedelta

import pytest

from fraudstream.cases import CaseNotFoundError, CaseStore, InvalidCaseTransitionError
from fraudstream.investigation import QueueItem


def alert(
    transaction_id: str = "txn_case_1", *, risk_score: int = 85, action: str = "decline"
) -> QueueItem:
    return QueueItem(
        transaction_id=transaction_id,
        occurred_at=datetime(2026, 9, 21, 13, 0, tzinfo=UTC),
        account_id="acct_1",
        merchant_id="merchant_risky_1",
        amount=1_250.0,
        channel="ecommerce",
        risk_score=risk_score,
        action=action,
        reasons=("HIGH_VALUE", "WATCHLIST_MERCHANT"),
    )


def test_case_is_durable_idempotent_and_has_priority_sla(tmp_path) -> None:
    database = tmp_path / "cases.db"
    opened_at = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)
    store = CaseStore(database)

    created = store.create_from_alert(alert(), at=opened_at)
    replay = store.create_from_alert(alert(), at=opened_at + timedelta(hours=1))
    store.close()

    reopened = CaseStore(database)
    persisted = reopened.get(created.case_id)
    assert replay == created
    assert persisted == created
    assert persisted.priority == "critical"
    assert persisted.due_at == opened_at + timedelta(hours=2)
    assert persisted.sla_state(now=opened_at + timedelta(hours=3)) == "breached"
    assert len(reopened.history(created.case_id)) == 1


def test_assignment_transitions_disposition_and_audit_history() -> None:
    store = CaseStore()
    opened_at = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)
    case = store.create_from_alert(alert(), actor="risk-engine", at=opened_at)

    assigned = store.assign(
        case.case_id, "analyst@example.com", actor="queue-lead", at=opened_at + timedelta(minutes=5)
    )
    active = store.transition(
        case.case_id,
        "in_progress",
        actor="analyst@example.com",
        at=opened_at + timedelta(minutes=10),
    )
    closed = store.transition(
        case.case_id,
        "closed",
        actor="analyst@example.com",
        disposition="confirmed_fraud",
        note="Customer confirmed the transaction was unauthorized.",
        at=opened_at + timedelta(minutes=30),
    )

    assert assigned.assigned_to == "analyst@example.com"
    assert active.status == "in_progress"
    assert closed.disposition == "confirmed_fraud"
    assert closed.sla_state() == "met"
    history = store.history(case.case_id)
    assert [event.event_type for event in history] == [
        "case_created",
        "case_assigned",
        "status_changed",
        "status_changed",
    ]
    assert history[-1].details["note"].startswith("Customer confirmed")


def test_workflow_rejects_invalid_mutations() -> None:
    store = CaseStore()
    case = store.create_from_alert(alert())

    with pytest.raises(InvalidCaseTransitionError, match="requires a disposition"):
        store.transition(case.case_id, "closed", actor="analyst")
    with pytest.raises(InvalidCaseTransitionError, match="cannot transition"):
        store.transition(case.case_id, "pending_information", actor="analyst")
    with pytest.raises(CaseNotFoundError, match="unknown case"):
        store.get("case_missing")
    with pytest.raises(ValueError, match="only review or decline"):
        store.create_from_alert(alert(action="approve", risk_score=0))


def test_case_queue_filters_and_orders_by_operational_priority() -> None:
    store = CaseStore()
    standard = store.create_from_alert(alert("txn_standard", risk_score=45, action="review"))
    critical = store.create_from_alert(alert("txn_critical"))
    store.assign(standard.case_id, "analyst-a", actor="lead")

    assert [case.case_id for case in store.list_cases()] == [critical.case_id, standard.case_id]
    assert store.list_cases(assigned_to="analyst-a") == (store.get(standard.case_id),)
    assert len(store.list_cases(status="new")) == 2
