# SPDX-License-Identifier: Apache-2.0
"""scan_form_guidance MCP tool — universal form-fill Stage 1 recon surface.

Uses the in-repo public blank evaluation-plan form fixture (no owner PII).
Skips until the installed python-hwpx provides hwpx.guidance_scan.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("hwpx.guidance_scan", reason="requires python-hwpx with guidance_scan")

from hwpx_mcp_server.hwpx_ops import HwpxOps
from hwpx_mcp_server.tools import build_tool_definitions

BLANK = (Path(__file__).parent.parent.parent / "python-hwpx" / "tests"
         / "fixtures" / "m105_evalplan" / "blank_form_3hak.hwpx")


@pytest.fixture()
def ops(tmp_path):
    import shutil

    shutil.copy(BLANK, tmp_path / "blank.hwpx")
    return HwpxOps(base_directory=tmp_path)


def test_tool_is_registered():
    names = {d.name for d in build_tool_definitions()}
    assert "scan_form_guidance" in names


def test_scan_reports_legend_and_candidates(ops):
    out = ops.scan_form_guidance("blank.hwpx")
    actions = {b["family"]: b["action"] for b in out["legend"]}
    assert actions.get("red") == "delete"
    assert actions.get("blue") == "modify"
    assert out["deleteCandidatesTotal"] >= 20
    assert out["emptyCellTotal"] >= 50
    assert any(
        "**과목" in c["textPreview"] for c in out["placeholderCandidates"]
    )
    assert out["conditionalChoices"], "조건부 선택 블록 미탐지"
    assert "지울 것" in out["markdownReport"]


def test_scan_is_non_mutating(ops, tmp_path):
    before = (tmp_path / "blank.hwpx").read_bytes()
    ops.scan_form_guidance("blank.hwpx")
    assert (tmp_path / "blank.hwpx").read_bytes() == before


def test_max_items_caps_lists(ops):
    out = ops.scan_form_guidance("blank.hwpx", max_items=5)
    assert len(out["deleteCandidates"]) == 5
    assert out["deleteCandidatesTotal"] >= 20  # 총계는 그대로 정직 보고
    assert len(out["emptyCellCandidates"]) == 5


def test_apply_table_ops_dry_run_writes_nothing(ops, tmp_path):
    before = (tmp_path / "blank.hwpx").read_bytes()
    out = ops.apply_table_ops(
        "blank.hwpx",
        [{"op": "delete_table", "tableIndex": 5},
         {"op": "fill_cell", "tableIndex": 2, "row": 5, "col": 3, "text": "미리보기"}],
        output="would_be.hwpx",
        dry_run=True,
    )
    assert out["dryRun"] is True and out["outputPath"] is None
    assert out["transcript"][0]["status"] == "would_apply"
    assert "→" in out["transcript"][0]["dims"]
    assert (tmp_path / "blank.hwpx").read_bytes() == before
    assert not (tmp_path / "would_be.hwpx").exists()


def test_apply_body_ops_replace_and_dry_run(ops, tmp_path):
    before = (tmp_path / "blank.hwpx").read_bytes()
    out = ops.apply_body_ops(
        "blank.hwpx",
        [{"op": "replace_text", "find": "성취수준별 고정분할점수(5단계)", "replace": "[1] 성취수준별 고정분할점수(5단계)"}],
        dry_run=True,
    )
    assert out["dryRun"] is True and out["outputPath"] is None
    assert out["transcript"][0]["status"] == "would_apply" and out["transcript"][0]["hits"] == 1
    assert (tmp_path / "blank.hwpx").read_bytes() == before
    wet = ops.apply_body_ops(
        "blank.hwpx",
        [{"op": "replace_text", "find": "성취수준별 고정분할점수(5단계)", "replace": "X단계"}],
        output="body_out.hwpx",
    )
    assert wet["ok"] and (tmp_path / "body_out.hwpx").exists()


def test_inspect_fill_residue_gate(ops):
    out = ops.inspect_fill_residue("blank.hwpx", blank_path="blank.hwpx")
    assert out["ok"] is False  # blank 그대로 = 최악의 채움본
    kinds = {e["kind"] for e in out["errors"]}
    assert {"delete_color_residue", "unmodified_sample"} <= kinds
    names = {d.name for d in __import__("hwpx_mcp_server.tools", fromlist=["build_tool_definitions"]).build_tool_definitions()}
    assert "inspect_fill_residue" in names
