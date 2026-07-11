from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from hwpx_mcp_server.document_state import document_revision
from hwpx_mcp_server.workflow import (
    ActionRequest,
    AllowlistedDispatcher,
    PolicyViolation,
    WorkFamily,
    WorkOrder,
    WorkflowPolicyEngine,
    WorkflowState,
    WorkflowStore,
)


def make_record(tmp_path: Path, *, family: WorkFamily = WorkFamily.UNKNOWN_FORM_FILL, **changes):
    source = tmp_path / "source.hwpx"
    source.write_bytes(b"original")
    values = {
        "family": family,
        "source_path": str(source),
        "output_path": str(tmp_path / "copy.hwpx"),
        "expected_revision": document_revision(source),
        "idempotency_key": f"policy-{family.value}",
    }
    values.update(changes)
    store = WorkflowStore(tmp_path / f"{family.value}.sqlite3")
    record, _ = store.create(WorkOrder.model_validate(values))
    return store, record, source


def test_original_overwrite_is_forbidden(tmp_path):
    source = tmp_path / "same.hwpx"
    source.write_bytes(b"original")
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    record, _ = store.create(
        WorkOrder(
            family=WorkFamily.TRANSACTIONAL_EDIT,
            source_path=str(source),
            output_path=str(source),
            expected_revision=document_revision(source),
            idempotency_key="same-path",
        )
    )

    with pytest.raises(PolicyViolation) as caught:
        WorkflowPolicyEngine().validate_intake(record)
    assert caught.value.code == "ORIGINAL_OVERWRITE_FORBIDDEN"


def test_stale_revision_and_capability_skew_fail_closed(tmp_path):
    store, record, source = make_record(tmp_path)
    action = ActionRequest(tool_name="scan_form_guidance", arguments={"path": str(source)})
    source.write_bytes(b"external change")
    with pytest.raises(PolicyViolation) as stale:
        WorkflowPolicyEngine().authorize_action(record, action, store.events(record.workflow_id), capability_ok=True)
    assert stale.value.code == "STALE_DOCUMENT_REVISION"

    source.write_bytes(b"original")
    with pytest.raises(PolicyViolation) as skew:
        WorkflowPolicyEngine().authorize_action(record, action, store.events(record.workflow_id), capability_ok=False)
    assert skew.value.code == "CAPABILITY_SKEW"


def test_destructive_action_requires_matching_decision_receipt(tmp_path):
    store, record, source = make_record(tmp_path)
    action = ActionRequest(
        tool_name="apply_table_ops",
        arguments={"input_path": str(source), "output_path": record.work_order.output_path},
        destructive=True,
    )
    with pytest.raises(PolicyViolation) as decision:
        WorkflowPolicyEngine().authorize_action(record, action, store.events(record.workflow_id), capability_ok=True)
    assert decision.value.code == "DECISION_REQUIRED"

    engine = WorkflowPolicyEngine()
    for target in (WorkflowState.RECON, WorkflowState.PLAN, WorkflowState.DECISION):
        record = store.transition(
            record.workflow_id,
            target,
            expected_state=record.state,
            expected_version=record.state_version,
        )
    different = ActionRequest(tool_name="apply_body_ops", arguments={}, destructive=True)
    record = engine.record_decision(store, record, different, approved=True)
    with pytest.raises(PolicyViolation) as mismatch:
        engine.authorize_action(record, action, store.events(record.workflow_id), capability_ok=True)
    assert mismatch.value.code == "DECISION_REQUIRED"

    record = engine.record_decision(store, record, action, approved=True)
    engine.authorize_action(record, action, store.events(record.workflow_id), capability_ok=True)


def test_decision_receipt_is_rejected_outside_decision_state(tmp_path):
    store, record, source = make_record(tmp_path)
    action = ActionRequest(tool_name="apply_table_ops", arguments={"path": str(source)}, destructive=True)

    with pytest.raises(PolicyViolation) as caught:
        WorkflowPolicyEngine().record_decision(store, record, action, approved=True)
    assert caught.value.code == "DECISION_STATE_REQUIRED"


def test_unverified_or_unsafe_work_cannot_complete(tmp_path):
    store, record, _ = make_record(tmp_path, family=WorkFamily.TRANSACTIONAL_EDIT)
    for target in (WorkflowState.RECON, WorkflowState.PLAN, WorkflowState.EXECUTE, WorkflowState.VERIFY):
        record = store.transition(
            record.workflow_id,
            target,
            expected_state=record.state,
            expected_version=record.state_version,
        )

    engine = WorkflowPolicyEngine()
    with pytest.raises(PolicyViolation) as unsafe:
        engine.authorize_completion(record, {"verified": True, "openSafetyOk": False})
    assert unsafe.value.code == "OPEN_SAFETY_REQUIRED"
    with pytest.raises(PolicyViolation) as unverified:
        engine.authorize_completion(record, {"verified": False, "openSafetyOk": True})
    assert unverified.value.code == "VERIFIED_COMPLETION_REQUIRED"
    engine.authorize_completion(record, {"verified": True, "openSafetyOk": True})


def test_dispatcher_is_family_and_toolspec_allowlisted():
    calls = []

    def scan_form_guidance(**arguments):
        calls.append(arguments)
        return {"ok": True}

    dispatcher = AllowlistedDispatcher({"scan_form_guidance": scan_form_guidance})
    result = dispatcher.dispatch(
        WorkFamily.UNKNOWN_FORM_FILL,
        ActionRequest(tool_name="scan_form_guidance", arguments={"path": "/tmp/form.hwpx"}),
    )
    assert result == {"ok": True}
    assert calls == [{"path": "/tmp/form.hwpx"}]

    with pytest.raises(PolicyViolation) as blocked:
        dispatcher.resolve(
            WorkFamily.READ_EXTRACT,
            ActionRequest(tool_name="apply_table_ops", arguments={}),
        )
    assert blocked.value.code == "TOOL_NOT_ALLOWED"


def test_durable_dispatch_writes_receipts_and_completed_retry_never_executes_twice(tmp_path):
    calls = []

    def scan_form_guidance(**arguments):
        calls.append(arguments)
        return {"ok": True, "count": len(calls)}

    store, record, source = make_record(tmp_path)
    action = ActionRequest(tool_name="scan_form_guidance", arguments={"path": str(source)})
    dispatcher = AllowlistedDispatcher({"scan_form_guidance": scan_form_guidance})
    first = dispatcher.dispatch_durable(
        store, record.workflow_id, action, expected_version=record.state_version, capability_ok=True
    )
    current = store.get(record.workflow_id)
    replay = dispatcher.dispatch_durable(
        store, record.workflow_id, action, expected_version=current.state_version, capability_ok=True
    )

    assert first.result == {"ok": True, "count": 1}
    assert first.replayed is False
    assert replay.result is None
    assert replay.replayed is True
    assert calls == [{"path": str(source)}]
    events = store.events(record.workflow_id)
    assert [event.event_type for event in events[-2:]] == ["dispatch.started", "dispatch.completed"]
    assert events[-2].payload["argumentsHash"].startswith("sha256:")
    assert "path" not in str(events[-2].payload)


def test_started_without_terminal_receipt_is_in_doubt_and_never_reexecuted(tmp_path):
    calls = []
    store, record, source = make_record(tmp_path)
    action = ActionRequest(tool_name="scan_form_guidance", arguments={"path": str(source)})
    record, _, _ = store.append_event(
        record.workflow_id,
        "dispatch.started",
        expected_state=record.state,
        expected_version=record.state_version,
        payload={"actionHash": action.action_hash, "attempt": 1, "tool": action.tool_name},
        event_key=f"dispatch:{action.action_hash}:1:started",
    )
    dispatcher = AllowlistedDispatcher({"scan_form_guidance": lambda **kwargs: calls.append(kwargs)})

    with pytest.raises(PolicyViolation) as caught:
        dispatcher.dispatch_durable(
            store, record.workflow_id, action, expected_version=record.state_version, capability_ok=True
        )
    assert caught.value.code == "DISPATCH_IN_DOUBT"
    assert calls == []


def test_failed_dispatch_is_receipted_and_may_retry_within_budget(tmp_path):
    attempts = 0

    def flaky(**arguments):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("document content must not enter receipt")
        return {"ok": True}

    store, record, source = make_record(tmp_path)
    action = ActionRequest(tool_name="scan_form_guidance", arguments={"path": str(source)})
    dispatcher = AllowlistedDispatcher({"scan_form_guidance": flaky})
    with pytest.raises(RuntimeError):
        dispatcher.dispatch_durable(
            store, record.workflow_id, action, expected_version=record.state_version, capability_ok=True
        )
    record = store.get(record.workflow_id)
    outcome = dispatcher.dispatch_durable(
        store, record.workflow_id, action, expected_version=record.state_version, capability_ok=True
    )

    assert outcome.result == {"ok": True}
    assert attempts == 2
    events = store.events(record.workflow_id)
    assert [event.event_type for event in events[-4:]] == [
        "dispatch.started",
        "dispatch.failed",
        "dispatch.started",
        "dispatch.completed",
    ]
    assert "document content" not in str(events)


def test_tool_call_budget_blocks_retry_before_execution(tmp_path):
    calls = 0

    def failing(**arguments):
        nonlocal calls
        calls += 1
        raise RuntimeError("failed")

    store, record, source = make_record(
        tmp_path,
        budget={"max_tool_calls": 1, "max_attempts": 3},
    )
    action = ActionRequest(tool_name="scan_form_guidance", arguments={"path": str(source)})
    dispatcher = AllowlistedDispatcher({"scan_form_guidance": failing})
    with pytest.raises(RuntimeError):
        dispatcher.dispatch_durable(
            store, record.workflow_id, action, expected_version=record.state_version, capability_ok=True
        )
    record = store.get(record.workflow_id)
    with pytest.raises(PolicyViolation) as caught:
        dispatcher.dispatch_durable(
            store, record.workflow_id, action, expected_version=record.state_version, capability_ok=True
        )
    assert caught.value.code == "TOOL_CALL_BUDGET_EXCEEDED"
    assert calls == 1


def test_action_attempt_budget_blocks_retry_before_execution(tmp_path):
    calls = 0

    def failing(**arguments):
        nonlocal calls
        calls += 1
        raise RuntimeError("failed")

    store, record, source = make_record(
        tmp_path,
        budget={"max_tool_calls": 5, "max_attempts": 1},
    )
    action = ActionRequest(tool_name="scan_form_guidance", arguments={"path": str(source)})
    dispatcher = AllowlistedDispatcher({"scan_form_guidance": failing})
    with pytest.raises(RuntimeError):
        dispatcher.dispatch_durable(
            store, record.workflow_id, action, expected_version=record.state_version, capability_ok=True
        )
    record = store.get(record.workflow_id)
    with pytest.raises(PolicyViolation) as caught:
        dispatcher.dispatch_durable(
            store, record.workflow_id, action, expected_version=record.state_version, capability_ok=True
        )
    assert caught.value.code == "ACTION_ATTEMPT_BUDGET_EXCEEDED"
    assert calls == 1


def test_elapsed_time_budget_is_derived_from_durable_creation_time(tmp_path, monkeypatch):
    store, record, source = make_record(tmp_path, budget={"max_elapsed_seconds": 1})
    action = ActionRequest(tool_name="scan_form_guidance", arguments={"path": str(source)})
    monkeypatch.setattr(
        "hwpx_mcp_server.workflow.policy.utc_now",
        lambda: record.created_at + timedelta(seconds=2),
    )

    with pytest.raises(PolicyViolation) as caught:
        WorkflowPolicyEngine().authorize_action(
            record, action, store.events(record.workflow_id), capability_ok=True
        )
    assert caught.value.code == "ELAPSED_TIME_BUDGET_EXCEEDED"


def test_repair_round_budget_blocks_actions_after_excess_round(tmp_path):
    store, record, source = make_record(tmp_path, budget={"max_repair_rounds": 1})
    for target in (
        WorkflowState.RECON,
        WorkflowState.PLAN,
        WorkflowState.EXECUTE,
        WorkflowState.REPAIR,
        WorkflowState.VERIFY,
        WorkflowState.REPAIR,
    ):
        record = store.transition(
            record.workflow_id,
            target,
            expected_state=record.state,
            expected_version=record.state_version,
        )
    action = ActionRequest(tool_name="scan_form_guidance", arguments={"path": str(source)})

    with pytest.raises(PolicyViolation) as caught:
        WorkflowPolicyEngine().authorize_action(
            record, action, store.events(record.workflow_id), capability_ok=True
        )
    assert caught.value.code == "REPAIR_BUDGET_EXCEEDED"


def test_policy_gated_complete_persists_verification_receipt(tmp_path):
    store, record, _ = make_record(tmp_path, family=WorkFamily.TRANSACTIONAL_EDIT)
    for target in (WorkflowState.RECON, WorkflowState.PLAN, WorkflowState.EXECUTE, WorkflowState.VERIFY):
        record = store.transition(
            record.workflow_id,
            target,
            expected_state=record.state,
            expected_version=record.state_version,
        )
    completed = WorkflowPolicyEngine().complete(
        store,
        record,
        {"verified": True, "openSafetyOk": True},
        output_content_hash="sha256:output",
    )
    assert completed.state == WorkflowState.COMPLETED
    assert completed.output_content_hash == "sha256:output"
    assert store.events(record.workflow_id)[-1].event_type == "workflow.completed"
