#!/usr/bin/env python3
"""Render/check the versioned 4.0.0 ToolSpec consolidation delta."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hwpx_mcp_server import runtime as _runtime  # noqa: E402,F401
from hwpx_mcp_server.tool_contract import (  # noqa: E402
    BASELINE_TOOL_SPECS,
    TOOL_SPECS,
    ToolClassification,
    bound_tool_registry,
    contract_hash,
)


DEFAULT_OUTPUT = ROOT / "docs" / "tool-contract-delta-4.0.0.json"
MAJOR_BOUNDARY_RECEIPT = ROOT / "docs" / "tool-contract-delta-5.0.0.json"
# The five transition stubs removed at the 5.0.0 major boundary and the three
# template-formfit facades demoted from compatibility to deprecated. This is the
# structural contract the hand-authored 5.0.0 receipt must record, cross-checked
# against the live registry so the receipt cannot drift from the real removal.
MAJOR_BOUNDARY_REMOVED_STUBS = (
    "plan_edit",
    "preview_edit",
    "apply_edit",
    "analyze_quality_generation",
    "apply_quality_generation",
)
MAJOR_BOUNDARY_DEMOTED_FACADES = (
    "analyze_template_formfit",
    "apply_template_formfit",
    "fill_form_field",
)
BASELINE: dict[str, Any] = {
    "versions": {
        "pythonHwpx": "3.0.0",
        "mcpServer": "3.0.0",
        "skill": "0.2.0",
    },
    "defaultToolCount": 126,
    "advancedToolCount": 136,
    "domainCount": 21,
    "skillRequiredToolCount": 30,
    "contractHash": "76d143ccc0787828",
}
FROZEN_TARGET: dict[str, Any] = {
    "versions": {
        "pythonHwpx": "3.1.0",
        "mcpServer": "4.0.0",
        "skill": "0.3.0",
    },
    "defaultToolCount": 121,
    "advancedToolCount": 132,
    "domainCount": 19,
    "skillRequiredToolCount": 28,
    "contractHash": "f46ec677231b3a20",
}
FROZEN_BINDING_HASH = "0deefdad1aa81a68"
# The advanced installed surface size exactly as bound at the 4.0.0 release. The
# 5.0.0 major boundary removes the five transition stubs (127 installed), so this
# historical receipt keeps its own frozen count instead of reading the live
# registry (history must not mutate with later removals).
FROZEN_BOUND_INSTALLED_TOOL_COUNT = 132
# The eleven compatibility facades exactly as classified at the 4.0.0 release.
# 4.3.0 promoted apply_table_ops/apply_body_ops to public, so this historical
# receipt keeps its own frozen list instead of deriving it from the live
# registry (history must not mutate with later reclassifications).
_CANONICAL_FORM_FILL = ("analyze_form_fill", "apply_form_fill", "verify_form_fill")
FROZEN_COMPATIBILITY_FACADES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("apply_table_ops", _CANONICAL_FORM_FILL),
    ("apply_body_ops", _CANONICAL_FORM_FILL),
    ("fill_form_field", _CANONICAL_FORM_FILL),
    ("fill_by_path", _CANONICAL_FORM_FILL),
    ("analyze_template_formfit", _CANONICAL_FORM_FILL),
    ("apply_template_formfit", _CANONICAL_FORM_FILL),
    ("apply_evalplan_fill", _CANONICAL_FORM_FILL),
    ("create_government_report_document", ("create_document_from_plan",)),
    ("create_proposal_document", ("create_document_from_plan",)),
    ("create_comparison_table_document", ("create_document_from_plan",)),
    ("apply_edits", ("apply_document_commands",)),
)
# The five one-transition deprecation stubs exactly as classified at the 4.0.0
# release. The 5.0.0 major boundary removes them from the registry entirely (see
# docs/tool-contract-delta-5.0.0.json), so this historical receipt keeps its own
# frozen list instead of reading the live DEPRECATED classification, which now
# holds the demoted template-formfit facades rather than these stubs.
FROZEN_DEPRECATED_STUBS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "analyze_quality_generation",
        ("create_document_from_plan", "create_proposal_document", "inspect_document_quality"),
    ),
    (
        "apply_quality_generation",
        ("create_document_from_plan", "create_proposal_document", "inspect_document_quality"),
    ),
    ("plan_edit", ("apply_document_commands",)),
    ("preview_edit", ("apply_document_commands",)),
    ("apply_edit", ("apply_document_commands",)),
)


def _by_classification(classification: ToolClassification) -> list[Any]:
    return [
        spec
        for spec in BASELINE_TOOL_SPECS
        if spec.classification is classification
    ]


def _target() -> dict[str, Any]:
    """Return the immutable 4.0.0 release coordinates.

    This renderer is a historical receipt, not the current release contract.
    Keep the target and binding hash literal when later releases raise their
    compatibility floors and therefore receive a new canonical contract hash.
    """

    return dict(FROZEN_TARGET)


def build_payload() -> dict[str, Any]:
    target = _target()
    registry = bound_tool_registry()
    internal = _by_classification(ToolClassification.INTERNAL)
    live_compatibility = _by_classification(ToolClassification.COMPATIBILITY)
    removed_names = {spec.name for spec in internal}
    active_names = {spec.name for spec in TOOL_SPECS}
    server_source = (SRC / "hwpx_mcp_server" / "server.py").read_text(encoding="utf-8")
    runtime_hits = sorted(name for name in removed_names if name in server_source)

    errors: list[str] = []
    expected_counts = {
        "defaultToolCount": 121,
        "advancedToolCount": 132,
        "domainCount": 19,
        "skillRequiredToolCount": 28,
    }
    for key, expected in expected_counts.items():
        if target[key] != expected:
            errors.append(f"{key}: expected {expected}, got {target[key]}")
    if len(internal) != 4 or removed_names & active_names:
        errors.append("the four internal fixture-QA names must be absent from the installed contract")
    if len(FROZEN_COMPATIBILITY_FACADES) != 11 or any(
        not replacements for _, replacements in FROZEN_COMPATIBILITY_FACADES
    ):
        errors.append("all eleven 4.0.0 compatibility facades require replacement guidance")
    live_names = {spec.name for spec in live_compatibility}
    frozen_names = {name for name, _ in FROZEN_COMPATIBILITY_FACADES}
    if not live_names <= frozen_names:
        errors.append(
            "live compatibility facades outside the frozen 4.0.0 set: "
            f"{sorted(live_names - frozen_names)}"
        )
    if len(FROZEN_DEPRECATED_STUBS) != 5 or any(
        not replacements for _, replacements in FROZEN_DEPRECATED_STUBS
    ):
        errors.append("all five transition deprecations require replacement guidance")
    if runtime_hits:
        errors.append(f"removed fixture tools remain in server runtime: {runtime_hits}")
    if errors:
        raise RuntimeError("\n".join(errors))

    return {
        "schemaVersion": "hwpx.tool-contract-delta.v2",
        "release": "4.0.0",
        "changeKind": "breaking-removal-and-typed-registry",
        "baseline": BASELINE,
        "target": target,
        "delta": {
            key: target[key] - BASELINE[key]
            for key in (
                "defaultToolCount",
                "advancedToolCount",
                "domainCount",
                "skillRequiredToolCount",
            )
        },
        "removedTools": [
            {
                "name": spec.name,
                "classification": spec.classification.value,
                "destination": "internal CI library",
                "alias": None,
                "replacementTools": [],
            }
            for spec in internal
        ],
        "profileMoves": [
            {"name": "score_form_fill", "from": "default", "to": "advanced"}
        ],
        "compatibility": {
            "aliases": [],
            "facades": [
                {
                    "name": name,
                    "replacementTools": list(replacements),
                }
                for name, replacements in FROZEN_COMPATIBILITY_FACADES
            ],
            "deprecatedStubs": [
                {
                    "name": name,
                    "replacementTools": list(replacements),
                    "transitionRelease": "4.0.0",
                }
                for name, replacements in FROZEN_DEPRECATED_STUBS
            ],
            "ghostRegistrations": [],
        },
        "registrationEvidence": {
            "canonicalToolSpec": "src/hwpx_mcp_server/tool_contract.py",
            "fastMcpRegistration": "src/hwpx_mcp_server/server.py",
            "boundInstalledToolCount": FROZEN_BOUND_INSTALLED_TOOL_COUNT,
            "bindingHash": FROZEN_BINDING_HASH,
            "normalizedInputOutputSchemasComplete": all(
                bool(item.input_schema) and bool(item.output_schema)
                for item in registry.tools
            ),
            "remainingRemovedRuntimeReferences": runtime_hits,
            "activeRemovedNames": sorted(removed_names & active_names),
        },
    }


def render_json() -> str:
    return json.dumps(build_payload(), ensure_ascii=False, indent=2) + "\n"


def validate_major_boundary_receipt() -> None:
    """Cross-check the hand-authored 5.0.0 receipt against the live registry.

    The receipt is a frozen historical document like the 4.3.0/4.4.0 deltas, but
    the removal it records must be real: the five stubs are gone from every
    installed name and the three demoted facades are now DEPRECATED. The renderer
    verifies that here so the receipt cannot claim a removal the code did not make.
    """

    payload = json.loads(MAJOR_BOUNDARY_RECEIPT.read_text(encoding="utf-8"))
    errors: list[str] = []

    installed = {spec.name for spec in TOOL_SPECS}
    deprecated = {
        spec.name
        for spec in BASELINE_TOOL_SPECS
        if spec.classification is ToolClassification.DEPRECATED
    }

    removed = {item["name"] for item in payload.get("removedTools", [])}
    if removed != set(MAJOR_BOUNDARY_REMOVED_STUBS):
        errors.append(f"removedTools must list exactly the five stubs, got {sorted(removed)}")
    if removed & installed:
        errors.append(f"removed stubs still installed: {sorted(removed & installed)}")
    for item in payload.get("removedTools", []):
        if item.get("alias") is not None:
            errors.append(f"removed stub {item['name']} must not carry an alias")
        if not item.get("migrationReference"):
            errors.append(f"removed stub {item['name']} must cite a migrationReference")

    demoted = {item["name"] for item in payload.get("classificationChanges", [])}
    if demoted != set(MAJOR_BOUNDARY_DEMOTED_FACADES):
        errors.append(
            f"classificationChanges must list exactly the three demoted facades, got {sorted(demoted)}"
        )
    if not set(MAJOR_BOUNDARY_DEMOTED_FACADES) <= deprecated:
        errors.append(
            "demoted facades are not DEPRECATED in the live registry: "
            f"{sorted(set(MAJOR_BOUNDARY_DEMOTED_FACADES) - deprecated)}"
        )
    for item in payload.get("classificationChanges", []):
        if (item.get("from"), item.get("to")) != ("compatibility", "deprecated"):
            errors.append(f"{item.get('name')} must record compatibility -> deprecated")

    target_hash = payload.get("target", {}).get("contractHash")
    if target_hash != contract_hash():
        errors.append(
            f"target.contractHash {target_hash!r} != live contract hash {contract_hash()!r}"
        )

    if errors:
        raise RuntimeError("5.0.0 receipt is inconsistent with the live registry:\n- " + "\n- ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if args.output == DEFAULT_OUTPUT and MAJOR_BOUNDARY_RECEIPT.is_file():
        validate_major_boundary_receipt()

    content = render_json()
    current = args.output.read_text(encoding="utf-8") if args.output.is_file() else None
    if current == content:
        print(f"ok: {args.output}")
        return 0
    if args.check:
        print(f"drift: {args.output}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(f"wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
