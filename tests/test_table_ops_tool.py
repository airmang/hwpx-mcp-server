# SPDX-License-Identifier: Apache-2.0
"""S-064 / M10 P4 — apply_table_ops / verify_form_fill MCP tools.

Skips until the installed python-hwpx provides hwpx.table_patch (the tools
degrade with a clear TABLE_OPS_UNAVAILABLE error otherwise — the version-skew
guard). Fixture is the in-repo license-cleared corpus form.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("hwpx.table_patch", reason="requires python-hwpx with byte-preserving form-fill")

from hwpx_mcp_server.hwpx_ops import HwpxOps
from hwpx_mcp_server.tools import build_tool_definitions

FIXT = Path(__file__).parent.parent.parent / "python-hwpx-s064" / "tests" / "fixtures" / "m2_corpus" / "public_official_table.hwpx"
if not FIXT.exists():
    FIXT = Path(__file__).parent / "fixtures" / "public_official_table.hwpx"


def test_tools_registered():
    names = {t.name for t in build_tool_definitions()}
    assert "apply_table_ops" in names
    assert "verify_form_fill" in names


@pytest.mark.skipif(not FIXT.exists(), reason="corpus fixture not available")
def test_apply_table_ops_fill_cell_preserves_bytes(tmp_path):
    shutil.copy(FIXT, tmp_path / "doc.hwpx")
    ops = HwpxOps(base_directory=tmp_path)
    out = ops.apply_table_ops("doc.hwpx", [{"op": "fill_cell", "table_index": 0, "row": 0, "col": 0, "text": "MCP채움OK"}])
    assert out["ok"] is True
    assert len(out["applied"]) == 1
    assert out["changedParts"] == ["Contents/section0.xml"]
    assert out["openSafety"]["ok"] is True


@pytest.mark.skipif(not FIXT.exists(), reason="corpus fixture not available")
def test_apply_table_ops_failclosed_out_of_range(tmp_path):
    shutil.copy(FIXT, tmp_path / "doc.hwpx")
    ops = HwpxOps(base_directory=tmp_path)
    out = ops.apply_table_ops("doc.hwpx", [{"op": "delete_row", "table_index": 9999, "row": 0}])
    assert out["byteIdentical"] is True
    assert out["skipped"] and "out of range" in out["skipped"][0]["reason"]
