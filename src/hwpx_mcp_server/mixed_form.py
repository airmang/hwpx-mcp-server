# SPDX-License-Identifier: Apache-2.0
"""Typed MCP adapter for the canonical mixed-anchor form-fill transaction.

The adapter deliberately does not edit HWPX packages.  It validates the public
wire model, resolves workspace paths, and delegates planning and execution to
``python-hwpx``'s single mixed-form compiler/executor.
"""

from __future__ import annotations

import copy
import hashlib
import threading
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, TypeAlias

from hwpx.agent import (
    MIXED_FORM_COMPILED_PLAN_SCHEMA,
    MIXED_FORM_PLAN_SCHEMA,
    HwpxAgentDocument,
    apply_mixed_form_plan,
    plan_mixed_form_fill,
    validate_mixed_form_plan,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .storage import build_hwpx_open_safety_report
from .utils.helpers import resolve_path


FORM_VERIFICATION_RECEIPT_SCHEMA = "hwpx.form-verification-receipt/v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NativeFieldTarget(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra={
            "oneOf": [
                {"required": ["fieldId"], "not": {"required": ["name"]}},
                {"required": ["name"], "not": {"required": ["fieldId"]}},
            ]
        },
    )

    kind: Literal["nativeField"]
    field_id: str | None = Field(default=None, alias="fieldId", min_length=1)
    name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _one_selector(self) -> NativeFieldTarget:
        if (self.field_id is None) == (self.name is None):
            raise ValueError("nativeField requires exactly one of fieldId or name")
        return self


class LabelCellAnchor(_StrictModel):
    label: str = Field(min_length=1)
    direction: Literal["right", "left", "below", "above"]


class LabelCellTarget(_StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra={
            "oneOf": [
                {"required": ["tableAnchor"], "not": {"required": ["tableIndex"]}},
                {"required": ["tableIndex"], "not": {"required": ["tableAnchor"]}},
            ]
        },
    )

    kind: Literal["labelCell"]
    section_path: str = Field(
        alias="sectionPath",
        pattern=r"^/section\[[1-9][0-9]*\]$",
    )
    table_anchor: str | None = Field(default=None, alias="tableAnchor", min_length=1)
    table_index: int | None = Field(default=None, alias="tableIndex", ge=0)
    cell_anchor: LabelCellAnchor = Field(alias="cellAnchor")

    @model_validator(mode="after")
    def _one_table_selector(self) -> LabelCellTarget:
        if (self.table_anchor is None) == (self.table_index is None):
            raise ValueError("labelCell requires exactly one of tableAnchor or tableIndex")
        return self


class CanonicalPathTarget(_StrictModel):
    kind: Literal["canonicalPath"]
    path: str = Field(min_length=1)


class BodyAnchorTarget(_StrictModel):
    kind: Literal["bodyAnchor"]
    section_path: str = Field(
        alias="sectionPath",
        pattern=r"^/section\[[1-9][0-9]*\]$",
    )
    anchor: str = Field(min_length=1)
    expected_count: Literal[1] = Field(alias="expectedCount")


MixedFormTarget: TypeAlias = Annotated[
    NativeFieldTarget | LabelCellTarget | CanonicalPathTarget | BodyAnchorTarget,
    Field(discriminator="kind"),
]


class MixedFormOperation(_StrictModel):
    operation_id: str = Field(
        alias="operationId",
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$",
    )
    target: MixedFormTarget
    value: str


class MixedFormQuality(_StrictModel):
    mode: Literal["transparent", "strict"] | None = None
    render_check: Literal["off", "auto", "required"] | None = Field(
        default=None,
        alias="renderCheck",
    )
    xsd_mode: Literal["off", "lint"] | None = Field(default=None, alias="xsdMode")
    overflow_policy: Literal["fail", "warn", "truncate"] | None = Field(
        default=None,
        alias="overflowPolicy",
    )
    layout_lint: Literal["off", "warn", "strict"] | None = Field(
        default=None,
        alias="layoutLint",
    )
    preserve_unmodified_parts: bool | None = Field(
        default=None,
        alias="preserveUnmodifiedParts",
    )
    require_reference_integrity: bool | None = Field(
        default=None,
        alias="requireReferenceIntegrity",
    )


VerificationRequirement: TypeAlias = Literal[
    "package",
    "reopen",
    "openSafety",
    "semanticDiff",
    "bytePreservation",
    "domain",
    "realHancom",
]
QualityInput: TypeAlias = Literal["transparent", "strict"] | MixedFormQuality | None


class MixedFormPlanInput(_StrictModel):
    """Strict public ``hwpx.mixed-form-plan/v1`` request."""

    schema_version: Literal["hwpx.mixed-form-plan/v1"] = Field(alias="schemaVersion")
    source: str = Field(min_length=1)
    output: str = Field(min_length=1)
    expected_revision: str | None = Field(
        alias="expectedRevision",
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    idempotency_key: str | None = Field(
        alias="idempotencyKey",
        min_length=1,
        max_length=128,
    )
    dry_run: bool = Field(alias="dryRun")
    overwrite: bool
    quality: QualityInput
    verification_requirements: list[VerificationRequirement] = Field(
        alias="verificationRequirements",
    )
    operations: list[MixedFormOperation] = Field(min_length=1, max_length=100)


class _FilenameRef(_StrictModel):
    filename: str = Field(min_length=1)


class _OutputRef(_FilenameRef):
    overwrite: bool


class _TextSetProperties(_StrictModel):
    text: str


class _ValueSetProperties(_StrictModel):
    value: str


class _CompiledSetCommand(_StrictModel):
    command_id: str = Field(
        alias="commandId",
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$",
    )
    op: Literal["set"]
    path: str = Field(min_length=1)
    properties: _TextSetProperties | _ValueSetProperties


class _CompiledBatch(_StrictModel):
    schema_version: Literal["hwpx.agent-batch/v1"] = Field(alias="schemaVersion")
    input: _FilenameRef
    output: _OutputRef
    commands: list[_CompiledSetCommand] = Field(min_length=1, max_length=100)
    expected_revision: str = Field(
        alias="expectedRevision",
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    idempotency_key: str | None = Field(
        alias="idempotencyKey",
        min_length=1,
        max_length=128,
    )
    dry_run: bool = Field(alias="dryRun")
    quality: QualityInput
    verification_requirements: list[VerificationRequirement] = Field(
        alias="verificationRequirements",
    )


class MixedFormResolution(_StrictModel):
    operation_id: str = Field(alias="operationId")
    locator_kind: Literal["nativeField", "labelCell", "canonicalPath", "bodyAnchor"] = Field(
        alias="locatorKind"
    )
    path: str
    node_kind: Literal["paragraph", "run", "cell", "form-field"] = Field(alias="nodeKind")
    stability: Literal["native", "derived", "positional"]
    section: int | None
    table_index: int | None = Field(alias="tableIndex")
    logical_row: int | None = Field(alias="logicalRow")
    logical_column: int | None = Field(alias="logicalColumn")
    physical_row: int | None = Field(alias="physicalRow")
    physical_column: int | None = Field(alias="physicalColumn")


class MixedFormCompiledPlanInput(_StrictModel):
    """Detached, revision-bound plan returned by ``analyze_form_fill``."""

    schema_version: Literal["hwpx.mixed-form-compiled-plan/v1"] = Field(
        alias="schemaVersion"
    )
    input_revision: str = Field(
        alias="inputRevision",
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    request_hash: str = Field(
        alias="requestHash",
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    resolutions: list[MixedFormResolution] = Field(min_length=1, max_length=100)
    batch: _CompiledBatch
    plan_hash: str = Field(
        alias="planHash",
        pattern=r"^sha256:[0-9a-f]{64}$",
    )


MixedFormApplyInput: TypeAlias = Annotated[
    MixedFormPlanInput | MixedFormCompiledPlanInput,
    Field(discriminator="schema_version"),
]


_PUBLIC_PLAN_ADAPTER = TypeAdapter(MixedFormPlanInput)
_APPLY_PLAN_ADAPTER = TypeAdapter(MixedFormApplyInput)
_IDEMPOTENCY_STORE: dict[str, Any] = {}
_IDEMPOTENCY_LOCK = threading.RLock()
# Test-only failure seam.  It is intentionally absent from every MCP model.
_fault_injector_for_tests: Any = None


def _payload(value: BaseModel | dict[str, Any], *, apply: bool) -> dict[str, Any]:
    adapter = _APPLY_PLAN_ADAPTER if apply else _PUBLIC_PLAN_ADAPTER
    parsed = adapter.validate_python(value)
    if not isinstance(parsed, BaseModel):  # pragma: no cover - union invariant
        raise TypeError("mixed-form plan did not validate to a typed model")
    payload = parsed.model_dump(by_alias=True, exclude_none=False)
    # Nullable fields at the plan/batch level are required by the frozen core
    # schema. Selector and quality optionals, however, are presence-sensitive.
    if payload["schemaVersion"] == MIXED_FORM_PLAN_SCHEMA:
        for operation in payload["operations"]:
            target = operation["target"]
            for name in ("fieldId", "name", "tableAnchor", "tableIndex"):
                if target.get(name) is None:
                    target.pop(name, None)
    quality = payload.get("quality")
    if isinstance(quality, dict):
        payload["quality"] = {name: item for name, item in quality.items() if item is not None}
    batch_quality = payload.get("batch", {}).get("quality")
    if isinstance(batch_quality, dict):
        payload["batch"]["quality"] = {
            name: item for name, item in batch_quality.items() if item is not None
        }
    return payload


def _authorize_public_plan(payload: dict[str, Any]) -> dict[str, Any]:
    authorized = copy.deepcopy(payload)
    authorized["source"] = resolve_path(str(payload["source"]))
    authorized["output"] = resolve_path(str(payload["output"]))
    return authorized


def _authorize_compiled_plan(payload: dict[str, Any]) -> tuple[Path, Path]:
    batch = payload["batch"]
    # Resolution is itself the workspace authorization check.  Keep the bytes
    # hash-protected compiled payload untouched after checking it.
    source_text = str(batch["input"]["filename"])
    output_text = str(batch["output"]["filename"])
    source = Path(resolve_path(source_text))
    output = Path(resolve_path(output_text))
    # A detached plan cannot be rewritten after its planHash is frozen. Reject
    # relative/non-canonical coordinates whose runtime interpretation could
    # differ from the workspace resolver used for authorization.
    for raw, resolved, name in (
        (source_text, source, "batch.input.filename"),
        (output_text, output, "batch.output.filename"),
    ):
        raw_path = Path(raw).expanduser()
        if not raw_path.is_absolute() or raw_path.resolve(strict=False) != resolved:
            raise ValueError(
                f"compiled mixed-form {name} must be an authorized canonical absolute path; "
                "run analyze_form_fill again"
            )
    return source, output


def _revision_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _file_revision(path: Path) -> str | None:
    try:
        return _revision_bytes(path.read_bytes())
    except OSError:
        return None


def _member_diff(before: bytes, after: bytes) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(BytesIO(before)) as old_zip, zipfile.ZipFile(
            BytesIO(after)
        ) as new_zip:
            old_names = set(old_zip.namelist())
            new_names = set(new_zip.namelist())
            shared = sorted(old_names & new_names)
            changed = [name for name in shared if old_zip.read(name) != new_zip.read(name)]
            return {
                "ok": True,
                "changedMembers": changed,
                "addedMembers": sorted(new_names - old_names),
                "removedMembers": sorted(old_names - new_names),
                "unchangedMemberCount": len(shared) - len(changed),
                "beforeMemberCount": len(old_names),
                "afterMemberCount": len(new_names),
            }
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _not_produced(reason: str = "not-produced") -> dict[str, Any]:
    return {"ok": None, "status": reason}


def _expected_values(compiled: dict[str, Any]) -> list[dict[str, Any]]:
    expected: list[dict[str, Any]] = []
    commands = compiled["batch"]["commands"]
    for resolution, command in zip(compiled["resolutions"], commands, strict=True):
        properties = command["properties"]
        property_name = "value" if "value" in properties else "text"
        expected.append(
            {
                "operationId": resolution["operationId"],
                "locatorKind": resolution["locatorKind"],
                "path": resolution["path"],
                "nodeKind": resolution["nodeKind"],
                "property": property_name,
                "value": properties[property_name],
            }
        )
    return expected


def _source_preservation(
    source: Path,
    *,
    expected_revision: str | None,
    before: bytes | None = None,
) -> dict[str, Any]:
    actual_revision = _file_revision(source)
    before_revision = _revision_bytes(before) if before is not None else expected_revision
    preserved = (
        actual_revision is not None
        and before_revision is not None
        and actual_revision == before_revision
    )
    return {
        "ok": preserved,
        "filename": str(source),
        "expectedRevision": before_revision,
        "actualRevision": actual_revision,
        "preserved": preserved,
    }


def _unified_receipt(
    *,
    phase: Literal["apply", "verify", "domain-apply"],
    ok: bool,
    source: Path,
    output: Path,
    dry_run: bool,
    rolled_back: bool,
    source_preservation: dict[str, Any],
    plan: dict[str, Any] | None = None,
    core_verification: dict[str, Any] | None = None,
    member_diff: dict[str, Any] | None = None,
    value_checks: list[dict[str, Any]] | None = None,
    output_revision: str | None = None,
    error: dict[str, Any] | None = None,
    domain: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verification = copy.deepcopy(core_verification or {})
    if ok and dry_run:
        status = "dry-run"
    elif ok and phase == "verify":
        status = "verified"
    elif ok:
        status = "committed"
    elif rolled_back:
        status = "rolled-back"
    else:
        status = "failed"
    output_exists = output.exists()
    open_safety = copy.deepcopy(verification.get("openSafety"))
    if not isinstance(open_safety, dict):
        open_safety = _not_produced("dry-run" if dry_run else "not-produced")
    byte_preservation = copy.deepcopy(
        member_diff or verification.get("bytePreservation") or _not_produced()
    )
    plan_summary = None
    resolutions: list[dict[str, Any]] = []
    expected: list[dict[str, Any]] = []
    if plan is not None:
        plan_summary = {
            "schemaVersion": plan["schemaVersion"],
            "planHash": plan["planHash"],
            "requestHash": plan["requestHash"],
            "inputRevision": plan["inputRevision"],
        }
        resolutions = copy.deepcopy(plan["resolutions"])
        expected = _expected_values(plan)
    checks = copy.deepcopy(value_checks or [])
    values_ok: bool | None = None
    if checks:
        values_ok = all(bool(item.get("ok")) for item in checks)
    return {
        "schemaVersion": FORM_VERIFICATION_RECEIPT_SCHEMA,
        "phase": phase,
        "status": status,
        "ok": ok,
        "dryRun": dry_run,
        "committed": bool(ok and not dry_run and phase != "verify"),
        "rolledBack": rolled_back,
        "plan": plan_summary,
        "source": {
            "filename": str(source),
            "revision": source_preservation.get("actualRevision"),
        },
        "output": {
            "filename": str(output),
            "exists": output_exists,
            "revision": output_revision,
        },
        "sourcePreservation": copy.deepcopy(source_preservation),
        "resolutions": resolutions,
        "expectedValues": expected,
        "valueVerification": {
            "ok": values_ok,
            "status": "checked" if checks else "deferred",
            "checks": checks,
        },
        "package": copy.deepcopy(verification.get("package") or _not_produced()),
        "reopen": copy.deepcopy(verification.get("reopen") or _not_produced()),
        "openSafety": open_safety,
        "semanticDiff": copy.deepcopy(
            verification.get("semanticDiff") or _not_produced()
        ),
        "memberDiff": byte_preservation,
        # Compatibility key retained for callers already reading the core name.
        "bytePreservation": copy.deepcopy(byte_preservation),
        "idempotency": copy.deepcopy(
            verification.get("idempotency") or _not_produced()
        ),
        "savePipeline": copy.deepcopy(
            verification.get("savePipeline") or _not_produced()
        ),
        "domain": copy.deepcopy(domain or verification.get("domain") or _not_produced()),
        "realHancom": copy.deepcopy(
            verification.get("realHancom") or _not_produced("not-requested")
        ),
        "error": copy.deepcopy(error),
    }


def analyze_mixed_form_plan(plan: MixedFormPlanInput | dict[str, Any]) -> dict[str, Any]:
    """Compile a public plan without copying, saving, or mutating either path."""

    request = _authorize_public_plan(_payload(plan, apply=False))
    source = Path(request["source"])
    before = source.read_bytes()
    output = Path(request["output"])
    output_existed = output.exists()
    output_hash = hashlib.sha256(output.read_bytes()).hexdigest() if output_existed else None

    compiled = plan_mixed_form_fill(request).to_dict()

    source_after = source.read_bytes()
    output_unchanged = output.exists() == output_existed
    if output_existed and output_hash is not None:
        output_unchanged = output_unchanged and hashlib.sha256(output.read_bytes()).hexdigest() == output_hash
    if source_after != before or not output_unchanged:  # pragma: no cover - core purity invariant
        raise RuntimeError("mixed-form analysis mutated an input or output file")
    detached = copy.deepcopy(compiled)
    return {
        "schemaVersion": "hwpx.mixed-form-analysis/v1",
        "mutated": False,
        "source": {
            "filename": str(source),
            "revision": detached["inputRevision"],
            "unchangedAfterAnalysis": True,
        },
        "output": {
            "filename": str(output),
            "unchangedAfterAnalysis": True,
        },
        "compiledPlan": detached,
        "planHash": detached["planHash"],
        "requestHash": detached["requestHash"],
        "resolutions": copy.deepcopy(detached["resolutions"]),
        "openSafety": build_hwpx_open_safety_report(source),
        "nextTool": "apply_form_fill",
    }


def apply_canonical_mixed_form_plan(
    plan: MixedFormApplyInput | dict[str, Any],
) -> dict[str, Any]:
    """Apply one typed public/compiled plan through the core transaction."""

    payload = _payload(plan, apply=True)
    if payload["schemaVersion"] == MIXED_FORM_PLAN_SCHEMA:
        compiled = plan_mixed_form_fill(_authorize_public_plan(payload)).to_dict()
    elif payload["schemaVersion"] == MIXED_FORM_COMPILED_PLAN_SCHEMA:
        _authorize_compiled_plan(payload)
        compiled = payload
    else:  # pragma: no cover - discriminated union guard
        raise ValueError("unsupported mixed-form plan schema")

    # Revalidate the frozen hash/invariants before observing any filesystem
    # state, then bind the receipt to exact pre/post bytes.
    compiled = validate_mixed_form_plan(compiled).to_dict()
    source, output = _authorize_compiled_plan(compiled)
    source_before = source.read_bytes()
    output_existed = output.exists()
    output_before = output.read_bytes() if output_existed else None

    with _IDEMPOTENCY_LOCK:
        result = apply_mixed_form_plan(
            compiled,
            idempotency_store=_IDEMPOTENCY_STORE,
            fault_injector=_fault_injector_for_tests,
        )
    response = result.to_dict()
    core_verification = copy.deepcopy(response.get("verificationReport", {}))
    source_report = _source_preservation(
        source,
        expected_revision=compiled["inputRevision"],
        before=source_before,
    )
    output_after = output.read_bytes() if output.exists() else None
    if output_before is None:
        rollback_preserved = output_after is None
    else:
        rollback_preserved = output_after == output_before
    rollback_report = {
        "required": bool(response.get("rolledBack") or response.get("dryRun")),
        "outputExistedBefore": output_existed,
        "preserved": rollback_preserved,
    }
    receipt = _unified_receipt(
        phase="apply",
        ok=bool(response.get("ok")) and bool(source_report["ok"]),
        source=source,
        output=output,
        dry_run=bool(response.get("dryRun")),
        rolled_back=bool(response.get("rolledBack")),
        source_preservation=source_report,
        plan=compiled,
        core_verification=core_verification,
        member_diff=core_verification.get("bytePreservation"),
        output_revision=(
            str(response.get("documentRevision"))
            if response.get("ok")
            else _file_revision(output)
        ),
        error=response.get("error"),
    )
    receipt["rollbackPreservation"] = rollback_report
    response.update(
        {
            "planHash": compiled["planHash"],
            "requestHash": compiled["requestHash"],
            "resolutions": copy.deepcopy(compiled["resolutions"]),
            "verificationReceipt": receipt,
            "openSafety": copy.deepcopy(
                receipt.get(
                    "openSafety",
                    {"ok": False, "status": "not-produced"},
                )
            ),
        }
    )
    return response


def verify_canonical_mixed_form_plan(
    plan: MixedFormCompiledPlanInput | dict[str, Any],
    *,
    require: bool = False,
    render_verifier: Callable[[str, str, bool], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify one compiled plan against the materialized output and source."""

    payload = _payload(plan, apply=True)
    if payload["schemaVersion"] != MIXED_FORM_COMPILED_PLAN_SCHEMA:
        raise ValueError("verify_form_fill requires hwpx.mixed-form-compiled-plan/v1")
    compiled = validate_mixed_form_plan(payload).to_dict()
    source, output = _authorize_compiled_plan(compiled)
    source_report = _source_preservation(
        source,
        expected_revision=compiled["inputRevision"],
    )
    checks: list[dict[str, Any]] = []
    output_bytes: bytes | None = None
    output_error: str | None = None
    try:
        output_bytes = output.read_bytes()
        with HwpxAgentDocument.open(output_bytes) as document:
            for expected in _expected_values(compiled):
                try:
                    record = document.resolve_record(expected["path"])
                    actual = record.summary.get(expected["property"])
                    checks.append(
                        {
                            **copy.deepcopy(expected),
                            "actual": actual,
                            "ok": actual == expected["value"],
                        }
                    )
                except Exception as exc:
                    checks.append(
                        {
                            **copy.deepcopy(expected),
                            "actual": None,
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
    except Exception as exc:
        output_error = f"{type(exc).__name__}: {exc}"

    if output_bytes is not None:
        open_safety = build_hwpx_open_safety_report(output)
        member_diff = _member_diff(source.read_bytes(), output_bytes)
        core_verification = {
            "package": copy.deepcopy(open_safety.get("validatePackage") or _not_produced()),
            "reopen": copy.deepcopy(open_safety.get("reopen") or _not_produced()),
            "openSafety": open_safety,
            "semanticDiff": {
                "ok": all(bool(item.get("ok")) for item in checks),
                "changeCount": len(checks),
            },
            "bytePreservation": member_diff,
        }
    else:
        open_safety = {"ok": False, "status": "output-unavailable", "error": output_error}
        member_diff = {"ok": False, "status": "output-unavailable", "error": output_error}
        core_verification = {
            "openSafety": open_safety,
            "bytePreservation": member_diff,
        }

    real_hancom = _not_produced("not-requested")
    if render_verifier is not None and (require or output_bytes is not None):
        real_hancom = render_verifier(str(output), str(source), require)
    core_verification["realHancom"] = real_hancom
    values_ok = bool(checks) and all(bool(item.get("ok")) for item in checks)
    render_ok = bool(real_hancom.get("ok")) if require else True
    ok = bool(
        source_report["ok"]
        and output_bytes is not None
        and open_safety.get("ok")
        and member_diff.get("ok")
        and values_ok
        and render_ok
    )
    receipt = _unified_receipt(
        phase="verify",
        ok=ok,
        source=source,
        output=output,
        dry_run=False,
        rolled_back=False,
        source_preservation=source_report,
        plan=compiled,
        core_verification=core_verification,
        member_diff=member_diff,
        value_checks=checks,
        output_revision=_revision_bytes(output_bytes) if output_bytes is not None else None,
        error=(
            None
            if ok
            else {
                "code": "verification_failed",
                "message": output_error or "compiled mixed-form output verification failed",
            }
        ),
    )
    return receipt


def attach_common_form_receipt(
    result: dict[str, Any],
    *,
    operation: str,
    source: Path,
    output: Path,
    source_before: bytes,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Attach the shared receipt envelope to a specialized form workflow."""

    payload = copy.deepcopy(result)
    target = output if not dry_run else source
    source_is_output = source.resolve(strict=False) == output.resolve(strict=False)
    if source_is_output and not dry_run:
        source_report = {
            "ok": None,
            "filename": str(source),
            "expectedRevision": _revision_bytes(source_before),
            "actualRevision": _file_revision(source),
            "preserved": None,
            "status": "in-place-specialized-operation",
        }
    else:
        source_report = _source_preservation(
            source,
            expected_revision=_revision_bytes(source_before),
            before=source_before,
        )
    target_bytes = target.read_bytes() if target.exists() else None
    member_diff = (
        _member_diff(source_before, target_bytes)
        if target_bytes is not None
        else _not_produced("dry-run" if dry_run else "output-unavailable")
    )
    open_safety = copy.deepcopy(payload.get("openSafety"))
    if not isinstance(open_safety, dict) and target_bytes is not None:
        open_safety = build_hwpx_open_safety_report(target)
    verification = copy.deepcopy(payload.get("verificationReport") or {})
    if isinstance(open_safety, dict):
        verification.setdefault("openSafety", open_safety)
        verification.setdefault("package", open_safety.get("validatePackage"))
        verification.setdefault("reopen", open_safety.get("reopen"))
    verification.setdefault("bytePreservation", member_diff)
    domain = {
        "ok": payload.get("ok"),
        "operation": operation,
        "status": "specialized-semantics-preserved",
    }
    receipt = _unified_receipt(
        phase="domain-apply",
        ok=bool(payload.get("ok", True)),
        source=source,
        output=output,
        dry_run=dry_run,
        rolled_back=False,
        source_preservation=source_report,
        core_verification=verification,
        member_diff=member_diff,
        output_revision=_file_revision(output) if output.exists() else None,
        domain=domain,
    )
    receipt["operation"] = operation
    payload["verificationReceipt"] = receipt
    payload.setdefault("compatibility", {})
    payload["compatibility"].update(
        {
            "status": "specialized-semantics-preserved",
            "canonicalReceipt": FORM_VERIFICATION_RECEIPT_SCHEMA,
        }
    )
    return payload


__all__ = [
    "BodyAnchorTarget",
    "CanonicalPathTarget",
    "LabelCellTarget",
    "MixedFormApplyInput",
    "MixedFormCompiledPlanInput",
    "MixedFormOperation",
    "MixedFormPlanInput",
    "MixedFormResolution",
    "MixedFormTarget",
    "NativeFieldTarget",
    "FORM_VERIFICATION_RECEIPT_SCHEMA",
    "analyze_mixed_form_plan",
    "apply_canonical_mixed_form_plan",
    "attach_common_form_receipt",
    "verify_canonical_mixed_form_plan",
]
