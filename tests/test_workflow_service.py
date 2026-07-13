from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hwpx_mcp_server.workflow import WorkFamily, WorkflowState, WorkflowStore
from hwpx_mcp_server.workflow.service import WorkflowService, default_workflow_store_path


RECEIPT_KEYS = {
    "artifacts",
    "decisions",
    "semanticDiff",
    "openSafety",
    "verificationStatus",
    "unresolvedFindings",
    "versions",
    "toolSpecHash",
    "stopReason",
}


def namespace(calls: list[str]):
    def read(name):
        def function(**arguments):
            calls.append(name)
            return {"ok": True, "filename": arguments.get("filename"), "payload": "durable-result"}

        return function

    def write(name):
        def function(**arguments):
            calls.append(name)
            source = arguments.get("source_filename") or arguments.get("filename")
            output = arguments.get("destination_filename") or arguments.get("output") or arguments.get("filename")
            if output and source and Path(source) != Path(output):
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, output)
            elif output:
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                Path(output).touch(exist_ok=True)
            return {
                "ok": True,
                "created": True,
                "openSafety": {"ok": True},
                "verificationReport": {"ok": True, "openSafety": {"ok": True}},
                "semanticDiff": {"changed": True, "operations": 1},
            }

        return function

    return {
        "get_document_info": read("get_document_info"),
        "analyze_template_formfit": read("analyze_template_formfit"),
        "scan_form_guidance": read("scan_form_guidance"),
        "validate_document_plan": lambda **arguments: calls.append("validate_document_plan") or {"ok": True},
        "apply_edits": write("apply_edits"),
        "apply_template_formfit": write("apply_template_formfit"),
        "apply_table_ops": write("apply_table_ops"),
        "create_document_from_plan": write("create_document_from_plan"),
        "doc_diff": lambda **arguments: calls.append("doc_diff") or {"changes": []},
        "inspect_fill_residue": lambda **arguments: calls.append("inspect_fill_residue") or {"ok": True, "errors": []},
        "verify_form_fill": lambda **arguments: calls.append("verify_form_fill") or {"ok": True, "renderChecked": False},
        "inspect_document_authoring_quality": lambda **arguments: calls.append("inspect_document_authoring_quality") or {"pass": True},
        "inspect_official_document_style": lambda **arguments: calls.append("inspect_official_document_style") or {"ok": True},
    }


def drive(service: WorkflowService, receipt: dict) -> dict:
    for _ in range(12):
        if receipt["state"] == "decision":
            receipt = service.approve_decision(receipt["workflowId"], approved=True)
        elif receipt["terminal"]:
            return receipt
        else:
            receipt = service.continue_workflow(receipt["workflowId"])
    raise AssertionError(f"workflow did not terminate: {receipt}")


@pytest.mark.parametrize(
    ("family", "parameters", "expected_tool"),
    [
        (WorkFamily.READ_EXTRACT, {"operation": "info"}, "get_document_info"),
        (WorkFamily.TRANSACTIONAL_EDIT, {"operations": []}, "apply_edits"),
        (
            WorkFamily.KNOWN_TEMPLATE_FILL,
            {"baseline": {"schema": "fixture"}, "content": {"name": "홍길동"}},
            "apply_template_formfit",
        ),
        (
            WorkFamily.UNKNOWN_FORM_FILL,
            {"operationKind": "table", "operations": []},
            "apply_table_ops",
        ),
        (
            WorkFamily.TYPED_AUTHORING,
            {"documentPlan": {"schemaVersion": "hwpx.document_plan.v2", "sections": []}},
            "create_document_from_plan",
        ),
    ],
)
def test_weak_client_drives_each_family_using_only_high_level_api(
    tmp_path: Path,
    family: WorkFamily,
    parameters: dict,
    expected_tool: str,
):
    calls: list[str] = []
    service = WorkflowService(namespace(calls), store=WorkflowStore(tmp_path / f"{family.value}.sqlite3"))
    source = tmp_path / f"{family.value}-source.hwpx"
    output = tmp_path / f"{family.value}-output.hwpx"
    if family != WorkFamily.TYPED_AUTHORING:
        source.write_bytes(b"synthetic HWPX fixture")
    receipt = service.start(
        family=family.value,
        idempotency_key=f"weak-client-{family.value}",
        source_path=str(source) if source.exists() else None,
        output_path=str(output) if family != WorkFamily.READ_EXTRACT else None,
        parameters=parameters,
    )

    terminal = drive(service, receipt)

    assert terminal["state"] == "completed"
    assert RECEIPT_KEYS <= set(terminal)
    assert terminal["verificationStatus"] in {
        "verified_read_only",
        "structurally_verified_render_unverified",
    }
    assert terminal["openSafety"]["renderChecked"] is False
    assert expected_tool in calls
    if family != WorkFamily.READ_EXTRACT:
        assert output.exists()
        assert terminal["decisions"][0]["status"] == "approved"
        assert terminal["domainVerification"]["ok"] is True
    else:
        assert terminal["result"]["payload"] == "durable-result"


def test_transactional_receipt_preserves_real_semantic_diff(tmp_path: Path):
    calls: list[str] = []
    source = tmp_path / "source.hwpx"
    output = tmp_path / "output.hwpx"
    source.write_bytes(b"fixture")
    service = WorkflowService(namespace(calls), store=WorkflowStore(tmp_path / "workflow.sqlite3"))
    terminal = drive(
        service,
        service.start(
            family="transactional_edit",
            idempotency_key="semantic-diff-result",
            source_path=str(source),
            output_path=str(output),
            parameters={"operations": [{"op": "replace_text", "find": "a", "replace": "b"}]},
        ),
    )

    assert terminal["state"] == "completed"
    assert terminal["semanticDiff"] == {
        "changed": True,
        "operations": 1,
        "status": "computed",
        "available": True,
    }
    assert terminal["result"]["semanticDiff"]["changed"] is True
    fetched = service.workflow_result(terminal["workflowId"])
    assert fetched["contentHash"].startswith("sha256:")
    assert fetched["result"] == terminal["result"]


def test_failed_family_verifier_never_completes(tmp_path: Path):
    calls: list[str] = []
    source = tmp_path / "blank.hwpx"
    output = tmp_path / "filled.hwpx"
    source.write_bytes(b"fixture")
    tools = namespace(calls)
    tools["inspect_fill_residue"] = lambda **arguments: {"ok": False, "errors": [{"kind": "placeholder"}]}
    service = WorkflowService(tools, store=WorkflowStore(tmp_path / "workflow.sqlite3"))
    terminal = drive(
        service,
        service.start(
            family="unknown_form_fill",
            idempotency_key="domain-verifier-failure",
            source_path=str(source),
            output_path=str(output),
            parameters={"operationKind": "table", "operations": []},
        ),
    )

    assert terminal["state"] == "needs_review"
    assert terminal["stopReason"] == "VERIFICATION_EVIDENCE_REQUIRED"
    assert terminal["domainVerification"]["ok"] is False


def test_missing_family_verifier_abstains_instead_of_completing(tmp_path: Path):
    calls: list[str] = []
    source = tmp_path / "blank.hwpx"
    output = tmp_path / "filled.hwpx"
    source.write_bytes(b"fixture")
    tools = namespace(calls)
    del tools["verify_form_fill"]
    service = WorkflowService(tools, store=WorkflowStore(tmp_path / "workflow.sqlite3"))
    terminal = drive(
        service,
        service.start(
            family="known_template_fill",
            idempotency_key="missing-domain-verifier",
            source_path=str(source),
            output_path=str(output),
            parameters={"baseline": {"schema": "fixture"}, "content": {"name": "홍길동"}},
        ),
    )

    assert terminal["state"] == "needs_review"
    assert terminal["stopReason"] == "TOOL_UNAVAILABLE"
    assert terminal["domainVerification"]["complete"] is False


def test_unsupported_intent_and_incomplete_adapter_abstain_honestly(tmp_path):
    service = WorkflowService(namespace([]), store=WorkflowStore(tmp_path / "workflow.sqlite3"))
    unsupported = service.start(family="make_magic", idempotency_key="unsupported-intent")
    assert unsupported["state"] == "needs_review"
    assert unsupported["stopReason"] == "UNSUPPORTED_INTENT"
    assert unsupported["workflowId"] is None

    output = tmp_path / "output.hwpx"
    receipt = service.start(
        family=WorkFamily.TYPED_AUTHORING.value,
        idempotency_key="missing-plan",
        output_path=str(output),
        parameters={},
    )
    receipt = service.continue_workflow(receipt["workflowId"])
    receipt = service.continue_workflow(receipt["workflowId"])
    assert receipt["state"] == "needs_review"
    assert receipt["stopReason"] == "DOCUMENT_PLAN_REQUIRED"


def test_store_default_is_env_controlled_and_never_uses_cwd(monkeypatch, tmp_path):
    configured = tmp_path / "state" / "workflow.sqlite3"
    monkeypatch.setenv("HWPX_WORKFLOW_STORE", str(configured))
    assert default_workflow_store_path() == configured.resolve()

    monkeypatch.delenv("HWPX_WORKFLOW_STORE")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert default_workflow_store_path() == (tmp_path / "xdg" / "hwpx-mcp-server" / "workflows.sqlite3")


def test_get_cancel_and_resume_use_the_same_durable_record(tmp_path):
    calls: list[str] = []
    source = tmp_path / "source.hwpx"
    source.write_bytes(b"fixture")
    service = WorkflowService(namespace(calls), store=WorkflowStore(tmp_path / "workflow.sqlite3"))
    started = service.start(
        family=WorkFamily.READ_EXTRACT.value,
        idempotency_key="get-cancel-resume",
        source_path=str(source),
        parameters={"operation": "info"},
    )
    assert service.get(started["workflowId"])["state"] == "intake"
    resumed = service.resume(started["workflowId"])
    assert resumed["state"] == "recon"
    cancelled = service.cancel(started["workflowId"], reason="USER_STOPPED")
    assert cancelled["state"] == "cancelled"
    assert cancelled["stopReason"] == "USER_STOPPED"
    assert service.resume(started["workflowId"]) == cancelled


@pytest.mark.parametrize("target", [WorkflowState.EXECUTE, WorkflowState.VERIFY, WorkflowState.REPAIR])
def test_cancel_is_available_from_every_nonterminal_operational_state(tmp_path, target):
    calls: list[str] = []
    source = tmp_path / f"{target.value}.hwpx"
    source.write_bytes(b"fixture")
    service = WorkflowService(namespace(calls), store=WorkflowStore(tmp_path / f"{target.value}.sqlite3"))
    receipt = service.start(
        family=WorkFamily.READ_EXTRACT.value,
        idempotency_key=f"cancel-{target.value}",
        source_path=str(source),
        parameters={"operation": "info"},
    )
    record = service.store.get(receipt["workflowId"])
    route = {
        WorkflowState.EXECUTE: [WorkflowState.RECON, WorkflowState.PLAN, WorkflowState.EXECUTE],
        WorkflowState.VERIFY: [WorkflowState.RECON, WorkflowState.PLAN, WorkflowState.EXECUTE, WorkflowState.VERIFY],
        WorkflowState.REPAIR: [WorkflowState.RECON, WorkflowState.PLAN, WorkflowState.EXECUTE, WorkflowState.REPAIR],
    }[target]
    for state in route:
        record = service.store.transition(
            record.workflow_id,
            state,
            expected_state=record.state,
            expected_version=record.state_version,
        )

    cancelled = service.cancel(record.workflow_id, reason="USER_STOPPED")
    assert cancelled["state"] == "cancelled"
    assert cancelled["stopReason"] == "USER_STOPPED"


def test_nested_generation_verification_open_safety_completes(tmp_path):
    calls: list[str] = []
    output = tmp_path / "authored.hwpx"
    tools = namespace(calls)

    def create_with_nested_verification(**arguments):
        Path(arguments["filename"]).write_bytes(b"authored")
        return {"created": True, "verification": {"ok": True, "openSafety": {"ok": True}}}

    tools["create_document_from_plan"] = create_with_nested_verification
    service = WorkflowService(tools, store=WorkflowStore(tmp_path / "authoring.sqlite3"))
    receipt = service.start(
        family=WorkFamily.TYPED_AUTHORING.value,
        idempotency_key="nested-verification",
        output_path=str(output),
        parameters={"documentPlan": {"schemaVersion": "hwpx.document_plan.v2", "sections": []}},
    )

    terminal = drive(service, receipt)
    assert terminal["state"] == "completed"
    assert terminal["openSafety"]["ok"] is True


def test_real_server_namespace_read_workflow_reaches_verified_completion(tmp_path, monkeypatch):
    from hwpx_mcp_server import server

    monkeypatch.setenv("HWPX_WORKFLOW_STORE", str(tmp_path / "server-workflows.sqlite3"))
    receipt = server.start_workflow(
        family="read_extract",
        idempotency_key="real-server-read",
        source_path=str(Path(__file__).with_name("sample.hwpx")),
        parameters={"operation": "info"},
    )
    for _ in range(8):
        if receipt["terminal"]:
            break
        receipt = server.continue_workflow(receipt["workflowId"])

    assert receipt["state"] == "completed"
    assert receipt["verificationStatus"] == "verified_read_only"
    assert receipt["stopReason"] == "VERIFIED_COMPLETION"
