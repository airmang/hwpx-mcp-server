from __future__ import annotations

import ast
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SMOKE_FILES = (
    "tests/test_form_fit_seal_placement.py",
    "tests/test_form_fit_wordbox.py",
    "tests/test_seal_mcp_e2e.py",
    "tests/test_visual_oracle.py",
)


def _mentions_opt_in(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant)
        and child.value == "HWPX_MAC_ORACLE_SMOKE"
        for child in ast.walk(node)
    )


def test_mac_oracle_smoke_decorators_short_circuit_on_opt_in_first() -> None:
    checked = 0
    for relative in SMOKE_FILES:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not _mentions_opt_in(
                    decorator
                ):
                    continue
                assert decorator.args
                condition = decorator.args[0]
                assert isinstance(condition, ast.UnaryOp)
                assert isinstance(condition.op, ast.Not)
                conjunction = condition.operand
                assert isinstance(conjunction, ast.BoolOp)
                assert isinstance(conjunction.op, ast.And)
                assert _mentions_opt_in(conjunction.values[0]), (
                    f"{relative}:{node.lineno} checks oracle availability "
                    "before the explicit opt-in"
                )
                checked += 1

    assert checked == 6


def test_quality_contract_has_no_collection_time_oracle_probe() -> None:
    source = (ROOT / "tests/test_quality_contract.py").read_text(encoding="utf-8")
    assert "_oracle_reachable" not in source


def test_default_suite_forces_structural_only_without_live_opt_in() -> None:
    if not os.environ.get("HWPX_MAC_ORACLE_SMOKE"):
        assert os.environ["HWPX_ORACLE_STRUCTURAL_ONLY"] == "1"
