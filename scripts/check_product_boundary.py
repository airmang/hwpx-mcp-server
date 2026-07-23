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
        "src/hwpx_mcp_server/handlers/specialized.py",
    }
)
CANONICAL_RENDER_BINDING = "src/hwpx_mcp_server/office/rendering.py"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def evaluate(root: Path) -> dict[str, Any]:
    violations: list[str] = []
    source = root / SOURCE_ROOT
    files = sorted(source.rglob("*.py"))

    if (root / "src" / "hwpx").exists():
        violations.append("MCP repository must not own or vendor src/hwpx")
    if not (root / CANONICAL_RENDER_BINDING).is_file():
        violations.append(f"missing canonical render binding: {CANONICAL_RENDER_BINDING}")

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
                violations.append(f"MCP imports skill implementation: {relative} -> {imported}")
            if (
                imported == "hwpx.visual.oracle"
                and relative != CANONICAL_RENDER_BINDING
                and relative not in LEGACY_DIRECT_RENDER_DISCOVERY
            ):
                violations.append(
                    f"new direct render discovery bypasses office adapter: {relative}"
                )

    return {
        "ok": not violations,
        "pythonFiles": len(files),
        "canonicalRenderBinding": CANONICAL_RENDER_BINDING,
        "legacyDirectRenderDiscovery": sorted(LEGACY_DIRECT_RENDER_DISCOVERY),
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    report = evaluate(args.root.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
