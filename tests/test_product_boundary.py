# SPDX-License-Identifier: Apache-2.0
"""Positive and negative fixtures for the MCP ownership check."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_product_boundary.py"
SPEC = importlib.util.spec_from_file_location("check_mcp_product_boundary", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
boundary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boundary)


def _minimal_tree(root: Path) -> Path:
    office = root / "src" / "hwpx_mcp_server" / "office"
    office.mkdir(parents=True)
    (office / "rendering.py").write_text("VALUE = 1\n", encoding="utf-8")
    return root / "src" / "hwpx_mcp_server"


def test_real_tree_satisfies_mcp_boundary() -> None:
    report = boundary.evaluate(ROOT)
    assert report["ok"], report["violations"]


def test_new_direct_render_discovery_fails_closed(tmp_path) -> None:
    source = _minimal_tree(tmp_path)
    handler = source / "handlers" / "new_handler.py"
    handler.parent.mkdir()
    handler.write_text(
        "from hwpx.visual.oracle import resolve_oracle\n",
        encoding="utf-8",
    )

    report = boundary.evaluate(tmp_path)

    assert not report["ok"]
    assert any("bypasses office adapter" in item for item in report["violations"])


def test_skill_implementation_import_fails_closed(tmp_path) -> None:
    source = _minimal_tree(tmp_path)
    module = source / "office" / "bad.py"
    module.write_text("import hwpx_skill\n", encoding="utf-8")

    report = boundary.evaluate(tmp_path)

    assert not report["ok"]
    assert any("imports skill implementation" in item for item in report["violations"])
