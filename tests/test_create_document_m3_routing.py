# SPDX-License-Identifier: Apache-2.0
"""M3 P4 — create_document_from_plan MCP surface: 공문 routing, quality, HWPX-only."""
from __future__ import annotations

from hwpx_mcp_server import server


def _gongmun_plan():
    return {
        "schemaVersion": "hwpx.document_plan.v1",
        "title": "교육협력 사업 추진 협조 요청",
        "metadata": {"document_type": "공문"},
        "blocks": [
            {"type": "paragraph", "text": "수신  각급학교장"},
            {"type": "heading", "level": 1, "text": "1. 관련"},
            {"type": "paragraph", "text": "가. 적극 협조하여 주시기 바랍니다.  끝."},
        ],
        "gyeolmun": {
            "issuer": "○○교육지원청교육장",
            "productionNumber": "교육협력과-1234",
            "enforcementDate": "2026. 6. 27.",
            "disclosure": "공개",
        },
    }


def test_gongmun_tool_routes_and_surfaces_quality(tmp_path):
    out = str(tmp_path / "gongmun.hwpx")
    res = server.create_document_from_plan(out, _gongmun_plan(), verbosity="full")
    assert res["created"] is True
    quality = res["quality"]
    # 공문 structure hard-gate surfaced + passing
    assert quality["gongmun_structure"] is not None
    assert quality["gongmun_structure"]["structure_pass"] is True
    # honest proofing + render status (verify_render default False)
    assert quality["korean_proofing_status"] == "unverified"
    assert quality["render_checked"] is False
    assert quality["visual_complete"] == "unverified"


def test_incomplete_gongmun_fails_gate(tmp_path):
    plan = _gongmun_plan()
    plan.pop("gyeolmun")  # drop 발신명의/시행/공개구분
    plan["blocks"] = [{"type": "heading", "level": 1, "text": "1. 안내"},
                      {"type": "paragraph", "text": "가. 알려드립니다."}]  # no 수신/끝.
    out = str(tmp_path / "incomplete.hwpx")
    res = server.create_document_from_plan(out, plan, verbosity="full")
    # routed + created, but the structure gate must flag the missing spine
    assert res["quality"]["gongmun_structure"]["structure_pass"] is False


def test_non_hwpx_filename_rejected(tmp_path):
    res = server.create_document_from_plan(str(tmp_path / "draft.odt"), _gongmun_plan())
    assert res["created"] is False
    assert "unsupported" in res["error"].lower()
    assert res["handoff_status"] == "unsupported_format"
