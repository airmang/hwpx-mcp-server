# SPDX-License-Identifier: Apache-2.0
"""styled 문단/표 셀의 글자속성(charPrIDRef) 회귀 테스트.

버그: ``add_paragraph(style=...)``가 문단 styleIDRef만 지정하고 run에는
스타일이 규정한 글자속성을 넘기지 않아, python-hwpx가 run에 기본값
``charPrIDRef="0"``을 박았다. 0번 글자속성이 본문 크기이면 티가 안 나지만,
제목용 큰 글자(예: 17pt)를 0번에 둔 투고 양식에서는 본문 전체가 그 크기로
렌더된다("양식에 채워줘" → 글자가 통째로 커짐).

수정: ``add_paragraph``/``insert_paragraph``는 스타일의 charPrIDRef를 run에
싣고, 표 셀은 본문(바탕글) 글자속성을 적용한다. ``add_heading``은 원래부터
char_pr_id_ref를 넘겨 정상이었다(대칭 회복).
"""

import pytest

import hwpx_mcp_server.server as server
from hwpx_mcp_server.core.content import _default_body_char_pr, _iter_tables
from hwpx_mcp_server.core.document import open_doc
from hwpx_mcp_server.core.formatting import list_styles_in_doc


def _style_char_pr(doc, name: str) -> str | None:
    for s in list_styles_in_doc(doc):
        if s.get("name") == name:
            ref = s.get("char_pr_id_ref")
            return None if ref is None else str(ref)
    return None


def _runs_for_text(doc, needle: str):
    out = []
    for para in doc.paragraphs:
        if (para.text or "").strip() == needle:
            out.extend(para.runs)
    return out


def test_add_paragraph_applies_style_char_pr(tmp_path):
    """스타일을 지정한 본문 run은 그 스타일의 charPr를 써야 한다(0번이 아니라)."""
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    # '머리말' 스타일은 charPr 0이 아닌 별도 글자속성을 참조한다.
    server.add_paragraph(target, "머리말 스타일 본문", style="머리말")

    doc = open_doc(target)
    want = _style_char_pr(doc, "머리말")
    assert want is not None and want != "0"
    runs = _runs_for_text(doc, "머리말 스타일 본문")
    assert runs, "대상 문단을 찾지 못함"
    for run in runs:
        assert str(run.char_pr_id_ref) == want


@pytest.mark.parametrize("style_name", ["머리말", "각주", "메모"])
def test_styled_run_never_falls_back_to_zero(tmp_path, style_name):
    """charPr가 0이 아닌 스타일을 지정하면 run이 0으로 기본값 처리되면 안 된다."""
    target = str(tmp_path / f"doc_{style_name}.hwpx")
    server.create_document(target)
    marker = f"{style_name} 본문 문단"
    server.add_paragraph(target, marker, style=style_name)

    doc = open_doc(target)
    want = _style_char_pr(doc, style_name)
    if want is None or want == "0":
        pytest.skip(f"'{style_name}' 스타일 charPr가 이 스켈레톤에선 0/None")
    runs = _runs_for_text(doc, marker)
    assert runs
    assert all(str(r.char_pr_id_ref) == want for r in runs)


def test_no_style_paragraph_is_backward_compatible(tmp_path):
    """스타일 없는 첫 문단은 기존처럼 기본 글자속성(0)을 유지한다."""
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    server.add_paragraph(target, "스타일 없는 첫 본문")

    doc = open_doc(target)
    runs = _runs_for_text(doc, "스타일 없는 첫 본문")
    assert runs
    assert all(str(r.char_pr_id_ref) == "0" for r in runs)


def test_add_table_cells_use_body_char_pr(tmp_path):
    """표 셀 run은 제목 크기(charPr 0 누수)가 아니라 본문 글자속성을 써야 한다."""
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    server.add_table(target, 2, 2, [["기능", "경계"], ["c", "d"]])

    doc = open_doc(target)
    body_ref = _default_body_char_pr(doc)
    assert body_ref is not None
    tables = list(_iter_tables(doc))
    assert tables, "표가 생성되지 않음"
    seen = 0
    for table in tables:
        for row in table.rows:
            for cell in row.cells:
                for cell_para in cell.paragraphs:
                    for run in cell_para.runs:
                        seen += 1
                        assert str(run.char_pr_id_ref) == str(body_ref)
    assert seen, "셀 run을 찾지 못함"


def test_styled_fill_passes_open_safety(tmp_path):
    """스타일 지정 본문 + 표를 채운 문서가 한컴 open-safety를 통과한다."""
    target = str(tmp_path / "doc.hwpx")
    server.create_document(target)
    server.add_paragraph(target, "머리말 스타일 본문", style="머리말")
    server.add_table(target, 2, 2, [["a", "b"], ["c", "d"]])

    from hwpx.tools.package_validator import validate_editor_open_safety

    report = validate_editor_open_safety(target)
    payload = report.as_dict() if hasattr(report, "as_dict") else {}
    assert payload.get("ok", getattr(report, "ok", False))
