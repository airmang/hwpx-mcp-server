# SPDX-License-Identifier: Apache-2.0
"""S-064 / M10 P4 — apply_table_ops / verify_form_fill MCP tools.

Skips until the installed python-hwpx provides hwpx.table_patch (the tools
degrade with a clear TABLE_OPS_UNAVAILABLE error otherwise — the version-skew
guard). Fixture is the in-repo license-cleared corpus form.
"""

from __future__ import annotations

import io
import os
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import hwpx
import hwpx.table_patch as table_patch

pytest.importorskip(
    "hwpx.table_patch", reason="requires python-hwpx with byte-preserving form-fill"
)

from hwpx.table_patch import _direct_cells, _iter_table_spans, build_grid
from hwpx_mcp_server.hwpx_ops import HwpxOperationError, HwpxOps
from hwpx_mcp_server import server

_CORE_REPO_PIN = os.environ.get("PYTHON_HWPX_REPO")
CORE_REPO = (
    Path(_CORE_REPO_PIN).expanduser().resolve()
    if _CORE_REPO_PIN
    else Path(hwpx.__file__).resolve().parents[2]
)
FIXT = CORE_REPO / "tests" / "fixtures" / "m2_corpus" / "public_official_table.hwpx"
if not FIXT.exists():
    FIXT = Path(__file__).parent / "fixtures" / "public_official_table.hwpx"


def test_tools_registered():
    names = set(server._fastmcp_tool_names())
    assert "apply_table_ops" in names
    assert "verify_form_fill" in names


@pytest.mark.skipif(not FIXT.exists(), reason="corpus fixture not available")
def test_apply_table_ops_fill_cell_preserves_bytes(tmp_path):
    shutil.copy(FIXT, tmp_path / "doc.hwpx")
    ops = HwpxOps(base_directory=tmp_path)
    out = ops.apply_table_ops(
        "doc.hwpx",
        [
            {
                "op": "fill_cell",
                "table_index": 0,
                "row": 0,
                "col": 0,
                "text": "MCP채움OK",
            }
        ],
    )
    assert out["ok"] is True
    assert len(out["applied"]) == 1
    assert out["changedParts"] == ["Contents/section0.xml"]
    assert out["openSafety"]["ok"] is True


@pytest.mark.skipif(not FIXT.exists(), reason="corpus fixture not available")
def test_apply_table_ops_prevents_squeeze_glyph_overlap(tmp_path):
    source = FIXT.read_bytes()
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        section_name = next(
            name for name in archive.namelist() if name.endswith("section0.xml")
        )
        section = archive.read(section_name)

    target = None
    for table_index, (start, end) in enumerate(_iter_table_spans(section)):
        table = section[start:end]
        for cell in _direct_cells(table):
            if b'lineWrap="SQUEEZE"' in table[cell.start : cell.end]:
                target = (table_index, cell.row, cell.col)
                break
        if target is not None:
            break
    assert target is not None

    shutil.copy(FIXT, tmp_path / "doc.hwpx")
    ops = HwpxOps(base_directory=tmp_path)
    table_index, row, col = target
    result = ops.apply_table_ops(
        "doc.hwpx",
        [
            {
                "op": "fill_cell",
                "table_index": table_index,
                "row": row,
                "col": col,
                "text": "긴 신규 검토 의견 " * 12,
            }
        ],
    )
    assert result["ok"] is True

    with zipfile.ZipFile(tmp_path / "doc.hwpx") as archive:
        changed_section = archive.read(section_name)
    spans = _iter_table_spans(changed_section)
    table = changed_section[spans[table_index][0] : spans[table_index][1]]
    changed_cell = build_grid(table)[0][(row, col)]
    changed_cell_bytes = table[changed_cell.start : changed_cell.end]
    assert b'lineWrap="BREAK"' in changed_cell_bytes
    assert b'lineWrap="SQUEEZE"' not in changed_cell_bytes


@pytest.mark.skipif(not FIXT.exists(), reason="corpus fixture not available")
def test_apply_table_ops_failclosed_out_of_range(tmp_path):
    shutil.copy(FIXT, tmp_path / "doc.hwpx")
    ops = HwpxOps(base_directory=tmp_path)
    out = ops.apply_table_ops(
        "doc.hwpx", [{"op": "delete_row", "table_index": 9999, "row": 0}]
    )
    assert out["byteIdentical"] is True
    assert out["skipped"] and "out of range" in out["skipped"][0]["reason"]


@pytest.mark.skipif(not FIXT.exists(), reason="corpus fixture not available")
def test_apply_table_ops_noop_materializes_distinct_output(tmp_path):
    source = tmp_path / "doc.hwpx"
    output = tmp_path / "copy.hwpx"
    shutil.copy(FIXT, source)
    ops = HwpxOps(base_directory=tmp_path)

    result = ops.apply_table_ops(
        "doc.hwpx",
        [],
        output="copy.hwpx",
    )

    assert result["byteIdentical"] is True
    assert result["openSafety"]["ok"] is True
    assert output.read_bytes() == source.read_bytes()


@pytest.mark.skipif(not FIXT.exists(), reason="corpus fixture not available")
def test_required_render_failure_precedes_destination_publication(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "doc.hwpx"
    output = tmp_path / "out.hwpx"
    shutil.copy(FIXT, source)
    source_before = source.read_bytes()
    ops = HwpxOps(base_directory=tmp_path)

    def fail_render(*args, **kwargs):
        return SimpleNamespace(
            render_checked=True,
            ok=False,
            overflow_detected=True,
            overlap_detected=False,
            page_count_changed=False,
            warnings=(),
            errors=("observed overflow",),
        )

    monkeypatch.setattr(table_patch, "verify_fill", fail_render)
    with pytest.raises(HwpxOperationError, match="required render detected"):
        ops.apply_table_ops(
            "doc.hwpx",
            [
                {
                    "op": "fill_cell",
                    "table_index": 0,
                    "row": 0,
                    "col": 0,
                    "text": "candidate-only",
                }
            ],
            output="out.hwpx",
            render_check="required",
        )

    assert source.read_bytes() == source_before
    assert not output.exists()

    optional = ops.apply_table_ops(
        "doc.hwpx",
        [
            {
                "op": "fill_cell",
                "table_index": 0,
                "row": 0,
                "col": 0,
                "text": "candidate-only",
            }
        ],
        output="out.hwpx",
        render_check="auto",
    )
    assert optional["ok"] is False
    assert optional["renderVerdict"]["renderChecked"] is True
    assert not output.exists()
