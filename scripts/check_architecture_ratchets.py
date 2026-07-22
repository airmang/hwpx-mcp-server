#!/usr/bin/env python3
"""Fail when the P3 architecture baseline changes without an explicit review."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from pprint import pformat
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPOSITORY_ROOT / "src" / "hwpx_mcp_server"
SERVICES_ROOT = PACKAGE_ROOT / "ops_services"

# S-081 removed the last cycle (render contracts extracted to a leaf module);
# the baseline is now exactly zero and must stay there.
EXPECTED_PACKAGE_CYCLES: tuple[tuple[str, ...], ...] = ()

# Modules allowed to import the mcp SDK at all. Everything else must reach the
# SDK through these seams (the audited adapter owns every private access), so a
# novel private-internal dependency cannot appear outside this list unnoticed.
EXPECTED_SDK_IMPORTERS = (
    "hwpx_mcp_server.fastmcp_adapter",
    "hwpx_mcp_server.handlers.quality_render",
    "hwpx_mcp_server.runtime",
    "hwpx_mcp_server.runtime_services",
)

EXPECTED_SERVICE_LINES = {
    "_border_fill.py": 283,
    "content_layout.py": 322,
    "context.py": 213,
    "form_fields.py": 540,
    "media.py": 178,
    "memo_style.py": 477,
    "package_validation.py": 166,
    "planning.py": 201,
    "preview_export.py": 583,
    "read_query.py": 601,
    "save_policy.py": 604,
    "tables.py": 539,
    "transactions.py": 616,
}

EXPECTED_FACADE_LINES = {
    "hwpx_ops.py": 1439,
    "server.py": 267,
}

PRIVATE_ATTRIBUTES = ("_mcp_server", "_tool_manager", "_tools")

EXPECTED_PRIVATE_ACCESSES = (
    (
        "src/hwpx_mcp_server/fastmcp_adapter.py",
        "_mcp_server",
        'runtime = getattr(mcp, "_mcp_server", None)',
    ),
    (
        "src/hwpx_mcp_server/fastmcp_adapter.py",
        "_tool_manager",
        'manager = getattr(mcp, "_tool_manager", None)',
    ),
    (
        "src/hwpx_mcp_server/fastmcp_adapter.py",
        "_tools",
        'tools = getattr(manager, "_tools", None)',
    ),
    (
        "src/hwpx_mcp_server/fastmcp_adapter.py",
        "request_handlers",
        "previous_handler = request_handlers[mcp_types.CallToolRequest]",
    ),
    (
        "src/hwpx_mcp_server/fastmcp_adapter.py",
        "request_handlers",
        "request_handlers[mcp_types.CallToolRequest] = previous_handler",
    ),
    (
        "src/hwpx_mcp_server/fastmcp_adapter.py",
        "request_handlers",
        "request_handlers[mcp_types.CallToolRequest] = strict_call_handler",
    ),
)

C901_PATHS = (
    "src/hwpx_mcp_server/handlers",
    "src/hwpx_mcp_server/ops_services",
    "src/hwpx_mcp_server/runtime.py",
    "src/hwpx_mcp_server/runtime_services.py",
    "src/hwpx_mcp_server/fastmcp_adapter.py",
    "src/hwpx_mcp_server/tool_bindings.py",
    "src/hwpx_mcp_server/server.py",
    "src/hwpx_mcp_server/hwpx_ops.py",
    "src/hwpx_mcp_server/form_fill.py",
    "src/hwpx_mcp_server/tool_contract.py",
    "src/hwpx_mcp_server/workflow/service.py",
)

EXPECTED_C901 = (
    ("src/hwpx_mcp_server/fastmcp_adapter.py", "_normalize_implicit_none_parameters", 13),
    ("src/hwpx_mcp_server/fastmcp_adapter.py", "_register", 19),
    ("src/hwpx_mcp_server/fastmcp_adapter.py", "normalize_schema", 12),
    ("src/hwpx_mcp_server/form_fill.py", "_build_mapping_analysis", 12),
    ("src/hwpx_mcp_server/form_fill.py", "_canonical_input_from_docx", 13),
    ("src/hwpx_mcp_server/form_fill.py", "apply_form_fill_workflow", 12),
    ("src/hwpx_mcp_server/handlers/content_edit.py", "_apply_edit_operation", 20),
    ("src/hwpx_mcp_server/handlers/content_edit.py", "replace_in_paragraph", 14),
    ("src/hwpx_mcp_server/handlers/read_export.py", "_build_read_model", 15),
    ("src/hwpx_mcp_server/handlers/tracked_changes.py", "_validate_tracked_edits", 19),
    ("src/hwpx_mcp_server/handlers/tracked_changes.py", "add_tracked_edit", 12),
    ("src/hwpx_mcp_server/ops_services/form_fields.py", "apply_evalplan_fill", 12),
    ("src/hwpx_mcp_server/ops_services/memo_style.py", "_split_run", 12),
    ("src/hwpx_mcp_server/ops_services/preview_export.py", "render_preview", 16),
    ("src/hwpx_mcp_server/ops_services/read_query.py", "analyze_template_structure", 13),
    ("src/hwpx_mcp_server/ops_services/read_query.py", "find", 20),
    ("src/hwpx_mcp_server/ops_services/read_query.py", "get_paragraphs", 11),
    ("src/hwpx_mcp_server/ops_services/read_query.py", "read_text", 12),
    ("src/hwpx_mcp_server/ops_services/save_policy.py", "_rotate_and_backup_exact", 12),
    ("src/hwpx_mcp_server/ops_services/tables.py", "_auto_fit_table_columns", 16),
    ("src/hwpx_mcp_server/ops_services/transactions.py", "_apply_transaction_operation", 20),
    ("src/hwpx_mcp_server/ops_services/transactions.py", "byte_preserving_patch", 23),
    ("src/hwpx_mcp_server/ops_services/transactions.py", "undo_last_edit", 20),
    ("src/hwpx_mcp_server/runtime.py", "_classified_error_payload", 11),
    ("src/hwpx_mcp_server/runtime.py", "_strict_call_tool_handler", 11),
    ("src/hwpx_mcp_server/workflow/service.py", "continue_workflow", 22),
)


def _package_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
        if path.name == "__init__.py":
            suffix = ".".join(relative.parent.parts)
        else:
            suffix = ".".join(relative.with_suffix("").parts)
        module = "hwpx_mcp_server" + (f".{suffix}" if suffix else "")
        modules[module] = path
    return modules


def _package_import_graph() -> dict[str, set[str]]:
    modules = _package_modules()
    graph = {module: set() for module in modules}
    for module, path in modules.items():
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = importlib.util.resolve_name(
                        "." * node.level + (node.module or ""), package
                    )
                else:
                    base = node.module or ""
                targets = [base]
                if node.module is None:
                    targets.extend(f"{base}.{alias.name}" for alias in node.names)
            graph[module].update(target for target in targets if target in modules)
    return graph


def _cyclic_components(graph: dict[str, set[str]]) -> tuple[tuple[str, ...], ...]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[tuple[str, ...]] = []

    def visit(module: str) -> None:
        nonlocal index
        indices[module] = lowlinks[module] = index
        index += 1
        stack.append(module)
        on_stack.add(module)
        for dependency in graph[module]:
            if dependency not in indices:
                visit(dependency)
                lowlinks[module] = min(lowlinks[module], lowlinks[dependency])
            elif dependency in on_stack:
                lowlinks[module] = min(lowlinks[module], indices[dependency])
        if lowlinks[module] != indices[module]:
            return
        component: set[str] = set()
        while True:
            item = stack.pop()
            on_stack.remove(item)
            component.add(item)
            if item == module:
                break
        if len(component) > 1 or module in graph[module]:
            cycles.append(tuple(sorted(component)))

    for module in graph:
        if module not in indices:
            visit(module)
    return tuple(sorted(cycles))


def _line_counts(root: Path, names: tuple[str, ...]) -> dict[str, int]:
    return {
        name: len((root / name).read_text(encoding="utf-8").splitlines())
        for name in names
    }


def _private_accesses() -> tuple[tuple[str, str, str], ...]:
    accesses: list[tuple[str, str, str]] = []
    for root in (PACKAGE_ROOT, REPOSITORY_ROOT / "tests"):
        for path in root.rglob("*.py"):
            source_lines = path.read_text(encoding="utf-8").splitlines()
            tree = ast.parse("\n".join(source_lines), filename=str(path))
            for node in ast.walk(tree):
                marker: str | None = None
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in PRIVATE_ATTRIBUTES
                ):
                    marker = str(node.args[1].value)
                elif isinstance(node, ast.Attribute) and node.attr in PRIVATE_ATTRIBUTES:
                    marker = node.attr
                elif (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "request_handlers"
                ):
                    marker = "request_handlers"
                if marker is not None:
                    accesses.append(
                        (
                            path.relative_to(REPOSITORY_ROOT).as_posix(),
                            marker,
                            source_lines[node.lineno - 1].strip(),
                        )
                    )
    return tuple(sorted(accesses))


def _sdk_importers() -> tuple[str, ...]:
    """Package modules that import the ``mcp`` SDK (any form, any depth)."""

    importers: set[str] = set()
    for module, path in _package_modules().items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(
                    alias.name == "mcp" or alias.name.startswith("mcp.")
                    for alias in node.names
                ):
                    importers.add(module)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                base = node.module or ""
                if base == "mcp" or base.startswith("mcp."):
                    importers.add(module)
    return tuple(sorted(importers))


def _c901_diagnostics() -> tuple[tuple[str, str, int], ...]:
    completed = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "C901", "--output-format", "json", *C901_PATHS],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in {0, 1}:
        raise RuntimeError(f"ruff C901 collection failed:\n{completed.stderr}")
    diagnostics = json.loads(completed.stdout or "[]")
    result: list[tuple[str, str, int]] = []
    for diagnostic in diagnostics:
        match = re.fullmatch(r"`([^`]+)` is too complex \((\d+) > \d+\)", diagnostic["message"])
        if match is None:
            raise RuntimeError(f"unexpected Ruff C901 message: {diagnostic['message']!r}")
        relative = Path(diagnostic["filename"]).resolve().relative_to(REPOSITORY_ROOT)
        result.append((relative.as_posix(), match.group(1), int(match.group(2))))
    return tuple(sorted(result))


EXPECTED_RATCHETS: dict[str, Any] = {
    "package_cycles": EXPECTED_PACKAGE_CYCLES,
    "sdk_importers": EXPECTED_SDK_IMPORTERS,
    "service_lines": EXPECTED_SERVICE_LINES,
    "facade_lines": EXPECTED_FACADE_LINES,
    "private_accesses": EXPECTED_PRIVATE_ACCESSES,
    "c901": EXPECTED_C901,
}


def capture_ratchets() -> dict[str, Any]:
    return {
        "package_cycles": _cyclic_components(_package_import_graph()),
        "sdk_importers": _sdk_importers(),
        "service_lines": _line_counts(
            SERVICES_ROOT, tuple(sorted(EXPECTED_SERVICE_LINES))
        ),
        "facade_lines": _line_counts(
            PACKAGE_ROOT, tuple(sorted(EXPECTED_FACADE_LINES))
        ),
        "private_accesses": _private_accesses(),
        "c901": _c901_diagnostics(),
    }


def assert_ratchets() -> None:
    actual = capture_ratchets()
    mismatches = {
        name: {"expected": EXPECTED_RATCHETS[name], "actual": actual[name]}
        for name in EXPECTED_RATCHETS
        if actual[name] != EXPECTED_RATCHETS[name]
    }
    if mismatches:
        raise RuntimeError("architecture ratchet drift:\n" + pformat(mismatches))


def main() -> int:
    try:
        assert_ratchets()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("Architecture ratchets match the exact P3 baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
