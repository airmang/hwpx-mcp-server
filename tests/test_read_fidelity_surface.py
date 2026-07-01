# SPDX-License-Identifier: Apache-2.0
"""M6 / S-060 P2: the installed read surface exposes run-formatting + notes."""
from __future__ import annotations

import base64

from hwpx.document import HwpxDocument
import hwpx_mcp_server.server as server


def _rich_note_doc_b64() -> str:
    doc = HwpxDocument.new()
    p = doc.add_paragraph("보통 ")
    p.add_run("굵게", bold=True)
    p.add_run("빨강", color="#C00000")
    note = p.add_footnote("각주 본문 ")
    note.add_run("강조", bold=True)
    p2 = doc.add_paragraph("문장2")
    p2.add_endnote("미주 END-TEST")
    return base64.b64encode(doc.to_bytes()).decode("ascii")


def test_extract_json_exposes_notes() -> None:
    result = server.hwpx_extract_json(hwpx_base64=_rich_note_doc_b64(), mask=False)
    notes = result["doc"]["notes"]
    kinds = {n["kind"] for n in notes}
    assert kinds == {"footNote", "endNote"}
    fn = next(n for n in notes if n["kind"] == "footNote")
    assert "각주 본문" in fn["bodyText"] and "강조" in fn["bodyText"]
    assert any(s["bold"] for s in fn["bodySpans"])  # body formatting preserved


def test_extract_json_named_run_format_and_strikeout_fix() -> None:
    result = server.hwpx_extract_json(
        hwpx_base64=_rich_note_doc_b64(), format_detail=True, mask=False
    )
    runs = result["doc"]["sections"][0]["paragraphs"][0]["format"]["runs"]
    # named fields present
    for key in ("bold", "italic", "underline", "strikeout", "color", "fontSize", "fontName"):
        assert key in runs[0]
    bold_run = next(r for r in runs if r["text"] == "굵게")
    assert bold_run["bold"] is True
    assert bold_run["fontSize"] and bold_run["fontName"]
    # regression: plain runs must not report strikeout (shape="NONE" always present)
    assert all(r["strikeout"] is False for r in runs)


def test_markdown_appends_note_appendix() -> None:
    md = server.hwpx_to_markdown(hwpx_base64=_rich_note_doc_b64(), mask=False)["markdown"]
    assert "[^fn1]" in md and "[^en1]" in md
    assert "END-TEST" in md


def test_notes_are_pii_masked_by_default() -> None:
    doc = HwpxDocument.new()
    p = doc.add_paragraph("본문")
    p.add_footnote("연락처 010-1234-5678")
    payload = base64.b64encode(doc.to_bytes()).decode("ascii")
    result = server.hwpx_extract_json(hwpx_base64=payload)  # mask defaults ON
    body = result["doc"]["notes"][0]["bodyText"]
    assert "010-1234-5678" not in body
