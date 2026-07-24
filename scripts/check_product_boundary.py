#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check MCP's application-layer ownership and dependency direction."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

SOURCE_ROOT = "src/hwpx_mcp_server"
FORBIDDEN_IMPORTS = ("hwpx_skill",)
LEGACY_DIRECT_RENDER_DISCOVERY = frozenset(
    {
        "src/hwpx_mcp_server/handlers/layout_style.py",
    }
)
CANONICAL_RENDER_BINDING = "src/hwpx_mcp_server/office/rendering.py"
CANONICAL_AGENT_ROOT = "src/hwpx_mcp_server/office/agent"
CANONICAL_AGENT_FILE_COUNT = 19
CANONICAL_AUTHORING_ROOT = "src/hwpx_mcp_server/office/authoring"
CANONICAL_AUTHORING_FILE_COUNT = 16
ALLOWED_AGENT_CORE_IMPORTS = (
    "hwpx.document",
    "hwpx.mutation_report",
    "hwpx.oxml",
    "hwpx.quality",
    "hwpx.table_patch",
    "hwpx.tools.package_validator",
)
ALLOWED_AUTHORING_CORE_IMPORTS = (
    "hwpx.document",
    "hwpx.opc.package",
    "hwpx.opc.relationships",
    "hwpx.oxml.namespaces",
    "hwpx.quality",
    "hwpx.tools.archive_cli",
    "hwpx.tools.id_integrity",
    "hwpx.tools.idempotence",
    "hwpx.tools.package_reconcile",
    "hwpx.tools.package_validator",
    "hwpx.tools.report_utils",
    "hwpx.tools.table_cleanup",
    "hwpx.tools.toc_author",
    "hwpx.tools.validator",
)
TEMPORARY_AUTHORING_CORE_IMPORTS = (
    "hwpx.tools.mail_merge",
    "hwpx.visual",
)
FROZEN_CORE_AUTHORING_IMPORTS = (
    "hwpx.authoring",
    "hwpx.builder",
    "hwpx.design",
    "hwpx.presets",
    "hwpx.tools.advanced_generators",
    "hwpx.tools.style_profile",
    "hwpx.tools.template_analyzer",
)
CANONICAL_POLICY_ROOTS = {
    "src/hwpx_mcp_server/office/compliance": ("hwpx.document",),
    "src/hwpx_mcp_server/office/quality": ("hwpx",),
    "src/hwpx_mcp_server/office/utilities": (),
}
FROZEN_CORE_POLICY_IMPORTS = (
    "hwpx.tools.official_lint",
    "hwpx.tools.pii",
    "hwpx.tools.page_guard",
    "hwpx.tools.table_compute",
)
CANONICAL_FORM_FILL_ROOT = "src/hwpx_mcp_server/office/form_fill"
CANONICAL_FORM_FILL_FILE_COUNT = 14
ALLOWED_FORM_FILL_CORE_IMPORTS = (
    "hwpx.document",
    "hwpx.evalplan_fill",
    "hwpx.oxml.namespaces",
    "hwpx.quality",
    "hwpx.table_patch",
    "hwpx.tools.package_validator",
    "hwpx.tools.validator",
)
TEMPORARY_FORM_FILL_CORE_IMPORTS = (
    # S-102 moves the eval-plan parser; S-101 may consume it but cannot enlarge it.
    "hwpx.evalplan_fill",
)
FROZEN_CORE_FORM_FILL_IMPORTS = (
    "hwpx.fill_residue",
    "hwpx.form_fill",
    "hwpx.form_fit",
    "hwpx.formfill_quality",
    "hwpx.guidance_scan",
    "hwpx.template_formfit",
)


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _policy_owner_import_violation(
    relative: str,
    imported: str,
) -> str | None:
    for root, allowed_imports in CANONICAL_POLICY_ROOTS.items():
        if relative == f"{root}.py" or relative.startswith(f"{root}/"):
            if any(
                imported == frozen or imported.startswith(f"{frozen}.")
                for frozen in FROZEN_CORE_POLICY_IMPORTS
            ):
                return (
                    "canonical policy owner imports frozen core compatibility "
                    f"copy: {relative} -> {imported}"
                )
            if imported == "hwpx" or imported.startswith("hwpx."):
                if not any(
                    imported == allowed
                    or (allowed != "hwpx" and imported.startswith(f"{allowed}."))
                    for allowed in allowed_imports
                ):
                    return (
                        "canonical policy owner uses unapproved core seam: "
                        f"{relative} -> {imported}"
                    )
            return None
    return None


def _form_fill_owner_import_violation(
    relative: str,
    imported: str,
) -> str | None:
    if not (
        relative == f"{CANONICAL_FORM_FILL_ROOT}.py"
        or relative.startswith(f"{CANONICAL_FORM_FILL_ROOT}/")
    ):
        return None
    if any(
        imported == frozen or imported.startswith(f"{frozen}.")
        for frozen in FROZEN_CORE_FORM_FILL_IMPORTS
    ):
        return (
            "canonical form-fill owner imports frozen core compatibility "
            f"copy: {relative} -> {imported}"
        )
    if imported == "hwpx" or imported.startswith("hwpx."):
        if not any(
            imported == allowed or imported.startswith(f"{allowed}.")
            for allowed in ALLOWED_FORM_FILL_CORE_IMPORTS
        ):
            return (
                "canonical form-fill owner uses unapproved core seam: "
                f"{relative} -> {imported}"
            )
    return None


def evaluate(root: Path) -> dict[str, Any]:
    violations: list[str] = []
    source = root / SOURCE_ROOT
    files = sorted(source.rglob("*.py"))

    if (root / "src" / "hwpx").exists():
        violations.append("MCP repository must not own or vendor src/hwpx")
    if not (root / CANONICAL_RENDER_BINDING).is_file():
        violations.append(
            f"missing canonical render binding: {CANONICAL_RENDER_BINDING}"
        )
    agent_files = sorted((root / CANONICAL_AGENT_ROOT).rglob("*.py"))
    if len(agent_files) != CANONICAL_AGENT_FILE_COUNT:
        violations.append(
            "canonical agent owner must contain exactly "
            f"{CANONICAL_AGENT_FILE_COUNT} Python files: {CANONICAL_AGENT_ROOT}"
        )
    authoring_files = sorted((root / CANONICAL_AUTHORING_ROOT).rglob("*.py"))
    if len(authoring_files) != CANONICAL_AUTHORING_FILE_COUNT:
        violations.append(
            "canonical authoring owner must contain exactly "
            f"{CANONICAL_AUTHORING_FILE_COUNT} Python files: "
            f"{CANONICAL_AUTHORING_ROOT}"
        )
    form_fill_files = sorted((root / CANONICAL_FORM_FILL_ROOT).rglob("*.py"))
    if len(form_fill_files) != CANONICAL_FORM_FILL_FILE_COUNT:
        violations.append(
            "canonical form-fill owner must contain exactly "
            f"{CANONICAL_FORM_FILL_FILE_COUNT} Python files: "
            f"{CANONICAL_FORM_FILL_ROOT}"
        )

    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            imports = _imports(path)
        except (OSError, SyntaxError) as exc:
            violations.append(f"could not inspect {relative}: {exc}")
            continue
        for imported in imports:
            if any(
                imported == prefix or imported.startswith(f"{prefix}.")
                for prefix in FORBIDDEN_IMPORTS
            ):
                violations.append(
                    f"MCP imports skill implementation: {relative} -> {imported}"
                )
            if (
                imported == "hwpx.visual.oracle"
                and relative != CANONICAL_RENDER_BINDING
                and relative not in LEGACY_DIRECT_RENDER_DISCOVERY
            ):
                violations.append(
                    f"new direct render discovery bypasses office adapter: {relative}"
                )
            if imported == "hwpx.agent" or imported.startswith("hwpx.agent."):
                violations.append(
                    f"MCP production imports frozen core agent copy: {relative} -> {imported}"
                )
            if any(
                imported == frozen or imported.startswith(f"{frozen}.")
                for frozen in FROZEN_CORE_AUTHORING_IMPORTS
            ):
                violations.append(
                    "MCP production imports frozen core authoring copy: "
                    f"{relative} -> {imported}"
                )
            if any(
                imported == frozen or imported.startswith(f"{frozen}.")
                for frozen in FROZEN_CORE_POLICY_IMPORTS
            ):
                violations.append(
                    "MCP production imports frozen core policy copy: "
                    f"{relative} -> {imported}"
                )
            if any(
                imported == frozen or imported.startswith(f"{frozen}.")
                for frozen in FROZEN_CORE_FORM_FILL_IMPORTS
            ):
                violations.append(
                    "MCP production imports frozen core form-fill copy: "
                    f"{relative} -> {imported}"
                )
            if relative.startswith(f"{CANONICAL_AGENT_ROOT}/") and (
                imported == "hwpx" or imported.startswith("hwpx.")
            ):
                if not any(
                    imported == allowed or imported.startswith(f"{allowed}.")
                    for allowed in ALLOWED_AGENT_CORE_IMPORTS
                ):
                    violations.append(
                        f"canonical agent uses unapproved core seam: {relative} -> {imported}"
                    )
            if relative.startswith(f"{CANONICAL_AUTHORING_ROOT}/") and (
                imported == "hwpx" or imported.startswith("hwpx.")
            ):
                approved = (
                    ALLOWED_AUTHORING_CORE_IMPORTS + TEMPORARY_AUTHORING_CORE_IMPORTS
                )
                if not any(
                    imported == allowed or imported.startswith(f"{allowed}.")
                    for allowed in approved
                ):
                    violations.append(
                        "canonical authoring uses unapproved core seam: "
                        f"{relative} -> {imported}"
                    )
            policy_violation = _policy_owner_import_violation(
                relative,
                imported,
            )
            if policy_violation is not None:
                violations.append(policy_violation)
            form_fill_violation = _form_fill_owner_import_violation(
                relative,
                imported,
            )
            if form_fill_violation is not None:
                violations.append(form_fill_violation)

    return {
        "ok": not violations,
        "pythonFiles": len(files),
        "canonicalRenderBinding": CANONICAL_RENDER_BINDING,
        "canonicalAgentRoot": CANONICAL_AGENT_ROOT,
        "canonicalAgentPythonFiles": len(agent_files),
        "allowedAgentCoreImports": list(ALLOWED_AGENT_CORE_IMPORTS),
        "canonicalAuthoringRoot": CANONICAL_AUTHORING_ROOT,
        "canonicalAuthoringPythonFiles": len(authoring_files),
        "allowedAuthoringCoreImports": list(ALLOWED_AUTHORING_CORE_IMPORTS),
        "temporaryAuthoringCoreImports": list(TEMPORARY_AUTHORING_CORE_IMPORTS),
        "frozenCoreAuthoringImports": list(FROZEN_CORE_AUTHORING_IMPORTS),
        "canonicalPolicyRoots": {
            root: list(imports) for root, imports in CANONICAL_POLICY_ROOTS.items()
        },
        "frozenCorePolicyImports": list(FROZEN_CORE_POLICY_IMPORTS),
        "canonicalFormFillRoot": CANONICAL_FORM_FILL_ROOT,
        "canonicalFormFillPythonFiles": len(form_fill_files),
        "allowedFormFillCoreImports": list(ALLOWED_FORM_FILL_CORE_IMPORTS),
        "temporaryFormFillCoreImports": list(TEMPORARY_FORM_FILL_CORE_IMPORTS),
        "frozenCoreFormFillImports": list(FROZEN_CORE_FORM_FILL_IMPORTS),
        "legacyDirectRenderDiscovery": sorted(LEGACY_DIRECT_RENDER_DISCOVERY),
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    report = evaluate(args.root.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
