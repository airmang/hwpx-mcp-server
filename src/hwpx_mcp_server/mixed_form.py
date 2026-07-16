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
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias

from hwpx.agent import (
    MIXED_FORM_COMPILED_PLAN_SCHEMA,
    MIXED_FORM_PLAN_SCHEMA,
    apply_mixed_form_plan,
    plan_mixed_form_fill,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .storage import build_hwpx_open_safety_report
from .utils.helpers import resolve_path


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NativeFieldTarget(_StrictModel):
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


def _authorize_compiled_plan(payload: dict[str, Any]) -> None:
    batch = payload["batch"]
    # Resolution is itself the workspace authorization check.  Keep the bytes
    # hash-protected compiled payload untouched after checking it.
    resolve_path(str(batch["input"]["filename"]))
    resolve_path(str(batch["output"]["filename"]))


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

    with _IDEMPOTENCY_LOCK:
        result = apply_mixed_form_plan(
            compiled,
            idempotency_store=_IDEMPOTENCY_STORE,
            fault_injector=_fault_injector_for_tests,
        )
    response = result.to_dict()
    receipt = copy.deepcopy(response.get("verificationReport", {}))
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
    "analyze_mixed_form_plan",
    "apply_canonical_mixed_form_plan",
]
