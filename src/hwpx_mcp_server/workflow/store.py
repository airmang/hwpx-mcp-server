# SPDX-License-Identifier: Apache-2.0
"""Crash-safe SQLite state and tamper-evident append-only workflow ledger."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .models import (
    WorkOrder,
    WorkflowEvent,
    WorkflowRecord,
    WorkflowState,
    canonical_json,
    content_hash,
    sanitize_ledger_payload,
    utc_now,
)
from .state_machine import assert_transition


class WorkflowConflict(RuntimeError):
    pass


class WorkflowNotFound(KeyError):
    pass


class WorkflowStore:
    """Durable local workflow store.

    Each mutation uses ``BEGIN IMMEDIATE`` and ``synchronous=FULL``.  The event
    chain makes accidental history edits detectable while SQLite WAL provides
    process-crash recovery.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    work_order_json TEXT NOT NULL,
                    work_order_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    original_content_hash TEXT,
                    output_content_hash TEXT,
                    stop_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS workflow_events (
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
                    event_index INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT,
                    event_hash TEXT NOT NULL,
                    event_key TEXT,
                    PRIMARY KEY (workflow_id, event_index),
                    UNIQUE (workflow_id, event_hash),
                    UNIQUE (workflow_id, event_key)
                );
                CREATE INDEX IF NOT EXISTS workflow_events_lookup
                    ON workflow_events(workflow_id, event_index);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(workflow_events)").fetchall()
            }
            if "event_key" not in columns:
                connection.execute("ALTER TABLE workflow_events ADD COLUMN event_key TEXT")
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS workflow_events_event_key "
                    "ON workflow_events(workflow_id, event_key) WHERE event_key IS NOT NULL"
                )

    def create(
        self,
        work_order: WorkOrder,
        *,
        original_content_hash: str | None = None,
        workflow_id: str | None = None,
    ) -> tuple[WorkflowRecord, bool]:
        """Create or idempotently recover a workflow.

        Returns ``(record, created)``. Reusing a key for different content fails
        closed instead of silently returning the unrelated workflow.
        """

        workflow_id = workflow_id or f"wf_{uuid.uuid4().hex}"
        now = utc_now()
        order_json = canonical_json(work_order.model_dump(mode="json"))
        order_hash = work_order.fingerprint()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM workflows WHERE idempotency_key = ?", (work_order.idempotency_key,)
            ).fetchone()
            if existing is not None:
                if existing["work_order_hash"] != order_hash:
                    raise WorkflowConflict("idempotency key was already used for a different work order")
                return self._record_from_row(existing), False

            connection.execute(
                """INSERT INTO workflows(
                    workflow_id, idempotency_key, work_order_json, work_order_hash,
                    state, state_version, created_at, updated_at, original_content_hash
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    workflow_id,
                    work_order.idempotency_key,
                    order_json,
                    order_hash,
                    WorkflowState.INTAKE.value,
                    now.isoformat(),
                    now.isoformat(),
                    original_content_hash,
                ),
            )
            self._append_event(
                connection,
                workflow_id=workflow_id,
                event_type="workflow.created",
                from_state=None,
                to_state=WorkflowState.INTAKE,
                payload=work_order.ledger_summary(),
                occurred_at=now,
            )
            row = connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            assert row is not None
            return self._record_from_row(row), True

    def get(self, workflow_id: str) -> WorkflowRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
        if row is None:
            raise WorkflowNotFound(workflow_id)
        return self._record_from_row(row)

    def transition(
        self,
        workflow_id: str,
        target: WorkflowState,
        *,
        expected_state: WorkflowState,
        expected_version: int,
        event_type: str = "workflow.transitioned",
        payload: dict[str, Any] | None = None,
        output_content_hash: str | None = None,
        stop_reason: str | None = None,
    ) -> WorkflowRecord:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            if row is None:
                raise WorkflowNotFound(workflow_id)
            current = WorkflowState(row["state"])
            version = int(row["state_version"])
            if event_type in {"decision.approved", "decision.rejected"}:
                raise WorkflowConflict("decision receipts must use the same-state append_event policy path")
            if current != expected_state or version != expected_version:
                raise WorkflowConflict(
                    f"stale workflow state: expected {expected_state.value}@{expected_version}, "
                    f"actual {current.value}@{version}"
                )
            assert_transition(current, target)
            now = utc_now()
            updated = connection.execute(
                """UPDATE workflows
                   SET state = ?, state_version = state_version + 1, updated_at = ?,
                       output_content_hash = COALESCE(?, output_content_hash),
                       stop_reason = COALESCE(?, stop_reason)
                   WHERE workflow_id = ? AND state = ? AND state_version = ?""",
                (
                    target.value,
                    now.isoformat(),
                    output_content_hash,
                    stop_reason,
                    workflow_id,
                    current.value,
                    version,
                ),
            )
            if updated.rowcount != 1:
                raise WorkflowConflict("workflow changed concurrently")
            self._append_event(
                connection,
                workflow_id=workflow_id,
                event_type=event_type,
                from_state=current,
                to_state=target,
                payload=payload or {},
                occurred_at=now,
            )
            result = connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            assert result is not None
            return self._record_from_row(result)

    def append_event(
        self,
        workflow_id: str,
        event_type: str,
        *,
        expected_state: WorkflowState,
        expected_version: int,
        payload: dict[str, Any] | None = None,
        event_key: str | None = None,
    ) -> tuple[WorkflowRecord, WorkflowEvent, bool]:
        """Append an idempotent same-state receipt under optimistic CAS.

        Same-state actions are mutations too, so they advance ``state_version``.
        An ``event_key`` makes retries return the original receipt without
        creating a second event, while reuse for different content fails closed.
        """

        raw_payload = payload or {}
        safe_payload = sanitize_ledger_payload(raw_payload, key="event")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            if row is None:
                raise WorkflowNotFound(workflow_id)
            current = WorkflowState(row["state"])
            version = int(row["state_version"])
            if event_type in {"decision.approved", "decision.rejected"}:
                if current != WorkflowState.DECISION:
                    raise WorkflowConflict("decision receipts are only valid in decision state")
                if not isinstance(raw_payload.get("actionHash"), str):
                    raise WorkflowConflict("decision receipt requires an action hash")
                expected_approval = event_type == "decision.approved"
                if raw_payload.get("approved") is not expected_approval:
                    raise WorkflowConflict("decision receipt type and approved value disagree")
            if event_key is not None:
                existing = connection.execute(
                    "SELECT * FROM workflow_events WHERE workflow_id = ? AND event_key = ?",
                    (workflow_id, event_key),
                ).fetchone()
                if existing is not None:
                    event = self._event_from_row(existing)
                    if event.event_type != event_type or event.payload != safe_payload:
                        raise WorkflowConflict("event key was already used for a different receipt")
                    return self._record_from_row(row), event, False
            if current != expected_state or version != expected_version:
                raise WorkflowConflict(
                    f"stale workflow state: expected {expected_state.value}@{expected_version}, "
                    f"actual {current.value}@{version}"
                )
            now = utc_now()
            updated = connection.execute(
                """UPDATE workflows SET state_version = state_version + 1, updated_at = ?
                   WHERE workflow_id = ? AND state = ? AND state_version = ?""",
                (now.isoformat(), workflow_id, current.value, version),
            )
            if updated.rowcount != 1:
                raise WorkflowConflict("workflow changed concurrently")
            self._append_event(
                connection,
                workflow_id=workflow_id,
                event_type=event_type,
                from_state=current,
                to_state=current,
                payload=raw_payload,
                occurred_at=now,
                event_key=event_key,
            )
            result = connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            event_row = connection.execute(
                "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY event_index DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
            assert result is not None and event_row is not None
            return self._record_from_row(result), self._event_from_row(event_row), True

    def events(self, workflow_id: str, *, verify_chain: bool = True) -> list[WorkflowEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY event_index", (workflow_id,)
            ).fetchall()
        if not rows and not self._exists(workflow_id):
            raise WorkflowNotFound(workflow_id)
        events = [self._event_from_row(row) for row in rows]
        if verify_chain:
            previous: str | None = None
            for event in events:
                if event.previous_hash != previous or self._calculate_event_hash(event) != event.event_hash:
                    raise WorkflowConflict(f"workflow event chain is invalid at index {event.index}")
                previous = event.event_hash
        return events

    def _exists(self, workflow_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM workflows WHERE workflow_id = ?", (workflow_id,)
            ).fetchone() is not None

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        workflow_id: str,
        event_type: str,
        from_state: WorkflowState | None,
        to_state: WorkflowState,
        payload: dict[str, Any],
        occurred_at: datetime,
        event_key: str | None = None,
    ) -> None:
        last = connection.execute(
            "SELECT event_index, event_hash FROM workflow_events WHERE workflow_id = ? ORDER BY event_index DESC LIMIT 1",
            (workflow_id,),
        ).fetchone()
        index = int(last["event_index"]) + 1 if last else 0
        previous_hash = str(last["event_hash"]) if last else None
        safe_payload = sanitize_ledger_payload(payload, key="event")
        event = WorkflowEvent(
            workflow_id=workflow_id,
            index=index,
            event_type=event_type,
            from_state=from_state,
            to_state=to_state,
            occurred_at=occurred_at,
            payload=safe_payload,
            previous_hash=previous_hash,
            event_hash="pending",
            event_key=event_key,
        )
        event_hash = self._calculate_event_hash(event)
        connection.execute(
            """INSERT INTO workflow_events(
                workflow_id, event_index, event_type, from_state, to_state,
                occurred_at, payload_json, previous_hash, event_hash
                , event_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                workflow_id,
                index,
                event_type,
                from_state.value if from_state else None,
                to_state.value,
                occurred_at.isoformat(),
                canonical_json(safe_payload),
                previous_hash,
                event_hash,
                event_key,
            ),
        )

    @staticmethod
    def _calculate_event_hash(event: WorkflowEvent) -> str:
        hashed = {
            "workflowId": event.workflow_id,
            "index": event.index,
            "eventType": event.event_type,
            "fromState": event.from_state.value if event.from_state else None,
            "toState": event.to_state.value,
            "occurredAt": event.occurred_at.isoformat(),
            "payload": event.payload,
            "previousHash": event.previous_hash,
        }
        if event.event_key is not None:
            hashed["eventKey"] = event.event_key
        return content_hash(hashed)

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> WorkflowRecord:
        return WorkflowRecord(
            workflow_id=row["workflow_id"],
            work_order=WorkOrder.model_validate_json(row["work_order_json"]),
            state=WorkflowState(row["state"]),
            state_version=int(row["state_version"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            original_content_hash=row["original_content_hash"],
            output_content_hash=row["output_content_hash"],
            stop_reason=row["stop_reason"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> WorkflowEvent:
        return WorkflowEvent(
            workflow_id=row["workflow_id"],
            index=int(row["event_index"]),
            event_type=row["event_type"],
            from_state=WorkflowState(row["from_state"]) if row["from_state"] else None,
            to_state=WorkflowState(row["to_state"]),
            occurred_at=datetime.fromisoformat(row["occurred_at"]),
            payload=json.loads(row["payload_json"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
            event_key=row["event_key"],
        )


__all__ = ["WorkflowConflict", "WorkflowNotFound", "WorkflowStore"]
