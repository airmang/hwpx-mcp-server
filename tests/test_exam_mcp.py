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
from hwpx.oxml.namespaces import HH


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
