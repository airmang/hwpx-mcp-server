from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from hwpx import HwpxDocument
from hwpx.tools.package_validator import validate_editor_open_safety

from hwpx_mcp_server.practice import PracticeScenarioError, PracticeScenarioService


SCENARIO_ID = "SCN-0123456789ABCDEFFEDC"
DERIVATIVE_ID = "DER-0123456789ABCDEFFEDC"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[PracticeScenarioService, Path, Path]:
    root = tmp_path / "private-practice"
    source = root / "sanitized" / f"{DERIVATIVE_ID}.hwpx"
    source.parent.mkdir(parents=True)
    document = HwpxDocument.new()
    document.add_paragraph("합성-연습양식")
    table = document.add_paragraph("").add_table(2, 2)
    table.set_cell_text(0, 0, "합성-항목")
    table.set_cell_text(1, 1, "")
    document.save_to_path(source)
    scenario = {
        "schema": "hwpx.practice-runner-manifest/v1",
        "runnerScenarioId": SCENARIO_ID,
        "taskKind": "unknown_form_fill",
        "family": "합성-양식",
        "difficulty": "routine",
        "instruction": "합성 값을 빈 셀에 채운다.",
        "syntheticInputs": {
            "synthetic": True,
            "fields": {"목적": "합성-문서편집연습"},
        },
        "controlledMutation": {"synthetic": True},
        "startArtifact": {"artifactId": DERIVATIVE_ID, "sha256": _sha(source)},
        "allowedWorkflow": "unknown_form_fill",
        "budgets": {"toolCalls": 12},
        "requiredOracles": ["open_safety", "form_mapping", "form_residue"],
        "suggestedOperations": [
            {"op": "fill_cell", "table_index": 0, "row": 1, "col": 1, "text": "합성-문서편집연습"}
        ],
    }
    manifest = root / "scenarios" / "runner.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {"schema": "hwpx.practice-runner-manifest/v1", "scenarios": [scenario]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def apply_table(filename, ops, *, output, render_check, dry_run):  # type: ignore[no-untyped-def]
        assert render_check == "off" and dry_run is False
        target = HwpxDocument.open(filename)
        table = next(table for paragraph in target.paragraphs for table in paragraph.tables)
        op = ops[0]
        table.set_cell_text(op["row"], op["col"], op["text"])
        target.save_to_path(output)
        return {"ok": True, "applied": [op], "openSafety": {"ok": True}}

    service = PracticeScenarioService(
        root,
        runner_manifest_path=manifest,
        apply_table_ops=apply_table,
        apply_body_ops=lambda *args, **kwargs: {"ok": False},
        inspect_fill_residue=lambda *args, **kwargs: {"ok": True, "remaining": 0},
    )
    return service, root, source


def test_start_is_idempotent_and_exposes_no_private_coordinate(tmp_path: Path) -> None:
    service, root, _source = _fixture(tmp_path)
    first = service.start(SCENARIO_ID, idempotency_key="practice-demo-001")
    replay = service.start(SCENARIO_ID, idempotency_key="practice-demo-001")

    assert first["state"] == "prepared"
    assert first["suggestedOperations"][0]["text"].startswith("합성-")
    assert first["privateStorageCoordinatesExposed"] is False
    assert str(root) not in json.dumps(first, ensure_ascii=False)
    assert replay["runId"] == first["runId"]
    assert replay["idempotentReplay"] is True


def test_apply_has_decision_gate_then_structural_unverified_receipt(tmp_path: Path) -> None:
    service, root, source = _fixture(tmp_path)
    run = service.start(SCENARIO_ID, idempotency_key="practice-demo-002")
    destination = tmp_path / "public-output" / "demo.hwpx"
    source_before = _sha(source)

    preview = service.apply(
        run["runId"],
        destination_path=destination,
        use_suggested_operations=True,
        confirm=False,
    )
    assert preview["state"] == "decision"
    assert not destination.exists()

    receipt = service.apply(
        run["runId"],
        destination_path=destination,
        use_suggested_operations=True,
        confirm=True,
    )
    assert receipt["state"] == "completed"
    assert receipt["sourceArtifact"]["unchanged"] is True
    assert receipt["openSafety"]["ok"] is True
    assert receipt["domainVerification"]["ok"] is True
    assert receipt["render"] == {"checked": False, "status": "unverified"}
    assert receipt["verificationStatus"] == "structurally_verified_render_unverified"
    assert str(root) not in json.dumps(receipt, ensure_ascii=False)
    assert _sha(source) == source_before
    assert validate_editor_open_safety(destination).ok
    reopened = HwpxDocument.open(destination)
    table = next(table for paragraph in reopened.paragraphs for table in paragraph.tables)
    assert table.cell(1, 1).text == "합성-문서편집연습"

    replay = service.apply(
        run["runId"],
        destination_path=destination,
        use_suggested_operations=True,
        confirm=True,
    )
    assert replay["idempotentReplay"] is True


def test_apply_rejects_private_destination_and_manifest_gold(tmp_path: Path) -> None:
    service, root, _source = _fixture(tmp_path)
    run = service.start(SCENARIO_ID, idempotency_key="practice-demo-003")
    with pytest.raises(PracticeScenarioError, match="outside private"):
        service.apply(
            run["runId"],
            destination_path=root / "runs" / "leak.hwpx",
            use_suggested_operations=True,
            confirm=True,
        )

    manifest = service.manifest_path
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["scenarios"][0]["gold"] = {"verifierId": "must-not-leak"}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PracticeScenarioError, match="evaluator-only"):
        service.start(SCENARIO_ID, idempotency_key="practice-demo-004")


def test_start_rejects_artifact_hash_tampering(tmp_path: Path) -> None:
    service, _root, source = _fixture(tmp_path)
    source.write_bytes(source.read_bytes() + b"tamper")
    with pytest.raises(PracticeScenarioError, match="hash mismatch"):
        service.start(SCENARIO_ID, idempotency_key="practice-demo-005")


def test_apply_redacts_internal_failure_and_removes_partial_output(tmp_path: Path) -> None:
    service, root, _source = _fixture(tmp_path)
    run = service.start(SCENARIO_ID, idempotency_key="practice-demo-006")
    destination = tmp_path / "public-output" / "failed.hwpx"

    def fail_with_private_path(*args, **kwargs):  # type: ignore[no-untyped-def]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"partial")
        raise RuntimeError(str(root / "secret-source.hwpx"))

    service._apply_table_ops = fail_with_private_path
    with pytest.raises(PracticeScenarioError) as captured:
        service.apply(
            run["runId"],
            destination_path=destination,
            use_suggested_operations=True,
            confirm=True,
        )

    assert str(captured.value) == "practice operation or verification failed"
    assert str(root) not in str(captured.value)
    assert not destination.exists()
