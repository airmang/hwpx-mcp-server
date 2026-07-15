#!/usr/bin/env python3
"""Render/check the versioned 3.0.0 public ToolSpec contract delta."""

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
    DOMAIN_SPECS,
    MIN_MCP_VERSION,
    MIN_PYTHON_HWPX,
    MIN_SKILL_VERSION,
    contract_hash,
    expected_tool_names,
    skill_required_tool_names,
)


DEFAULT_OUTPUT = ROOT / "docs" / "tool-contract-delta-3.0.0.json"
BASELINE: dict[str, Any] = {
    "versions": {
        "pythonHwpx": "2.29.2",
        "mcpServer": "2.23.1",
        "skill": "0.1.31",
    },
    "defaultToolCount": 133,
    "advancedToolCount": 143,
    "domainCount": 22,
    "skillRequiredToolCount": 32,
    "contractHash": "0f9e1dcb7c646202",
}
EXPECTED_TARGET: dict[str, Any] = {
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
REMOVALS: tuple[dict[str, Any], ...] = (
    {
        "name": "start_practice_scenario",
        "alias": None,
        "replacementKind": "none",
        "replacementTools": [],
    },
    {
        "name": "apply_practice_scenario",
        "alias": None,
        "replacementKind": "none",
        "replacementTools": [],
    },
    {
        "name": "start_practice_campaign",
        "alias": None,
        "replacementKind": "none",
        "replacementTools": [],
    },
    {
        "name": "get_practice_campaign",
        "alias": None,
        "replacementKind": "none",
        "replacementTools": [],
    },
    {
        "name": "continue_practice_campaign",
        "alias": None,
        "replacementKind": "none",
        "replacementTools": [],
    },
    {
        "name": "cancel_practice_campaign",
        "alias": None,
        "replacementKind": "none",
        "replacementTools": [],
    },
    {
        "name": "export_practice_campaign",
        "alias": None,
        "replacementKind": "none",
        "replacementTools": [],
    },
)
PUBLIC_DOCUMENT_WORK_ALTERNATIVES: tuple[str, ...] = (
    "apply_document_commands",
    "apply_evalplan_fill",
    "scan_form_guidance",
    "apply_table_ops",
    "apply_body_ops",
    "verify_form_fill",
)


def _runtime_reference_hits(removed_names: set[str]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    runtime_root = SRC / "hwpx_mcp_server"
    for path in sorted(runtime_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        names = sorted(name for name in removed_names if name in text)
        if names:
            hits.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "removedNames": names,
                }
            )
    return hits


def build_payload() -> dict[str, Any]:
    default_names = expected_tool_names(advanced=False)
    advanced_names = expected_tool_names(advanced=True)
    removed_names = {item["name"] for item in REMOVALS}
    replacement_names = set(PUBLIC_DOCUMENT_WORK_ALTERNATIVES)
    runtime_hits = _runtime_reference_hits(removed_names)
    runtime_package = ROOT / "src" / "hwpx_mcp_server" / "practice"
    target = {
        "versions": {
            "pythonHwpx": MIN_PYTHON_HWPX,
            "mcpServer": MIN_MCP_VERSION,
            "skill": MIN_SKILL_VERSION,
        },
        "defaultToolCount": len(default_names),
        "advancedToolCount": len(advanced_names),
        "domainCount": len(DOMAIN_SPECS),
        "skillRequiredToolCount": len(skill_required_tool_names()),
        "contractHash": contract_hash(),
    }

    errors: list[str] = []
    if target != EXPECTED_TARGET:
        errors.append(f"target contract drift: expected {EXPECTED_TARGET!r}, got {target!r}")
    if len(REMOVALS) != 7 or len(removed_names) != 7:
        errors.append("the versioned delta must contain exactly seven unique removals")
    if BASELINE["defaultToolCount"] - target["defaultToolCount"] != len(removed_names):
        errors.append("default count delta does not match the seven removed tools")
    if BASELINE["advancedToolCount"] - target["advancedToolCount"] != len(removed_names):
        errors.append("advanced count delta does not match the seven removed tools")
    if removed_names & advanced_names:
        errors.append(f"removed names remain active: {sorted(removed_names & advanced_names)!r}")
    if replacement_names - default_names:
        errors.append(
            f"replacement tools are not in the default contract: "
            f"{sorted(replacement_names - default_names)!r}"
        )
    if any(item["alias"] is not None for item in REMOVALS):
        errors.append("removed tools must not retain aliases")
    if any(item["replacementKind"] != "none" or item["replacementTools"] for item in REMOVALS):
        errors.append("removed private QA tools must not claim one-to-one public replacements")
    if runtime_package.exists():
        errors.append(f"removed runtime package still exists: {runtime_package.relative_to(ROOT)}")
    if runtime_hits:
        errors.append(f"removed tool call sites remain in runtime source: {runtime_hits!r}")
    if errors:
        raise RuntimeError("\n".join(errors))

    return {
        "schemaVersion": "hwpx.tool-contract-delta.v1",
        "release": "3.0.0",
        "changeKind": "breaking-removal",
        "baseline": BASELINE,
        "target": target,
        "delta": {
            "defaultToolCount": target["defaultToolCount"] - BASELINE["defaultToolCount"],
            "advancedToolCount": target["advancedToolCount"] - BASELINE["advancedToolCount"],
            "domainCount": target["domainCount"] - BASELINE["domainCount"],
            "skillRequiredToolCount": (
                target["skillRequiredToolCount"] - BASELINE["skillRequiredToolCount"]
            ),
        },
        "removedTools": list(REMOVALS),
        "compatibility": {
            "aliases": [],
            "deprecatedStubs": [],
            "ghostRegistrations": [],
        },
        "replacementGuidance": {
            "oneToOnePublicReplacement": False,
            "internalScenarioCampaignDestination": "workspace-private QA harness",
            "publicDocumentWorkAlternatives": list(PUBLIC_DOCUMENT_WORK_ALTERNATIVES),
        },
        "callSiteRuntimeRemovalEvidence": {
            "canonicalToolSpec": "src/hwpx_mcp_server/tool_contract.py",
            "fastMcpRegistration": "src/hwpx_mcp_server/server.py",
            "removedRuntimePackage": "src/hwpx_mcp_server/practice",
            "runtimePackagePresent": runtime_package.exists(),
            "remainingRuntimeReferences": runtime_hits,
            "activeRemovedNames": sorted(removed_names & advanced_names),
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
