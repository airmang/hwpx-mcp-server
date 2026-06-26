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

import shutil
from pathlib import Path

import pytest

import hwpx
import hwpx_mcp_server.server as server
from hwpx.document import HwpxDocument
from hwpx.exam import SplitReport
from hwpx.oxml.namespaces import HH

# The exam form fixtures are vendored once in python-hwpx (real school forms,
# governed by tests/fixtures/exam/NOTICE.md). With the editable sibling checkout
# the MCP tests reference them directly instead of duplicating sensitive data.
PYHWPX_EXAM_FIX = Path(hwpx.__file__).resolve().parents[2] / "tests" / "fixtures" / "exam"
_FIX_AVAILABLE = (PYHWPX_EXAM_FIX / "A_form.hwpx").exists()
requires_form_fixture = pytest.mark.skipif(
    not _FIX_AVAILABLE,
    reason="python-hwpx exam fixtures unavailable (need editable sibling checkout)",
)


def _form_copy(tmp_path) -> str:
    dest = tmp_path / "form.hwpx"
    shutil.copy(PYHWPX_EXAM_FIX / "A_form.hwpx", dest)
    return str(dest)


def _sample_md() -> str:
    return (PYHWPX_EXAM_FIX / "sample_exam.md").read_text(encoding="utf-8")


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


# --------------------------------------------------------------------------- #
# compose_exam (the leap) — verify-off / fail-loud / input XOR, no Hancom needed
# --------------------------------------------------------------------------- #


@requires_form_fixture
def test_compose_exam_verify_off(tmp_path):
    form = _form_copy(tmp_path)
    out = str(tmp_path / "out.hwpx")
    res = server.compose_exam(form, out, exam_md=_sample_md(), verify=False)
    assert res["ok"] is True
    # verify=False -> compose only, honest: no render, never a silent 0.
    assert res["renderChecked"] is False
    assert res["needsReview"] is True
    assert res["splits"] is None
    assert res["notes"]
    assert Path(res["outputPath"]).exists()
    HwpxDocument.open(res["outputPath"])  # output reopens cleanly
    assert "openSafety" in res


@requires_form_fixture
def test_compose_exam_md_from_file(tmp_path):
    form = _form_copy(tmp_path)
    md_file = tmp_path / "exam.md"
    md_file.write_text(_sample_md(), encoding="utf-8")
    out = str(tmp_path / "out.hwpx")
    res = server.compose_exam(form, out, exam_md_filename=str(md_file), verify=False)
    assert res["ok"] is True
    assert Path(res["outputPath"]).exists()


@requires_form_fixture
def test_compose_exam_parse_error_is_loud(tmp_path):
    form = _form_copy(tmp_path)
    out = str(tmp_path / "out.hwpx")
    # plain content with no 문항 header -> ExamParseError before anything is written.
    res = server.compose_exam(form, out, exam_md="문항 헤더 없는 그냥 본문\n", verify=False)
    assert res["ok"] is False
    assert res.get("error") == "ExamParseError"
    assert not Path(out).exists()


def test_compose_exam_requires_md_xor(tmp_path):
    out = str(tmp_path / "out.hwpx")
    form = str(tmp_path / "form.hwpx")
    # neither
    r_none = server.compose_exam(form, out)
    assert r_none["ok"] is False
    # both
    r_both = server.compose_exam(form, out, exam_md="x", exam_md_filename="y.md")
    assert r_both["ok"] is False


# --------------------------------------------------------------------------- #
# surface wiring — the new tools are part of the declared MCP surface
# --------------------------------------------------------------------------- #


def test_exam_tools_registered_and_not_skewed():
    names = server._fastmcp_tool_names()
    assert "compose_exam" in names
    assert "verify_question_splits" in names
    health = server.mcp_server_health()
    surface = health["toolSurface"]
    assert surface["status"] == "ok"  # expected count keeps up with the new tools
    assert "compose_exam" in surface["keyTools"]
    assert surface["missingKeyTools"] == []
