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
