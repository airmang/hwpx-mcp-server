from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from hwpx_mcp_server.document_state import document_revision
from hwpx_mcp_server.tool_contract import expected_tool_names
from hwpx_mcp_server.workflow import (
    ActionRequest,
    PolicyViolation,
    WorkFamily,
    WorkOrder,
    WorkflowPolicyEngine,
    WorkflowState,
    WorkflowStore,
)
from hwpx_mcp_server.workflow.service import WorkflowService


ROOT = Path(__file__).resolve().parents[1]
CRASH_CODE = 73


def _child_env(**values: Path | str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env.update({key: str(value) for key, value in values.items()})
    return env


def _crash_after(code: str, *, env: dict[str, str], expected: int = CRASH_CODE) -> None:
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=False)
    assert result.returncode == expected


def _query_state_in_fresh_service(db: Path, workflow_id: str) -> str:
    code = """
import json, os
from hwpx_mcp_server.workflow import WorkflowStore
from hwpx_mcp_server.workflow.service import WorkflowService
service = WorkflowService({}, store=WorkflowStore(os.environ['DB']))
print(json.dumps({'state': service.get(os.environ['WF'])['state']}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=_child_env(DB=db, WF=workflow_id),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)["state"]


def test_process_exit_after_every_committed_service_state_resumes_from_same_sqlite(tmp_path):
    db = tmp_path / "state.sqlite3"
    source = tmp_path / "source.hwpx"
    source.write_bytes(b"read workflow fixture")
    start_code = """
import os
from hwpx_mcp_server.workflow import WorkflowStore
from hwpx_mcp_server.workflow.service import WorkflowService
service = WorkflowService({'get_document_info': lambda **kwargs: {'ok': True}}, store=WorkflowStore(os.environ['DB']))
service.start(family='read_extract', idempotency_key='process-boundary-read', source_path=os.environ['SOURCE'], parameters={'operation': 'info'})
os._exit(73)
"""
    continue_code = """
import os
from hwpx_mcp_server.workflow import WorkflowStore
from hwpx_mcp_server.workflow.service import WorkflowService
service = WorkflowService({'get_document_info': lambda **kwargs: {'ok': True}}, store=WorkflowStore(os.environ['DB']))
service.continue_workflow(os.environ['WF'])
os._exit(73)
"""

    _crash_after(start_code, env=_child_env(DB=db, SOURCE=source))
    with sqlite3.connect(db) as connection:
        workflow_id = str(connection.execute("SELECT workflow_id FROM workflows").fetchone()[0])
    assert _query_state_in_fresh_service(db, workflow_id) == "intake"

    for expected_state in ("recon", "plan", "execute", "verify", "completed"):
        _crash_after(continue_code, env=_child_env(DB=db, WF=workflow_id))
        assert _query_state_in_fresh_service(db, workflow_id) == expected_state

    store = WorkflowStore(db)
    record = store.get(workflow_id)
    assert record.state == WorkflowState.COMPLETED
    assert len(store.events(workflow_id)) == record.state_version
    assert sum(event.event_type == "dispatch.started" for event in store.events(workflow_id)) == 1


def test_mutation_then_process_exit_is_in_doubt_and_never_runs_twice(tmp_path):
    db = tmp_path / "mutation.sqlite3"
    source = tmp_path / "source.hwpx"
    output = tmp_path / "output.hwpx"
    counter = tmp_path / "counter.txt"
    source.write_bytes(b"blank")
    calls: list[str] = []

    def scan(**arguments):
        calls.append("scan")
        return {"ok": True}

    service = WorkflowService(
        {"scan_form_guidance": scan, "apply_table_ops": lambda **kwargs: None},
        store=WorkflowStore(db),
    )
    receipt = service.start(
        family=WorkFamily.UNKNOWN_FORM_FILL.value,
        idempotency_key="mutation-crash",
        source_path=str(source),
        output_path=str(output),
        parameters={"operationKind": "table", "operations": []},
    )
    while receipt["state"] != "decision":
        receipt = service.continue_workflow(receipt["workflowId"])
    receipt = service.approve_decision(receipt["workflowId"], approved=True)
    assert receipt["state"] == "execute"

    mutate_then_exit = """
import os
from pathlib import Path
from hwpx_mcp_server.workflow import WorkflowStore
from hwpx_mcp_server.workflow.service import WorkflowService
def scan(**kwargs): return {'ok': True}
def mutate(**kwargs):
    counter = Path(os.environ['COUNTER'])
    value = int(counter.read_text() or '0') if counter.exists() else 0
    counter.write_text(str(value + 1))
    output = Path(os.environ['OUTPUT'])
    output.write_bytes(output.read_bytes() + b'X' if output.exists() else b'X')
    os._exit(79)
service = WorkflowService({'scan_form_guidance': scan, 'apply_table_ops': mutate}, store=WorkflowStore(os.environ['DB']))
service.continue_workflow(os.environ['WF'])
"""
    _crash_after(
        mutate_then_exit,
        env=_child_env(DB=db, WF=receipt["workflowId"], COUNTER=counter, OUTPUT=output),
        expected=79,
    )
    events = WorkflowStore(db).events(receipt["workflowId"])
    assert events[-1].event_type == "dispatch.started"
    assert counter.read_text() == "1"
    assert output.read_bytes() == b"X"

    rerun_calls = 0

    def must_not_run(**arguments):
        nonlocal rerun_calls
        rerun_calls += 1
        output.write_bytes(output.read_bytes() + b"X")
        return {"ok": True, "openSafety": {"ok": True}}

    recovered = WorkflowService(
        {"scan_form_guidance": scan, "apply_table_ops": must_not_run},
        store=WorkflowStore(db),
    )
    resumed = recovered.resume(receipt["workflowId"])
    assert resumed["state"] == "needs_review"
    assert resumed["stopReason"] == "DISPATCH_IN_DOUBT"
    assert recovered.resume(receipt["workflowId"]) == resumed
    assert rerun_calls == 0
    assert counter.read_text() == "1"
    assert output.read_bytes() == b"X"


def test_process_exit_inside_sqlite_transition_rolls_back_update_and_event(tmp_path):
    db = tmp_path / "rollback.sqlite3"
    store = WorkflowStore(db)
    record, _ = store.create(
        WorkOrder(
            family=WorkFamily.READ_EXTRACT,
            source_path=str(tmp_path / "source.hwpx"),
            idempotency_key="rollback-process-exit",
        )
    )
    crash_inside_transaction = """
import os
from hwpx_mcp_server.workflow import WorkflowState, WorkflowStore
class CrashStore(WorkflowStore):
    def _append_event(self, *args, **kwargs):
        if kwargs.get('event_type') == 'workflow.transitioned':
            os._exit(81)
        return super()._append_event(*args, **kwargs)
store = CrashStore(os.environ['DB'])
record = store.get(os.environ['WF'])
store.transition(record.workflow_id, WorkflowState.RECON, expected_state=record.state, expected_version=record.state_version)
"""
    _crash_after(crash_inside_transaction, env=_child_env(DB=db, WF=record.workflow_id), expected=81)

    reopened = WorkflowStore(db)
    assert reopened.get(record.workflow_id).state == WorkflowState.INTAKE
    assert reopened.get(record.workflow_id).state_version == 1
    assert [event.event_type for event in reopened.events(record.workflow_id)] == ["workflow.created"]


def _record(tmp_path: Path, name: str, **changes):
    root = tmp_path / name
    root.mkdir()
    source = root / "source.hwpx"
    source.write_bytes(b"original")
    values = {
        "family": WorkFamily.UNKNOWN_FORM_FILL,
        "source_path": str(source),
        "output_path": str(root / "output.hwpx"),
        "expected_revision": document_revision(source),
        "idempotency_key": f"negative-{name}",
    }
    values.update(changes)
    store = WorkflowStore(root / "workflow.sqlite3")
    record, _ = store.create(WorkOrder.model_validate(values))
    return store, record, source


def test_policy_bypass_negative_matrix_reports_exact_fail_closed_codes(tmp_path, monkeypatch):
    report: dict[str, str] = {}

    def capture(label: str, operation) -> None:
        with pytest.raises(PolicyViolation) as caught:
            operation()
        report[label] = caught.value.code

    store, record, source = _record(tmp_path, "decision")
    destructive = ActionRequest("apply_table_ops", {"filename": str(source)}, destructive=True)
    capture(
        "decisionBypass",
        lambda: WorkflowPolicyEngine().authorize_action(
            record, destructive, store.events(record.workflow_id), capability_ok=True
        ),
    )

    overwrite_store, overwrite_record, _ = _record(tmp_path, "overwrite")
    overwrite_record = overwrite_record.model_copy(
        update={"work_order": overwrite_record.work_order.model_copy(update={"output_path": overwrite_record.work_order.source_path})}
    )
    capture("originalOverwrite", lambda: WorkflowPolicyEngine().validate_intake(overwrite_record))

    stale_store, stale_record, stale_source = _record(tmp_path, "stale")
    stale_source.write_bytes(b"changed")
    read_action = ActionRequest("scan_form_guidance", {"filename": str(stale_source)})
    capture(
        "staleRevision",
        lambda: WorkflowPolicyEngine().authorize_action(
            stale_record, read_action, stale_store.events(stale_record.workflow_id), capability_ok=True
        ),
    )

    tool_store, tool_record, tool_source = _record(
        tmp_path, "tool-budget", budget={"max_tool_calls": 1, "max_attempts": 3}
    )
    tool_action = ActionRequest("scan_form_guidance", {"filename": str(tool_source)})
    tool_record, _, _ = tool_store.append_event(
        tool_record.workflow_id,
        "dispatch.started",
        expected_state=tool_record.state,
        expected_version=tool_record.state_version,
        payload={"actionHash": tool_action.action_hash, "attempt": 1},
    )
    capture(
        "toolBudget",
        lambda: WorkflowPolicyEngine().authorize_action(
            tool_record, tool_action, tool_store.events(tool_record.workflow_id), capability_ok=True
        ),
    )

    attempt_store, attempt_record, attempt_source = _record(
        tmp_path, "attempt-budget", budget={"max_tool_calls": 5, "max_attempts": 1}
    )
    attempt_action = ActionRequest("scan_form_guidance", {"filename": str(attempt_source)})
    attempt_record, _, _ = attempt_store.append_event(
        attempt_record.workflow_id,
        "dispatch.started",
        expected_state=attempt_record.state,
        expected_version=attempt_record.state_version,
        payload={"actionHash": attempt_action.action_hash, "attempt": 1},
    )
    capture(
        "attemptBudget",
        lambda: WorkflowPolicyEngine().authorize_action(
            attempt_record,
            attempt_action,
            attempt_store.events(attempt_record.workflow_id),
            capability_ok=True,
        ),
    )

    elapsed_store, elapsed_record, elapsed_source = _record(
        tmp_path, "elapsed-budget", budget={"max_elapsed_seconds": 1}
    )
    elapsed_action = ActionRequest("scan_form_guidance", {"filename": str(elapsed_source)})
    monkeypatch.setattr(
        "hwpx_mcp_server.workflow.policy.utc_now",
        lambda: elapsed_record.created_at + timedelta(seconds=2),
    )
    capture(
        "elapsedBudget",
        lambda: WorkflowPolicyEngine().authorize_action(
            elapsed_record,
            elapsed_action,
            elapsed_store.events(elapsed_record.workflow_id),
            capability_ok=True,
        ),
    )
    monkeypatch.undo()

    repair_store, repair_record, repair_source = _record(
        tmp_path, "repair-budget", budget={"max_repair_rounds": 1}
    )
    for target in (
        WorkflowState.RECON,
        WorkflowState.PLAN,
        WorkflowState.EXECUTE,
        WorkflowState.REPAIR,
        WorkflowState.VERIFY,
        WorkflowState.REPAIR,
    ):
        repair_record = repair_store.transition(
            repair_record.workflow_id,
            target,
            expected_state=repair_record.state,
            expected_version=repair_record.state_version,
        )
    repair_action = ActionRequest("scan_form_guidance", {"filename": str(repair_source)})
    capture(
        "repairBudget",
        lambda: WorkflowPolicyEngine().authorize_action(
            repair_record,
            repair_action,
            repair_store.events(repair_record.workflow_id),
            capability_ok=True,
        ),
    )

    capability_store, capability_record, capability_source = _record(tmp_path, "capability")
    capability_action = ActionRequest("scan_form_guidance", {"filename": str(capability_source)})
    capture(
        "capabilitySkew",
        lambda: WorkflowPolicyEngine().authorize_action(
            capability_record,
            capability_action,
            capability_store.events(capability_record.workflow_id),
            capability_ok=False,
        ),
    )

    completion_store, completion_record, _ = _record(tmp_path, "completion")
    for target in (WorkflowState.RECON, WorkflowState.PLAN, WorkflowState.EXECUTE, WorkflowState.VERIFY):
        completion_record = completion_store.transition(
            completion_record.workflow_id,
            target,
            expected_state=completion_record.state,
            expected_version=completion_record.state_version,
        )
    capture(
        "unverifiedCompletion",
        lambda: WorkflowPolicyEngine().authorize_completion(
            completion_record, {"verified": False, "openSafetyOk": True}
        ),
    )

    assert report == {
        "decisionBypass": "DECISION_REQUIRED",
        "originalOverwrite": "ORIGINAL_OVERWRITE_FORBIDDEN",
        "staleRevision": "STALE_DOCUMENT_REVISION",
        "toolBudget": "TOOL_CALL_BUDGET_EXCEEDED",
        "attemptBudget": "ACTION_ATTEMPT_BUDGET_EXCEEDED",
        "elapsedBudget": "ELAPSED_TIME_BUDGET_EXCEEDED",
        "repairBudget": "REPAIR_BUDGET_EXCEEDED",
        "capabilitySkew": "CAPABILITY_SKEW",
        "unverifiedCompletion": "VERIFIED_COMPLETION_REQUIRED",
    }


def test_default_toolspec_regression_remains_exactly_128():
    from hwpx_mcp_server import server

    expected = expected_tool_names(advanced=False)
    assert len(expected) == 128
    assert set(server._fastmcp_tool_names()) == expected
