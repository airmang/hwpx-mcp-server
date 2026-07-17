from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import MappingProxyType

import pytest

from hwpx_mcp_server import server
from hwpx_mcp_server.handlers import workflow as workflow_handler
from hwpx_mcp_server.runtime_services import RUNTIME_SERVICES
from hwpx_mcp_server.tool_bindings import TOOL_BINDINGS
from hwpx_mcp_server.tool_contract import (
    TOOL_SPECS,
    expected_tool_order,
    validate_registered_tools,
)


PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "hwpx_mcp_server"
HANDLER_ROOT = PACKAGE_ROOT / "handlers"
HANDLER_OWNERS = {
    "hwpx_mcp_server.handlers.agent_document": 5,
    "hwpx_mcp_server.handlers.read_export": 21,
    "hwpx_mcp_server.handlers.authoring": 15,
    "hwpx_mcp_server.handlers.content_edit": 23,
    "hwpx_mcp_server.handlers.layout_style": 17,
    "hwpx_mcp_server.handlers.form_fill": 17,
    "hwpx_mcp_server.handlers.tracked_changes": 1,
    "hwpx_mcp_server.handlers.specialized": 10,
    "hwpx_mcp_server.handlers.quality_render": 16,
    "hwpx_mcp_server.handlers.workflow": 7,
}
ALLOWED_IMPORT_CYCLES = {
    frozenset(
        {
            "hwpx_mcp_server.workflow.render_queue",
            "hwpx_mcp_server.workflow.rendering",
        }
    )
}


def test_all_tool_specs_have_one_explicit_immutable_handler_owner() -> None:
    assert isinstance(TOOL_BINDINGS, MappingProxyType)
    assert tuple(TOOL_BINDINGS) == expected_tool_order(advanced=True)
    assert tuple(TOOL_BINDINGS) == tuple(spec.callable_name for spec in TOOL_SPECS)
    assert len(TOOL_BINDINGS) == 132
    assert len({id(function) for function in TOOL_BINDINGS.values()}) == 132
    assert Counter(function.__module__ for function in TOOL_BINDINGS.values()) == Counter(
        HANDLER_OWNERS
    )
    with pytest.raises(TypeError):
        TOOL_BINDINGS["get_document_info"] = TOOL_BINDINGS["get_document_info"]  # type: ignore[index]


def test_server_runtime_workflow_and_registry_share_canonical_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert server._SERVER_TOOL_BINDINGS is TOOL_BINDINGS
    assert RUNTIME_SERVICES.require_tool_bindings() is TOOL_BINDINGS
    for name, function in TOOL_BINDINGS.items():
        assert getattr(server, name) is function
    for bound in server._TOOL_REGISTRY.tools:
        assert bound.function is TOOL_BINDINGS[bound.spec.name]

    captured: dict[str, object] = {}

    class WorkflowServiceProbe:
        def __init__(self, namespace, **kwargs) -> None:
            captured["namespace"] = namespace
            captured["kwargs"] = kwargs

    monkeypatch.setattr(workflow_handler, "WorkflowService", WorkflowServiceProbe)
    workflow_handler._workflow_service()
    assert captured["namespace"] is TOOL_BINDINGS


def test_handlers_are_plain_owners_without_runtime_or_facade_back_edges() -> None:
    forbidden_modules = {
        "hwpx_mcp_server.server",
        "hwpx_mcp_server.runtime",
        "hwpx_mcp_server.hwpx_ops",
    }
    for path in HANDLER_ROOT.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert "HwpxOps" not in source
        assert not any(isinstance(node, (ast.AsyncFunctionDef,)) for node in tree.body)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level == 2:
                    imported = {f"hwpx_mcp_server.{module}"}
                elif node.level == 1:
                    imported = {f"hwpx_mcp_server.handlers.{module}"}
                else:
                    imported = {module}
            else:
                continue
            assert imported.isdisjoint(forbidden_modules), (path.name, imported)

    binding_tree = ast.parse(
        (PACKAGE_ROOT / "tool_bindings.py").read_text(encoding="utf-8")
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "globals"
        for node in ast.walk(binding_tree)
    )


def test_server_is_bounded_explicit_facade() -> None:
    source = (PACKAGE_ROOT / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert len(source.splitlines()) <= 320
    declared_functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    assert declared_functions <= {"_env_int", "_replace_ops", "main"}
    assert declared_functions.isdisjoint(TOOL_BINDINGS)
    assert max(
        len(path.read_text(encoding="utf-8").splitlines())
        for path in HANDLER_ROOT.glob("*.py")
    ) <= 1600


def _package_import_graph() -> dict[str, set[str]]:
    modules: dict[str, Path] = {}
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
        if path.name == "__init__.py":
            suffix = ".".join(relative.parent.parts)
        else:
            suffix = ".".join(relative.with_suffix("").parts)
        module = "hwpx_mcp_server" + (f".{suffix}" if suffix else "")
        modules[module] = path

    graph = {module: set() for module in modules}
    for module, path in modules.items():
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
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
            for target in targets:
                if target in modules:
                    graph[module].add(target)
    return graph


def _cyclic_components(graph: dict[str, set[str]]) -> set[frozenset[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: set[frozenset[str]] = set()

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
            cycles.add(frozenset(component))

    for module in graph:
        if module not in indices:
            visit(module)
    return cycles


def test_package_import_cycles_are_limited_to_frozen_render_pair() -> None:
    assert _cyclic_components(_package_import_graph()) == ALLOWED_IMPORT_CYCLES


def test_live_order_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import hwpx_mcp_server.tool_contract as tool_contract

    snapshots = dict(tool_contract.snapshot_runtime_tools(server.mcp))
    first_name = next(iter(snapshots))
    reordered = {name: item for name, item in snapshots.items() if name != first_name}
    reordered[first_name] = snapshots[first_name]
    monkeypatch.setattr(tool_contract, "snapshot_runtime_tools", lambda _mcp: reordered)

    report = validate_registered_tools(server.mcp, server._TOOL_REGISTRY)

    assert report["orderMismatch"] is True
    assert report["ok"] is False
    assert report["actualOrder"] != report["expectedOrder"]


def test_unbound_tool_contract_does_not_lazy_import_server() -> None:
    code = """
import sys
from hwpx_mcp_server.tool_contract import bound_tool_registry
try:
    bound_tool_registry()
except RuntimeError as exc:
    assert 'initialize hwpx_mcp_server.runtime first' in str(exc)
else:
    raise AssertionError('unbound registry unexpectedly succeeded')
assert 'hwpx_mcp_server.server' not in sys.modules
assert 'hwpx_mcp_server.runtime' not in sys.modules
"""
    env = dict(os.environ)
    source_root = str(PACKAGE_ROOT.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_root, env.get("PYTHONPATH", "")) if value
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PACKAGE_ROOT.parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
