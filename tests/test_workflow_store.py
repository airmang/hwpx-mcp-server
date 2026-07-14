from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from pydantic import ValidationError

from hwpx_mcp_server.workflow import (
    InvalidTransition,
    WorkFamily,
    WorkOrder,
    WorkflowConflict,
    WorkflowState,
    WorkflowStore,
)
from hwpx_mcp_server.workflow.models import utc_now


def order(**changes: object) -> WorkOrder:
    values: dict[str, object] = {
        "family": WorkFamily.UNKNOWN_FORM_FILL,
        "source_path": "/tmp/input.hwpx",
        "output_path": "/tmp/output.hwpx",
        "expected_revision": "sha256:old",
        "idempotency_key": "request-0001",
        "parameters": {"fieldNames": ["성명", "학년"], "promptBody": "must-not-enter-ledger"},
    }
    values.update(changes)
    return WorkOrder.model_validate(values)


def test_create_is_idempotent_and_ledger_omits_parameter_values(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite3")

    first, created = store.create(order(), original_content_hash="sha256:input")
    recovered, created_again = store.create(order(), original_content_hash="sha256:input")

    assert created is True
    assert created_again is False
    assert recovered.workflow_id == first.workflow_id
    events = store.events(first.workflow_id)
    assert len(events) == 1
    serialized = str(events[0].payload)
    assert "must-not-enter-ledger" not in serialized
    assert events[0].payload["parameterKeys"] == ["fieldNames", "promptBody"]


def test_idempotency_key_reuse_with_different_work_is_rejected(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    store.create(order())

    with pytest.raises(WorkflowConflict, match="different work order"):
        store.create(order(parameters={"fieldNames": ["주소"]}))


def test_state_and_event_commit_atomically_and_survive_reopen(tmp_path):
    path = tmp_path / "workflow.sqlite3"
    store = WorkflowStore(path)
    created, _ = store.create(order())
    recon = store.transition(
        created.workflow_id,
        WorkflowState.RECON,
        expected_state=WorkflowState.INTAKE,
        expected_version=1,
        payload={"sourceHash": "sha256:input"},
    )

    reopened = WorkflowStore(path)
    recovered = reopened.get(created.workflow_id)
    assert recovered.state == WorkflowState.RECON
    assert recovered.state_version == 2
    assert [event.to_state for event in reopened.events(created.workflow_id)] == [
        WorkflowState.INTAKE,
        WorkflowState.RECON,
    ]
    assert recon == recovered


def test_transition_ledger_redacts_prompt_and_document_text(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    created, _ = store.create(order())
    store.transition(
        created.workflow_id,
        WorkflowState.RECON,
        expected_state=WorkflowState.INTAKE,
        expected_version=1,
        payload={
            "promptBody": "replace every secret",
            "documentText": "resident registration number",
            "sourceHash": "sha256:known-safe",
        },
    )

    payload = store.events(created.workflow_id)[1].payload
    assert payload["promptBody"]["redacted"] is True
    assert payload["documentText"]["redacted"] is True
    assert payload["sourceHash"] == "sha256:known-safe"
    assert "replace every secret" not in str(payload)


def test_mid_transaction_failure_rolls_back_state_and_event(tmp_path):
    class FailingStore(WorkflowStore):
        def _append_event(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            super()._append_event(*args, **kwargs)
            if kwargs.get("event_type") == "workflow.transitioned":
                raise RuntimeError("simulated process failure before commit")

    path = tmp_path / "workflow.sqlite3"
    base = WorkflowStore(path)
    created, _ = base.create(order())

    with pytest.raises(RuntimeError, match="simulated process failure"):
        FailingStore(path).transition(
            created.workflow_id,
            WorkflowState.RECON,
            expected_state=WorkflowState.INTAKE,
            expected_version=1,
        )

    reopened = WorkflowStore(path)
    assert reopened.get(created.workflow_id).state == WorkflowState.INTAKE
    assert len(reopened.events(created.workflow_id)) == 1


def test_reopen_after_every_operational_state_transition_has_no_duplicate_event(tmp_path):
    path = tmp_path / "workflow.sqlite3"
    store = WorkflowStore(path)
    current, _ = store.create(order())
    route = [
        WorkflowState.RECON,
        WorkflowState.PLAN,
        WorkflowState.DECISION,
        WorkflowState.EXECUTE,
        WorkflowState.VERIFY,
        WorkflowState.REPAIR,
        WorkflowState.VERIFY,
        WorkflowState.COMPLETED,
    ]

    for target in route:
        previous = current
        current = WorkflowStore(path).transition(
            previous.workflow_id,
            target,
            expected_state=previous.state,
            expected_version=previous.state_version,
        )
        reopened = WorkflowStore(path)
        assert reopened.get(current.workflow_id) == current
        assert len(reopened.events(current.workflow_id)) == current.state_version

    assert current.terminal is True
    assert current.state == WorkflowState.COMPLETED


def test_stale_writer_and_invalid_jump_are_rejected(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    created, _ = store.create(order())

    with pytest.raises(WorkflowConflict, match="stale workflow state"):
        store.transition(
            created.workflow_id,
            WorkflowState.RECON,
            expected_state=WorkflowState.INTAKE,
            expected_version=9,
        )
    with pytest.raises(InvalidTransition, match="intake -> completed"):
        store.transition(
            created.workflow_id,
            WorkflowState.COMPLETED,
            expected_state=WorkflowState.INTAKE,
            expected_version=1,
        )


def test_event_chain_detects_tampering(tmp_path):
    path = tmp_path / "workflow.sqlite3"
    store = WorkflowStore(path)
    created, _ = store.create(order())
    store.transition(
        created.workflow_id,
        WorkflowState.RECON,
        expected_state=WorkflowState.INTAKE,
        expected_version=1,
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflow_events SET payload_json = ? WHERE workflow_id = ? AND event_index = 0",
            ('{"changed":true}', created.workflow_id),
        )

    with pytest.raises(WorkflowConflict, match="event chain is invalid"):
        store.events(created.workflow_id)


def test_same_state_receipt_is_cas_guarded_and_idempotent(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    created, _ = store.create(order())
    updated, first, appended = store.append_event(
        created.workflow_id,
        "action.planned",
        expected_state=created.state,
        expected_version=created.state_version,
        payload={"actionHash": "sha256:action"},
        event_key="action:one",
    )
    recovered, same, appended_again = store.append_event(
        created.workflow_id,
        "action.planned",
        expected_state=created.state,
        expected_version=created.state_version,
        payload={"actionHash": "sha256:action"},
        event_key="action:one",
    )

    assert appended is True
    assert appended_again is False
    assert same == first
    assert recovered == updated
    assert updated.state == created.state
    assert updated.state_version == created.state_version + 1
    assert len(store.events(created.workflow_id)) == updated.state_version

    with pytest.raises(WorkflowConflict, match="stale workflow state"):
        store.append_event(
            created.workflow_id,
            "action.planned",
            expected_state=created.state,
            expected_version=created.state_version,
            payload={"actionHash": "sha256:other"},
            event_key="action:two",
        )


def test_event_key_reuse_with_different_receipt_fails_closed(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    created, _ = store.create(order())
    store.append_event(
        created.workflow_id,
        "action.planned",
        expected_state=created.state,
        expected_version=created.state_version,
        payload={"actionHash": "sha256:one"},
        event_key="action:one",
    )
    with pytest.raises(WorkflowConflict, match="different receipt"):
        store.append_event(
            created.workflow_id,
            "action.planned",
            expected_state=created.state,
            expected_version=created.state_version,
            payload={"actionHash": "sha256:two"},
            event_key="action:one",
        )


def test_store_rejects_forged_or_transition_based_decision_receipts(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    record, _ = store.create(order())

    with pytest.raises(WorkflowConflict, match="same-state append_event"):
        store.transition(
            record.workflow_id,
            WorkflowState.RECON,
            expected_state=record.state,
            expected_version=record.state_version,
            event_type="decision.approved",
            payload={"actionHash": "sha256:action", "approved": True},
        )
    with pytest.raises(WorkflowConflict, match="only valid in decision state"):
        store.append_event(
            record.workflow_id,
            "decision.approved",
            expected_state=record.state,
            expected_version=record.state_version,
            payload={"actionHash": "sha256:action", "approved": True},
        )

    for target in (WorkflowState.RECON, WorkflowState.PLAN, WorkflowState.DECISION):
        record = store.transition(
            record.workflow_id,
            target,
            expected_state=record.state,
            expected_version=record.state_version,
        )
    with pytest.raises(WorkflowConflict, match="type and approved value disagree"):
        store.append_event(
            record.workflow_id,
            "decision.rejected",
            expected_state=record.state,
            expected_version=record.state_version,
            payload={"actionHash": "sha256:action", "approved": True},
        )


def test_binary_payload_and_original_overwrite_policy_are_rejected():
    with pytest.raises(ValidationError, match="document bytes"):
        order(parameters={"document": b"PK..."})
    with pytest.raises(ValidationError, match="preserve the original"):
        order(policy={"preserve_original": False})


def test_work_order_and_action_results_are_authenticated_and_not_plaintext(tmp_path):
    path = tmp_path / "workflow.sqlite3"
    secret = "900101-1234567"
    store = WorkflowStore(path, encryption_key=b"x" * 32)
    record, _ = store.create(order(parameters={"residentNumber": secret}))
    saved = store.put_action_result(
        record.workflow_id, "sha256:action", {"residentNumber": secret, "ok": True}
    )

    assert store.get(record.workflow_id).work_order.parameters["residentNumber"] == secret
    assert store.get_action_result(record.workflow_id, "sha256:action") == saved.result
    assert saved.result["residentNumber"] == secret
    assert secret.encode() not in path.read_bytes()
    assert b"request-0001" not in path.read_bytes()
    metadata = store.action_result_metadata(record.workflow_id, "sha256:action")
    assert metadata is not None
    assert metadata["contentHash"].startswith("sha256:")
    assert metadata["sizeBytes"] > 0
    assert "result" not in metadata


def test_action_result_tampering_fails_authentication(tmp_path):
    path = tmp_path / "workflow.sqlite3"
    store = WorkflowStore(path, encryption_key=b"y" * 32)
    record, _ = store.create(order())
    store.put_action_result(record.workflow_id, "sha256:action", {"value": "secret"})
    with sqlite3.connect(path) as connection:
        envelope = connection.execute(
            "SELECT result_ciphertext FROM workflow_action_results"
        ).fetchone()[0]
        replacement = envelope[:-1] + ("A" if envelope[-1] != "A" else "B")
        connection.execute(
            "UPDATE workflow_action_results SET result_ciphertext = ?", (replacement,)
        )

    with pytest.raises(WorkflowConflict, match="failed authentication"):
        store.get_action_result(record.workflow_id, "sha256:action")


def test_terminal_transition_scrubs_parameters_and_result_retention_is_bounded(tmp_path):
    path = tmp_path / "workflow.sqlite3"
    store = WorkflowStore(path, encryption_key=b"z" * 32, result_retention_seconds=60)
    current, _ = store.create(order(parameters={"residentNumber": "900101-1234567"}))
    store.put_action_result(current.workflow_id, "sha256:action", {"answer": 42})
    for target in (
        WorkflowState.RECON,
        WorkflowState.PLAN,
        WorkflowState.EXECUTE,
        WorkflowState.VERIFY,
        WorkflowState.COMPLETED,
    ):
        current = store.transition(
            current.workflow_id,
            target,
            expected_state=current.state,
            expected_version=current.state_version,
        )

    assert current.work_order.parameters == {}
    assert store.get(current.workflow_id).work_order.parameters == {}
    assert store.purge_expired_results(now=utc_now() + timedelta(seconds=61)) == 1
    with pytest.raises(KeyError):
        store.get_action_result(current.workflow_id, "sha256:action")
    with pytest.raises(ValueError, match="between 1 second and 30 days"):
        WorkflowStore(tmp_path / "too-long.sqlite3", result_retention_seconds=31 * 24 * 60 * 60)


def test_action_result_hash_reuse_with_different_content_fails_closed(tmp_path):
    store = WorkflowStore(tmp_path / "workflow.sqlite3", encryption_key=b"k" * 32)
    record, _ = store.create(order())
    first = store.put_action_result(record.workflow_id, "sha256:action", {"value": 1})
    repeated = store.put_action_result(record.workflow_id, "sha256:action", {"value": 1})
    assert repeated == first
    with pytest.raises(WorkflowConflict, match="different result"):
        store.put_action_result(record.workflow_id, "sha256:action", {"value": 2})


def test_legacy_plaintext_work_order_is_migrated_and_vacuumed(tmp_path):
    path = tmp_path / "workflow.sqlite3"
    key = b"m" * 32
    store = WorkflowStore(path, encryption_key=key)
    record, _ = store.create(order())
    legacy_secret = "legacy-900101-1234567"
    legacy_order = order(parameters={"residentNumber": legacy_secret}).model_dump_json()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE workflows SET work_order_json = ? WHERE workflow_id = ?",
            (legacy_order, record.workflow_id),
        )

    reopened = WorkflowStore(path, encryption_key=key)
    assert reopened.get(record.workflow_id).work_order.parameters["residentNumber"] == legacy_secret
    assert legacy_secret.encode() not in path.read_bytes()
