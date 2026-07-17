# SPDX-License-Identifier: Apache-2.0
"""S-081 render-cycle removal and SDK import boundary self-proof."""
from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
RATCHET_SCRIPT = ROOT / "scripts" / "check_architecture_ratchets.py"


def _fresh_import(statements: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", statements],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr


def test_eager_import_succeeds_in_both_orders() -> None:
    """Partial-initialization must be impossible whichever side loads first."""

    _fresh_import(
        "import hwpx_mcp_server.workflow.rendering as r; "
        "import hwpx_mcp_server.workflow.render_queue as q; "
        "assert r.sign_submission is q.sign_submission"
    )
    _fresh_import(
        "import hwpx_mcp_server.workflow.render_queue as q; "
        "import hwpx_mcp_server.workflow.rendering as r; "
        "assert r.RenderJobV2 is q.RenderJobV2"
    )


def test_leaf_module_has_no_package_dependencies() -> None:
    """render_contracts must stay a leaf: stdlib + pydantic only."""

    namespace = runpy.run_path(str(RATCHET_SCRIPT))
    graph = namespace["_package_import_graph"]()
    leaf = "hwpx_mcp_server.workflow.render_contracts"
    assert graph[leaf] == set()


def test_compatibility_reexports_are_the_same_objects() -> None:
    from hwpx_mcp_server.workflow import render_contracts, render_queue, rendering

    for name in ("RenderJobV2", "RenderReceiptV2", "RenderStatus", "sign_submission"):
        assert getattr(rendering, name) is getattr(render_contracts, name)
        assert getattr(render_queue, name) is getattr(render_contracts, name)


def test_package_cycle_baseline_is_zero() -> None:
    namespace = runpy.run_path(str(RATCHET_SCRIPT))
    assert namespace["EXPECTED_PACKAGE_CYCLES"] == ()
    assert namespace["_cyclic_components"](namespace["_package_import_graph"]()) == ()


def test_current_sdk_importers_match_allowlist() -> None:
    namespace = runpy.run_path(str(RATCHET_SCRIPT))
    assert namespace["_sdk_importers"]() == namespace["EXPECTED_SDK_IMPORTERS"]


def test_sdk_import_outside_allowlist_fails_the_ratchet(tmp_path) -> None:
    """Self-proof: a new module importing the mcp SDK trips the real gate."""

    namespace = runpy.run_path(str(RATCHET_SCRIPT))
    rogue = tmp_path / "rogue.py"
    rogue.write_text("import mcp\n", encoding="utf-8")

    augmented = dict(namespace["_package_modules"]())
    augmented["hwpx_mcp_server.rogue"] = rogue
    # Patch the census inside the live globals the gate functions actually
    # use (runpy returns a copy of the namespace dict).
    namespace["_sdk_importers"].__globals__["_package_modules"] = lambda: augmented

    importers = namespace["_sdk_importers"]()
    assert "hwpx_mcp_server.rogue" in importers
    with pytest.raises(RuntimeError, match="sdk_importers"):
        namespace["assert_ratchets"]()
