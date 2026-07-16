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

from hwpx_mcp_server.tool_contract import (  # noqa: E402
    BASELINE_TOOL_SPECS,
    DOMAIN_SPECS,
    MIN_MCP_VERSION,
    MIN_PYTHON_HWPX,
    MIN_SKILL_VERSION,
    TOOL_SPECS,
    ToolClassification,
    bound_tool_registry,
    contract_hash,
    expected_tool_names,
    skill_required_tool_names,
)


DEFAULT_OUTPUT = ROOT / "docs" / "tool-contract-delta-4.0.0.json"
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


def _by_classification(classification: ToolClassification) -> list[Any]:
    return [
        spec
        for spec in BASELINE_TOOL_SPECS
        if spec.classification is classification
    ]


def _target() -> dict[str, Any]:
    return {
        "versions": {
            "pythonHwpx": MIN_PYTHON_HWPX,
            "mcpServer": MIN_MCP_VERSION,
            "skill": MIN_SKILL_VERSION,
        },
        "defaultToolCount": len(expected_tool_names(advanced=False)),
        "advancedToolCount": len(expected_tool_names(advanced=True)),
        "domainCount": len(DOMAIN_SPECS),
        "skillRequiredToolCount": len(skill_required_tool_names()),
        "contractHash": contract_hash(),
    }


def build_payload() -> dict[str, Any]:
    target = _target()
    registry = bound_tool_registry()
    internal = _by_classification(ToolClassification.INTERNAL)
    compatibility = _by_classification(ToolClassification.COMPATIBILITY)
    deprecated = _by_classification(ToolClassification.DEPRECATED)
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
    if len(compatibility) != 11 or any(not spec.replacement_tools for spec in compatibility):
        errors.append("all eleven compatibility facades require replacement guidance")
    if len(deprecated) != 5 or any(not spec.replacement_tools for spec in deprecated):
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
                    "name": spec.name,
                    "replacementTools": list(spec.replacement_tools),
                }
                for spec in compatibility
            ],
            "deprecatedStubs": [
                {
                    "name": spec.name,
                    "replacementTools": list(spec.replacement_tools),
                    "transitionRelease": "4.0.0",
                }
                for spec in deprecated
            ],
            "ghostRegistrations": [],
        },
        "registrationEvidence": {
            "canonicalToolSpec": "src/hwpx_mcp_server/tool_contract.py",
            "fastMcpRegistration": "src/hwpx_mcp_server/server.py",
            "boundInstalledToolCount": len(registry.tools),
            "bindingHash": registry.binding_hash(),
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

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
