"""Durable investigator case workflow with an append-only audit trail."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Literal

from fraudstream.investigation import QueueItem

CaseStatus = Literal["new", "in_progress", "pending_information", "closed"]
Disposition = Literal["confirmed_fraud", "false_positive", "account_takeover", "merchant_abuse"]

_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "new": {"in_progress", "closed"},
    "in_progress": {"pending_information", "closed"},
    "pending_information": {"in_progress", "closed"},
    "closed": set(),
}
_SLA_HOURS = {"critical": 2, "high": 8, "standard": 24}


class CaseNotFoundError(LookupError):
    """Raised when a requested case identifier is unknown."""


class InvalidCaseTransitionError(ValueError):
    """Raised when a workflow mutation violates case controls."""


@dataclass(frozen=True, slots=True)
class CaseRecord:
    case_id: str
    transaction_id: str
    priority: str
    risk_score: int
    action: str
    amount: float
    reasons: tuple[str, ...]
    status: CaseStatus
    assigned_to: str | None
    disposition: Disposition | None
    opened_at: datetime
    due_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    def sla_state(self, *, now: datetime | None = None) -> str:
        """Return an operational SLA label without mutating persisted state."""
        if self.status == "closed":
            return "met" if self.closed_at and self.closed_at <= self.due_at else "breached"
        reference = now or datetime.now(UTC)
        return "breached" if reference > self.due_at else "on_track"


@dataclass(frozen=True, slots=True)
class CaseEvent:
    event_id: int
    case_id: str
    event_type: str
    actor: str
    occurred_at: datetime
    details: dict[str, object]


class CaseStore:
    """Thread-safe SQLite repository for cases and immutable workflow events."""

    def __init__(self, database: str | Path = ":memory:") -> None:
        database_name = str(database)
        if database_name != ":memory:":
            Path(database_name).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database_name, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        with self._locked_connection() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            if database_name != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    transaction_id TEXT NOT NULL UNIQUE,
                    priority TEXT NOT NULL,
                    risk_score INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    amount REAL NOT NULL,
                    reasons_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_to TEXT,
                    disposition TEXT,
                    opened_at TEXT NOT NULL,
                    due_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    closed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS case_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL REFERENCES cases(case_id),
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cases_queue
                    ON cases(status, due_at, risk_score DESC);
                CREATE INDEX IF NOT EXISTS idx_case_events_history
                    ON case_events(case_id, event_id);
                """
            )

    def create_from_alert(
        self, alert: QueueItem, *, actor: str = "fraudstream", at: datetime | None = None
    ) -> CaseRecord:
        """Create one case per alerted transaction; exact retries return the original case."""
        if alert.action not in {"review", "decline"}:
            raise ValueError("only review or decline alerts can create cases")
        timestamp = _utc(at)
        case_id = f"case_{alert.transaction_id}"
        due_at = timestamp + timedelta(hours=_SLA_HOURS[alert.priority])
        with self._locked_connection() as connection:
            existing = connection.execute(
                "SELECT * FROM cases WHERE transaction_id = ?", (alert.transaction_id,)
            ).fetchone()
            if existing is not None:
                return _case_from_row(existing)
            connection.execute(
                """
                INSERT INTO cases (
                    case_id, transaction_id, priority, risk_score, action, amount,
                    reasons_json, status, opened_at, due_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
                """,
                (
                    case_id,
                    alert.transaction_id,
                    alert.priority,
                    alert.risk_score,
                    alert.action,
                    alert.amount,
                    json.dumps(alert.reasons),
                    _serialize(timestamp),
                    _serialize(due_at),
                    _serialize(timestamp),
                ),
            )
            self._append_event(
                connection,
                case_id,
                "case_created",
                actor,
                timestamp,
                {"priority": alert.priority, "sla_hours": _SLA_HOURS[alert.priority]},
            )
            return self._get(connection, case_id)

    def get(self, case_id: str) -> CaseRecord:
        with self._locked_connection() as connection:
            return self._get(connection, case_id)

    def list_cases(
        self, *, status: CaseStatus | None = None, assigned_to: str | None = None
    ) -> tuple[CaseRecord, ...]:
        query = "SELECT * FROM cases WHERE 1 = 1"
        parameters: list[str] = []
        if status is not None:
            query += " AND status = ?"
            parameters.append(status)
        if assigned_to is not None:
            query += " AND assigned_to = ?"
            parameters.append(assigned_to)
        query += (
            " ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, due_at"
        )
        with self._locked_connection() as connection:
            return tuple(_case_from_row(row) for row in connection.execute(query, parameters))

    def assign(
        self, case_id: str, assigned_to: str, *, actor: str, at: datetime | None = None
    ) -> CaseRecord:
        analyst = assigned_to.strip()
        if not analyst:
            raise ValueError("assigned_to is required")
        timestamp = _utc(at)
        with self._locked_connection() as connection:
            current = self._get(connection, case_id)
            if current.status == "closed":
                raise InvalidCaseTransitionError("closed cases cannot be reassigned")
            connection.execute(
                "UPDATE cases SET assigned_to = ?, updated_at = ? WHERE case_id = ?",
                (analyst, _serialize(timestamp), case_id),
            )
            self._append_event(
                connection,
                case_id,
                "case_assigned",
                actor,
                timestamp,
                {"from": current.assigned_to, "to": analyst},
            )
            return self._get(connection, case_id)

    def transition(
        self,
        case_id: str,
        status: CaseStatus,
        *,
        actor: str,
        disposition: Disposition | None = None,
        note: str | None = None,
        at: datetime | None = None,
    ) -> CaseRecord:
        timestamp = _utc(at)
        with self._locked_connection() as connection:
            current = self._get(connection, case_id)
            if status not in _ALLOWED_TRANSITIONS[current.status]:
                raise InvalidCaseTransitionError(
                    f"cannot transition case from {current.status} to {status}"
                )
            if status == "closed" and disposition is None:
                raise InvalidCaseTransitionError("closing a case requires a disposition")
            if status != "closed" and disposition is not None:
                raise InvalidCaseTransitionError("disposition is only valid when closing a case")
            closed_at = timestamp if status == "closed" else None
            connection.execute(
                """
                UPDATE cases
                SET status = ?, disposition = ?, updated_at = ?, closed_at = ?
                WHERE case_id = ?
                """,
                (
                    status,
                    disposition,
                    _serialize(timestamp),
                    _serialize(closed_at) if closed_at else None,
                    case_id,
                ),
            )
            details: dict[str, object] = {"from": current.status, "to": status}
            if disposition is not None:
                details["disposition"] = disposition
            if note and note.strip():
                details["note"] = note.strip()
            self._append_event(connection, case_id, "status_changed", actor, timestamp, details)
            return self._get(connection, case_id)

    def history(self, case_id: str) -> tuple[CaseEvent, ...]:
        with self._locked_connection() as connection:
            self._get(connection, case_id)
            rows = connection.execute(
                "SELECT * FROM case_events WHERE case_id = ? ORDER BY event_id", (case_id,)
            ).fetchall()
            return tuple(
                CaseEvent(
                    event_id=row["event_id"],
                    case_id=row["case_id"],
                    event_type=row["event_type"],
                    actor=row["actor"],
                    occurred_at=datetime.fromisoformat(row["occurred_at"]),
                    details=json.loads(row["details_json"]),
                )
                for row in rows
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def ping(self) -> bool:
        """Verify that the durable store can execute a query for readiness checks."""
        try:
            with self._locked_connection() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def _get(self, connection: sqlite3.Connection, case_id: str) -> CaseRecord:
        row = connection.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise CaseNotFoundError(f"unknown case: {case_id}")
        return _case_from_row(row)

    def _append_event(
        self,
        connection: sqlite3.Connection,
        case_id: str,
        event_type: str,
        actor: str,
        occurred_at: datetime,
        details: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO case_events (case_id, event_type, actor, occurred_at, details_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                case_id,
                event_type,
                actor,
                _serialize(occurred_at),
                json.dumps(details, sort_keys=True),
            ),
        )

    def _locked_connection(self) -> Iterator[sqlite3.Connection]:
        return _LockedConnection(self._lock, self._connection)


class _LockedConnection:
    def __init__(self, lock: RLock, connection: sqlite3.Connection) -> None:
        self._lock = lock
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        self._lock.acquire()
        return self._connection

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._lock.release()


def _case_from_row(row: sqlite3.Row) -> CaseRecord:
    return CaseRecord(
        case_id=row["case_id"],
        transaction_id=row["transaction_id"],
        priority=row["priority"],
        risk_score=row["risk_score"],
        action=row["action"],
        amount=row["amount"],
        reasons=tuple(json.loads(row["reasons_json"])),
        status=row["status"],
        assigned_to=row["assigned_to"],
        disposition=row["disposition"],
        opened_at=datetime.fromisoformat(row["opened_at"]),
        due_at=datetime.fromisoformat(row["due_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
    )


def _utc(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("case timestamps must be timezone-aware")
    return timestamp.astimezone(UTC)


def _serialize(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
