# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import ast
import copy
import hashlib
import zipfile
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import TypeAdapter, ValidationError

import hwpx_mcp_server.mixed_form as mixed_form_adapter
import hwpx_mcp_server.server as server
from hwpx import HwpxDocument, validate_editor_open_safety
from hwpx.agent import AgentContractError, HwpxAgentDocument
from hwpx.oxml.namespaces import HP
from hwpx_mcp_server.tool_contract import bound_tool_registry
from hwpx_mcp_server.mixed_form import (
    FORM_VERIFICATION_RECEIPT_SCHEMA,
    MixedFormPlanInput,
)


def _append(parent: Any, tag: str, attrs: dict[str, str] | None = None) -> Any:
    child = parent.makeelement(tag, attrs or {})
    parent.append(child)
    return child


def _add_native_field(document: HwpxDocument, *, field_id: str = "240021") -> None:
    paragraph = document.add_paragraph("사업명: ")
    paragraph.element.set("id", str(int(field_id) - 1))
    begin_run = _append(paragraph.element, f"{HP}run", {"charPrIDRef": "0"})
    control = _append(begin_run, f"{HP}ctrl", {"type": "FORM", "id": "ctrl-" + field_id})
    field = _append(
        control,
        f"{HP}fieldBegin",
        {
            "id": field_id,
            "fieldid": field_id,
            "type": "ClickHere",
            "name": "사업명",
            "prompt": "사업명",
            "editable": "true",
        },
    )
    params = _append(field, f"{HP}parameters", {"count": "2"})
    _append(params, f"{HP}stringParam", {"name": "FieldName"}).text = "사업명"
    _append(params, f"{HP}stringParam", {"name": "Instruction"}).text = "사업명"
    value_run = _append(paragraph.element, f"{HP}run", {"charPrIDRef": "0"})
    _append(value_run, f"{HP}t").text = "여기를 누르세요"
    end_run = _append(paragraph.element, f"{HP}run", {"charPrIDRef": "0"})
    end_control = _append(end_run, f"{HP}ctrl")
    _append(end_control, f"{HP}fieldEnd", {"beginIDRef": field_id, "fieldid": field_id})
    paragraph.section.mark_dirty()


def _build_fixture(
    path: Path,
    *,
    split_body_anchor: bool = False,
    duplicate_body_anchor: bool = False,
    duplicate_native_field: bool = False,
) -> None:
    with HwpxDocument.new() as document:
        title = document.sections[0].paragraphs[0]
        title.element.set("id", "240010")
        title.text = "S-079 혼합 양식 기준 문서"
        _add_native_field(document)
        if duplicate_native_field:
            _add_native_field(document, field_id="240023")

        table_paragraph = document.add_paragraph("담당 부서")
        table_paragraph.element.set("id", "240030")
        table = table_paragraph.add_table(2, 2)
        table.element.set("id", "240031")
        table.rows[0].cells[0].text = "사업명"
        table.rows[0].cells[1].text = ""
        table.rows[1].cells[0].text = "담당 부서"
        table.rows[1].cells[1].text = ""

        body = document.add_paragraph("", include_run=False)
        body.element.set("id", "240040")
        if split_body_anchor:
            body.add_run("담당자: {{담")
            body.add_run("당자}}")
        else:
            body.add_run("담당자: {{담당자}}")
        if duplicate_body_anchor:
            document.add_paragraph("비상 담당자: {{담당자}}")

        purpose = document.add_paragraph("행사 목적: 여기를 입력하세요")
        purpose.element.set("id", "240050")
        document.save_to_path(path)


def _revision(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _plan(
    source: Path,
    output: Path,
    *,
    dry_run: bool = False,
    key: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": "hwpx.mixed-form-plan/v1",
        "source": str(source),
        "output": str(output),
        "expectedRevision": _revision(source),
        "idempotencyKey": key,
        "dryRun": dry_run,
        "overwrite": True,
        "quality": "transparent",
        "verificationRequirements": [
            "package",
            "reopen",
            "openSafety",
            "semanticDiff",
            "bytePreservation",
        ],
        "operations": [
            {
                "operationId": "native-project",
                "target": {"kind": "nativeField", "fieldId": "240021"},
                "value": "AI 수업 나눔의 날",
            },
            {
                "operationId": "label-department",
                "target": {
                    "kind": "labelCell",
                    "sectionPath": "/section[1]",
                    "tableAnchor": "담당 부서",
                    "cellAnchor": {"label": "담당 부서", "direction": "right"},
                },
                "value": "교육연구부",
            },
            {
                "operationId": "canonical-purpose",
                "target": {
                    "kind": "canonicalPath",
                    "path": '/section[1]/paragraph[@id="240050"]',
                },
                "value": "행사 목적: 교내 AI 활용 사례 공유",
            },
            {
                "operationId": "body-owner",
                "target": {
                    "kind": "bodyAnchor",
                    "sectionPath": "/section[1]",
                    "anchor": "{{담당자}}",
                    "expectedCount": 1,
                },
                "value": "김서현",
            },
        ],
    }


def _single(plan: dict[str, Any], operation_id: str) -> dict[str, Any]:
    selected = copy.deepcopy(plan)
    selected["operations"] = [
        operation for operation in selected["operations"] if operation["operationId"] == operation_id
    ]
    return selected


def test_tool_schemas_are_discriminated_and_closed() -> None:
    bound = bound_tool_registry().by_name()
    analyze = bound["analyze_form_fill"].input_schema
    apply = bound["apply_form_fill"].input_schema
    assert analyze["additionalProperties"] is False
    assert apply["additionalProperties"] is False
    assert analyze["properties"]["plan"]["$ref"].endswith("MixedFormPlanInput")
    assert apply["properties"]["plan"]["discriminator"]["propertyName"] == "schemaVersion"
    assert set(apply["properties"]["plan"]["discriminator"]["mapping"]) == {
        "hwpx.mixed-form-plan/v1",
        "hwpx.mixed-form-compiled-plan/v1",
    }
    defs = apply["$defs"]
    for name in (
        "MixedFormPlanInput",
        "MixedFormCompiledPlanInput",
        "NativeFieldTarget",
        "LabelCellTarget",
        "CanonicalPathTarget",
        "BodyAnchorTarget",
    ):
        assert defs[name]["additionalProperties"] is False
    target = defs["MixedFormOperation"]["properties"]["target"]
    assert target["discriminator"]["propertyName"] == "kind"
    assert set(target["discriminator"]["mapping"]) == {
        "nativeField",
        "labelCell",
        "canonicalPath",
        "bodyAnchor",
    }
    assert len(defs["NativeFieldTarget"]["oneOf"]) == 2
    assert len(defs["LabelCellTarget"]["oneOf"]) == 2


def test_strict_plan_rejects_unknown_keys_and_selector_xor(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    output = tmp_path / "output.hwpx"
    _build_fixture(source)
    adapter = TypeAdapter(MixedFormPlanInput)
    schema = adapter.json_schema(by_alias=True)
    validator = Draft202012Validator(schema)

    unknown = _plan(source, output)
    unknown["unexpected"] = True
    assert list(validator.iter_errors(unknown))
    with pytest.raises(ValidationError):
        server.analyze_form_fill(plan=unknown)

    missing_native = _single(_plan(source, output), "native-project")
    missing_native["operations"][0]["target"] = {"kind": "nativeField"}
    assert list(validator.iter_errors(missing_native))
    with pytest.raises(ValidationError):
        server.analyze_form_fill(plan=missing_native)

    both_native = _single(_plan(source, output), "native-project")
    both_native["operations"][0]["target"] = {
        "kind": "nativeField",
        "fieldId": "240021",
        "name": "사업명",
    }
    assert list(validator.iter_errors(both_native))
    with pytest.raises(ValidationError):
        server.analyze_form_fill(plan=both_native)

    both_table = _single(_plan(source, output), "label-department")
    both_table["operations"][0]["target"]["tableIndex"] = 0
    assert list(validator.iter_errors(both_table))
    with pytest.raises(ValidationError):
        server.analyze_form_fill(plan=both_table)

    with pytest.raises(ValueError, match="cannot be combined"):
        server.analyze_form_fill(source_filename=str(source), plan=_plan(source, output))


def test_analyze_and_apply_four_targets_with_receipt_and_member_preservation(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    output = tmp_path / "output.hwpx"
    _build_fixture(source)
    source_before = source.read_bytes()

    analysis = server.analyze_form_fill(plan=_plan(source, output, key="mcp-four-targets"))
    assert analysis["schemaVersion"] == "hwpx.mixed-form-analysis/v1"
    assert analysis["mutated"] is False
    assert analysis["openSafety"]["ok"] is True
    assert source.read_bytes() == source_before
    assert not output.exists()
    compiled = analysis["compiledPlan"]
    assert compiled["schemaVersion"] == "hwpx.mixed-form-compiled-plan/v1"
    assert compiled["planHash"] == analysis["planHash"]
    assert [item["locatorKind"] for item in analysis["resolutions"]] == [
        "nativeField",
        "labelCell",
        "canonicalPath",
        "bodyAnchor",
    ]

    result = server.apply_form_fill(plan=compiled)
    assert result["ok"] is True
    assert result["rolledBack"] is False
    assert result["openSafety"]["ok"] is True
    assert result["verificationReceipt"]["reopen"]["ok"] is True
    assert result["verificationReceipt"]["bytePreservation"]["ok"] is True
    assert result["verificationReceipt"]["schemaVersion"] == FORM_VERIFICATION_RECEIPT_SCHEMA
    assert result["verificationReceipt"]["status"] == "committed"
    assert result["verificationReceipt"]["plan"]["planHash"] == compiled["planHash"]
    assert result["verificationReceipt"]["sourcePreservation"]["ok"] is True
    assert validate_editor_open_safety(output).ok
    assert source.read_bytes() == source_before

    changed = set(result["verificationReceipt"]["bytePreservation"]["changedMembers"])
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        common = set(before.namelist()) & set(after.namelist())
        assert all(before.read(name) == after.read(name) for name in common - changed)
    with HwpxDocument.open(output) as document:
        assert document.list_form_fields()[0]["current_value"] == "AI 수업 나눔의 날"
    with HwpxAgentDocument.open(output) as agent:
        purpose = agent.resolve_record('/section[1]/paragraph[@id="240050"]')
        owner = agent.resolve_record('/section[1]/paragraph[@id="240040"]')
        assert purpose.summary["text"] == "행사 목적: 교내 AI 활용 사례 공유"
        assert owner.summary["text"] == "담당자: 김서현"

    verified = server.verify_form_fill(plan=compiled)
    assert verified["schemaVersion"] == FORM_VERIFICATION_RECEIPT_SCHEMA
    assert verified["phase"] == "verify"
    assert verified["ok"] is True
    assert verified["plan"]["planHash"] == compiled["planHash"]
    assert verified["sourcePreservation"]["ok"] is True
    assert verified["memberDiff"]["ok"] is True
    assert verified["valueVerification"]["ok"] is True
    assert len(verified["valueVerification"]["checks"]) == 4

    server.fill_form_field(str(output), "변조값", field_id="240021")
    tampered = server.verify_form_fill(plan=compiled)
    assert tampered["ok"] is False
    assert tampered["valueVerification"]["ok"] is False
    assert any(
        item["operationId"] == "native-project" and not item["ok"]
        for item in tampered["valueVerification"]["checks"]
    )


def test_dry_run_rollback_replay_and_key_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.hwpx"
    _build_fixture(source)

    dry_output = tmp_path / "dry.hwpx"
    dry = server.apply_form_fill(plan=_plan(source, dry_output, dry_run=True))
    assert dry["ok"] is True and dry["dryRun"] is True
    assert not dry_output.exists()
    assert dry["verificationReceipt"]["status"] == "dry-run"
    assert dry["verificationReceipt"]["sourcePreservation"]["ok"] is True

    failed_output = tmp_path / "failed.hwpx"
    failed_output.write_bytes(b"existing destination")

    def fail_after_second(stage: str, index: int | None) -> None:
        if stage == "after_command" and index == 1:
            raise RuntimeError("injected MCP mixed-form failure")

    monkeypatch.setattr(mixed_form_adapter, "_fault_injector_for_tests", fail_after_second)
    failed = server.apply_form_fill(plan=_plan(source, failed_output))
    assert failed["ok"] is False and failed["rolledBack"] is True
    assert failed_output.read_bytes() == b"existing destination"
    assert failed["verificationReceipt"]["status"] == "rolled-back"
    assert failed["verificationReceipt"]["rollbackPreservation"]["preserved"] is True
    monkeypatch.setattr(mixed_form_adapter, "_fault_injector_for_tests", None)

    replay_output = tmp_path / "replay.hwpx"
    request = _plan(source, replay_output, key="mcp-replay-key")
    first = server.apply_form_fill(plan=request)
    replay = server.apply_form_fill(plan=request)
    assert first["ok"] is True and replay["ok"] is True
    assert replay["verificationReceipt"]["idempotency"]["replayed"] is True

    conflict_request = copy.deepcopy(request)
    conflict_request["operations"][0]["value"] = "다른 사업명"
    conflict = server.apply_form_fill(plan=conflict_request)
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "idempotency_conflict"


def test_compiled_paths_are_authorized_canonical_and_writes_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.hwpx"
    output = tmp_path / "output.hwpx"
    _build_fixture(source)
    with pytest.raises(AgentContractError, match="output"):
        server.analyze_form_fill(plan=_plan(source, source))

    compiled = server.analyze_form_fill(plan=_plan(source, output))["compiledPlan"]
    relative = copy.deepcopy(compiled)
    relative["batch"]["input"]["filename"] = source.name
    with pytest.raises(ValueError, match="canonical absolute path"):
        server.apply_form_fill(plan=relative)

    def blocked() -> None:
        raise RuntimeError("capability skew")

    monkeypatch.setattr(server.quality_contract, "assert_write_capability", blocked)
    with pytest.raises(RuntimeError, match="capability skew"):
        server.apply_form_fill(plan=compiled)
    assert not output.exists()


def test_zero_multiple_and_cross_run_targets_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    _build_fixture(source)
    missing = _single(_plan(source, tmp_path / "missing.hwpx"), "body-owner")
    missing["operations"][0]["target"]["anchor"] = "{{없는값}}"
    with pytest.raises(AgentContractError, match="not found") as absent:
        server.analyze_form_fill(plan=missing)
    assert absent.value.code == "not_found"

    duplicate = tmp_path / "duplicate.hwpx"
    _build_fixture(duplicate, duplicate_native_field=True)
    duplicate_plan = _single(
        _plan(duplicate, tmp_path / "duplicate-out.hwpx"),
        "native-project",
    )
    duplicate_plan["operations"][0]["target"] = {
        "kind": "nativeField",
        "name": "사업명",
    }
    with pytest.raises(AgentContractError) as ambiguous:
        server.analyze_form_fill(plan=duplicate_plan)
    assert ambiguous.value.code == "ambiguous_target"

    split = tmp_path / "split.hwpx"
    _build_fixture(split, split_body_anchor=True)
    with pytest.raises(AgentContractError, match="run boundaries") as cross_run:
        server.analyze_form_fill(
            plan=_single(_plan(split, tmp_path / "split-out.hwpx"), "body-owner")
        )
    assert cross_run.value.code == "unsupported_content"


def test_legacy_formfill_is_marked_and_evalplan_exam_remain_separate(tmp_path: Path) -> None:
    source = tmp_path / "legacy.hwpx"
    output = tmp_path / "legacy-out.hwpx"
    _build_fixture(source)
    legacy = server.analyze_form_fill(
        str(source),
        input_json={
            "schemaVersion": "hwpx.formfill.v1",
            "fields": [{"label": "담당 부서", "value": "교육연구부"}],
        },
        destination_filename=str(output),
    )
    assert legacy["compatibility"]["schemaVersion"] == "hwpx.formfill.v1"
    assert callable(server.apply_evalplan_fill)
    assert callable(server.compose_exam)

    tree = ast.parse(Path(mixed_form_adapter.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert not any(name == "hwpx.exam" or name.startswith("hwpx.exam.") for name in imported)
    assert "exam" not in str(bound_tool_registry().by_name()["analyze_form_fill"].input_schema).casefold()
