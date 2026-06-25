# SPDX-License-Identifier: Apache-2.0
"""MCP surface for 직인/관인 placement + compliance (M2 P3 slice 3 / FR-003, FR-005).

The seal capability is oracle-bound: real Hancom renders the form so we know where
the 발신명의 line lands. These e2e tests stub that render (no GUI) to pin the tool
logic — anchor discovery, placement, honest no-oracle degrade, and the pass/fail
compliance check. A real-Hancom smoke (gated) proves the full loop end-to-end.
"""
from __future__ import annotations

import zipfile

import pytest

import hwpx_mcp_server.server as server
from hwpx.form_fit.wordbox import OracleUnavailable, Rect, WordBox

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
SENDER = "행정안전부장관 홍길동"
# 1x1 PNG.
PNG_1X1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/axwAqkAAAAASUVORK5CYII="


def _sender_boxes(*, y: float = 700.0):
    """A single rendered line for SENDER; last inked glyph '동' centered ~ (x,y)."""
    boxes = []
    x = 100.0
    for ch in SENDER:
        if ch.isspace():
            x += 12.0
            continue
        boxes.append(WordBox(x0=x, y0=y, x1=x + 12.0, y1=y + 14.0, text=ch, page=0))
        x += 12.0
    return boxes


def _anchor_center():
    from hwpx.form_fit import seal

    return seal.find_seal_anchor(_sender_boxes(), SENDER).center


def _make_form(tmp_path):
    path = str(tmp_path / "form.hwpx")
    server.create_document(path)
    server.add_paragraph(path, "제목: 협조 요청")
    server.add_paragraph(path, SENDER)
    return path


def _pic_count(path):
    with zipfile.ZipFile(path) as z:
        return z.read("Contents/section0.xml").decode("utf-8").count("<hp:pic")


# --------------------------------------------------------------------------- #
# place_seal
# --------------------------------------------------------------------------- #


def test_place_seal_with_explicit_anchor_needs_no_oracle(tmp_path, monkeypatch):
    # explicit anchor -> no render needed; floating seal lands + file is open-safe.
    def _boom(*a, **k):
        raise AssertionError("oracle must not be called when anchor is explicit")

    monkeypatch.setattr(server, "render_glyph_boxes", _boom)
    path = _make_form(tmp_path)
    cx, cy = _anchor_center()
    result = server.place_seal(
        path, SENDER, PNG_1X1,
        anchor_x=cx, anchor_y=cy, anchor_page=0, verify=False,
    )
    assert result["ok"] is True
    assert result["placement"]["placed"] is True
    assert result["openSafety"]["ok"] is True
    assert _pic_count(path) == 1


def test_place_seal_uses_oracle_render_to_find_anchor(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server, "render_glyph_boxes",
        lambda *a, **k: (_sender_boxes(), [(595.0, 842.0)], "StubOracle"),
    )
    path = _make_form(tmp_path)
    result = server.place_seal(path, SENDER, PNG_1X1, verify=False)
    assert result["ok"] is True
    assert result["anchorSource"] == "StubOracle"
    assert result["placement"]["placed"] is True
    assert _pic_count(path) == 1


def test_place_seal_degrades_without_oracle(tmp_path, monkeypatch):
    def _no_oracle(*a, **k):
        raise OracleUnavailable("no reachable Hancom render backend")

    monkeypatch.setattr(server, "render_glyph_boxes", _no_oracle)
    path = _make_form(tmp_path)
    result = server.place_seal(path, SENDER, PNG_1X1)
    assert result["ok"] is False
    assert result["renderChecked"] is False
    assert "anchor" in result["note"].lower() or "오라클" in result["note"]
    assert _pic_count(path) == 0  # nothing stamped


def test_place_seal_sender_absent_in_render_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server, "render_glyph_boxes",
        lambda *a, **k: ([WordBox(x0=0, y0=0, x1=10, y1=12, text="본", page=0)], [(595.0, 842.0)], "StubOracle"),
    )
    path = _make_form(tmp_path)
    result = server.place_seal(path, SENDER, PNG_1X1, verify=False)
    assert result["ok"] is False
    assert result["renderChecked"] is True
    assert _pic_count(path) == 0


# --------------------------------------------------------------------------- #
# check_seal_compliance
# --------------------------------------------------------------------------- #


def _stub_render(seal_rect):
    boxes = _sender_boxes()

    def _render(*a, **k):
        return (boxes, [(595.0, 842.0)], "StubOracle")

    def _images(*a, **k):
        return [seal_rect]

    return _render, _images


def test_check_seal_compliance_pass(tmp_path, monkeypatch):
    cx, cy = _anchor_center()
    seal_rect = Rect(cx - 30, cy - 30, cx + 30, cy + 30, label="seal", page=0)
    render, images = _stub_render(seal_rect)
    monkeypatch.setattr(server, "render_glyph_boxes", render)
    monkeypatch.setattr(server, "extract_image_boxes", images)
    path = _make_form(tmp_path)
    result = server.check_seal_compliance(path, SENDER, tol_pt=8.0)
    assert result["renderChecked"] is True
    assert result["ok"] is True and result["centered"] is True


def test_check_seal_compliance_fail_when_misplaced(tmp_path, monkeypatch):
    cx, cy = _anchor_center()
    seal_rect = Rect(cx + 150, cy - 30, cx + 210, cy + 30, label="seal", page=0)  # far off
    render, images = _stub_render(seal_rect)
    monkeypatch.setattr(server, "render_glyph_boxes", render)
    monkeypatch.setattr(server, "extract_image_boxes", images)
    path = _make_form(tmp_path)
    result = server.check_seal_compliance(path, SENDER, tol_pt=8.0)
    assert result["renderChecked"] is True
    assert result["ok"] is False and result["centered"] is False


def test_check_seal_compliance_degrades_without_oracle(tmp_path, monkeypatch):
    def _no_oracle(*a, **k):
        raise OracleUnavailable("no reachable Hancom render backend")

    monkeypatch.setattr(server, "render_glyph_boxes", _no_oracle)
    path = _make_form(tmp_path)
    result = server.check_seal_compliance(path, SENDER)
    assert result["ok"] is False and result["renderChecked"] is False


# --------------------------------------------------------------------------- #
# live Hancom oracle smoke (full MCP loop — FR-003 + FR-005)
# --------------------------------------------------------------------------- #


def _mac_seal_oracle_ready() -> bool:
    try:
        import hwpx.form_fit.wordbox as wb
        from hwpx.visual.oracle import MacHancomOracle

        return MacHancomOracle().available() and wb.fitz_available()
    except Exception:
        return False


@pytest.mark.skipif(
    not (_mac_seal_oracle_ready() and __import__("os").environ.get("HWPX_MAC_ORACLE_SMOKE")),
    reason="set HWPX_MAC_ORACLE_SMOKE=1 on macOS+Hancom to drive the seal MCP smoke",
)
def test_place_seal_mcp_full_loop_smoke(tmp_path):
    """place_seal through the MCP surface: real Hancom locates the 발신명의, stamps
    the seal, and the re-render verdict confirms it landed centered (FR-003/FR-005)."""
    import base64

    import fitz

    doc = fitz.open()
    page = doc.new_page(width=72, height=72)
    page.draw_circle(fitz.Point(36, 36), 33, color=(0.8, 0, 0), width=4)
    seal_b64 = base64.b64encode(page.get_pixmap(dpi=200, alpha=False).tobytes("png")).decode()

    path = str(tmp_path / "letter.hwpx")
    server.create_document(path)
    server.add_paragraph(path, "협조 요청의 건")
    server.add_paragraph(path, "1. 귀 기관의 발전을 기원합니다.")
    server.add_paragraph(path, "")
    server.add_paragraph(path, SENDER)

    result = server.place_seal(path, SENDER, seal_b64, seal_width_mm=22.0, verify=True, tol_pt=10.0)
    assert result["ok"] is True
    assert result["openSafety"]["ok"] is True
    assert result["sealVerdict"]["renderChecked"] is True
    assert result["sealVerdict"]["ok"] is True
    assert result["sealVerdict"]["centered"] is True

    # the standalone compliance tool agrees on the saved file
    check = server.check_seal_compliance(path, SENDER, tol_pt=10.0)
    assert check["renderChecked"] is True and check["ok"] is True
