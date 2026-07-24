# SPDX-License-Identifier: Apache-2.0
"""Machine-check the planned canonical form-fill owner boundary."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOUNDARY = runpy.run_path(str(ROOT / "scripts" / "check_product_boundary.py"))
OWNER = json.loads(
    (ROOT / "docs" / "architecture" / "form-fill-runtime-owner.json").read_text(
        encoding="utf-8"
    )
)


def test_owner_ledger_matches_frozen_source_and_contract() -> None:
    assert OWNER["source"] == {
        "head": "5054b50665296a0970823765d753de3d3ff612b5",
        "pythonFiles": 13,
        "loc": 5745,
        "manifestSha256": (
            "9fa54af13942549af74600a841117589fac4eb21c12c659c0b345c48746aae6e"
        ),
    }
    assert (
        tuple(OWNER["approvedCoreImports"])
        == BOUNDARY["ALLOWED_FORM_FILL_CORE_IMPORTS"]
    )
    assert (
        tuple(OWNER["forbiddenCoreCompatibilityImports"])
        == BOUNDARY["FROZEN_CORE_FORM_FILL_IMPORTS"]
    )
    assert OWNER["toolContract"] == {
        "default": 119,
        "advanced": 127,
        "skillRequired": 28,
        "hash": "429cb6706323e762",
    }


def test_canonical_owner_rejects_frozen_and_unapproved_core_seams() -> None:
    check = BOUNDARY["_form_fill_owner_import_violation"]
    canonical = "src/hwpx_mcp_server/office/form_fill/quality.py"
    assert check(canonical, "hwpx.formfill_quality") is not None
    assert check(canonical, "hwpx.visual.oracle") is not None
    assert check(canonical, "hwpx.table_patch") is None
    assert check(canonical, "hwpx.evalplan_fill") is None


def test_real_tree_declares_the_form_fill_boundary() -> None:
    report = BOUNDARY["evaluate"](ROOT)
    assert report["ok"], report["violations"]
    assert report["canonicalFormFillRoot"] == ("src/hwpx_mcp_server/office/form_fill")
    assert report["canonicalFormFillPythonFiles"] == 14
