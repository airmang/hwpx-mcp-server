# SPDX-License-Identifier: Apache-2.0
"""S-056 Plan 3 — MCP exposure of the hwpx.exam composer.

Covers the three tools added in Plan 3:
  * set_paragraph_format keep-together passthrough (spec 3a)
  * verify_question_splits honest degrade (spec 3b)
  * compose_exam leap tool (verify-off / fail-loud / input XOR)

The oracle-bound paths are exercised through their honest-degrade branches so
the default suite needs no Hancom (Constitution V — no silent true).
"""
from __future__ import annotations

import hwpx_mcp_server.server as server
from hwpx.document import HwpxDocument
from hwpx.exam import SplitReport
from hwpx.oxml.namespaces import HH


class _FakeOracle:
    def __init__(self, available: bool, pdf: str | None):
        self._available = available
        self._pdf = pdf

    def available(self) -> bool:
        return self._available

    def render_pdf(self, path: str, out_pdf: str | None = None) -> str | None:
        return self._pdf


def _make_doc(tmp_path):
    path = str(tmp_path / "doc.hwpx")
    server.create_document(path)
    server.add_paragraph(path, "문항 1. 다음 중 옳은 것은?")
    return path


def _break_setting(path: str, paragraph_index: int):
    doc = HwpxDocument.open(path)
    para = doc.sections[0].paragraphs[paragraph_index]
    return doc.oxml.headers[0].element.find(
        f".//{HH}paraPr[@id='{para.para_pr_id_ref}']/{HH}breakSetting"
    )


def test_set_paragraph_format_keep_with_next(tmp_path):
    path = _make_doc(tmp_path)
    result = server.set_paragraph_format(
        path, paragraph_index=0, keep_with_next=True, keep_lines=True
    )
    assert result.get("formatted") == 1
    bs = _break_setting(path, 0)
    assert bs is not None
    assert bs.get("keepWithNext") == "1"
    assert bs.get("keepLines") == "1"


def test_set_paragraph_format_page_break_before(tmp_path):
    path = _make_doc(tmp_path)
    server.set_paragraph_format(path, paragraph_index=0, page_break_before=True)
    bs = _break_setting(path, 0)
    assert bs is not None
    assert bs.get("pageBreakBefore") == "1"


# --------------------------------------------------------------------------- #
# verify_question_splits (spec 3b) — honest degrade paths, no Hancom needed
# --------------------------------------------------------------------------- #


def test_verify_question_splits_no_oracle(tmp_path, monkeypatch):
    path = _make_doc(tmp_path)
    monkeypatch.setattr(server, "resolve_oracle", lambda: _FakeOracle(False, None))
    res = server.verify_question_splits(path)
    assert res["renderChecked"] is False
    assert res["needsReview"] is True
    assert res.get("splits") is None  # no silent 0


def test_verify_question_splits_render_returns_none(tmp_path, monkeypatch):
    path = _make_doc(tmp_path)
    monkeypatch.setattr(server, "resolve_oracle", lambda: _FakeOracle(True, None))
    res = server.verify_question_splits(path)
    assert res["renderChecked"] is False
    assert res["needsReview"] is True
    assert res.get("splits") is None


def test_verify_question_splits_curve_export(tmp_path, monkeypatch):
    path = _make_doc(tmp_path)
    monkeypatch.setattr(server, "resolve_oracle", lambda: _FakeOracle(True, "/tmp/fake.pdf"))
    monkeypatch.setattr(server, "measure_question_splits", lambda pdf, **kw: SplitReport(0, 0, 0, {}, ()))
    res = server.verify_question_splits(path, valid_question_numbers=["1", "2"])
    assert res["renderChecked"] is True
    assert res["splits"] is None  # curve-export form: unverifiable, never a silent 0
    assert res["needsReview"] is True
    assert res["nBlocks"] == 0


def test_verify_question_splits_reports_splits(tmp_path, monkeypatch):
    path = _make_doc(tmp_path)
    monkeypatch.setattr(server, "resolve_oracle", lambda: _FakeOracle(True, "/tmp/fake.pdf"))
    monkeypatch.setattr(
        server, "measure_question_splits", lambda pdf, **kw: SplitReport(2, 5, 120, {"column": 2}, ("3", "4"))
    )
    res = server.verify_question_splits(path)
    assert res["renderChecked"] is True
    assert res["splits"] == 2
    assert res["needsReview"] is True
    assert res["splitIds"] == ["3", "4"]
    assert res["nBlocks"] == 5


def test_verify_question_splits_clean(tmp_path, monkeypatch):
    path = _make_doc(tmp_path)
    monkeypatch.setattr(server, "resolve_oracle", lambda: _FakeOracle(True, "/tmp/fake.pdf"))
    monkeypatch.setattr(server, "measure_question_splits", lambda pdf, **kw: SplitReport(0, 5, 120, {}, ()))
    res = server.verify_question_splits(path)
    assert res["renderChecked"] is True
    assert res["splits"] == 0
    assert res["needsReview"] is False
