from pathlib import Path

from hwpx_mcp_server.server import (
    add_paragraph,
    add_table,
    copy_document,
    create_custom_style,
    create_document,
    format_text,
    list_styles,
    merge_table_cells,
)
from hwpx_mcp_server.core.document import open_doc


def test_format_text_bold(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "Hello World")

    result = format_text(str(target), paragraph_index=1, start_pos=6, end_pos=11, bold=True)

    assert result["formatted"] is True


def test_create_custom_style(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    create_custom_style(str(target), "강조체", bold=True, font_size=14)
    styles = list_styles(str(target))["styles"]

    assert any(style.get("name") == "강조체" for style in styles)


def test_create_custom_style_increases_style_count_without_overwrite(tmp_path: Path):
    target = tmp_path / "style_count.hwpx"
    create_document(str(target))

    before = list_styles(str(target))["styles"]
    before_by_id = {entry.get("id"): entry.get("name") for entry in before}

    create_custom_style(str(target), "신규스타일", bold=True)

    after = list_styles(str(target))["styles"]
    after_by_id = {entry.get("id"): entry.get("name") for entry in after}

    assert len(after) == len(before) + 1
    assert any(entry.get("name") == "신규스타일" for entry in after)
    for style_id, name in before_by_id.items():
        assert after_by_id.get(style_id) == name


def test_list_styles(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    result = list_styles(str(target))

    assert result["count"] > 0


def test_merge_table_cells(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))
    add_table(str(target), 2, 2, [["A", "B"], ["C", "D"]])

    merge_table_cells(str(target), 0, 0, 0, 1, 1)

    doc = open_doc(str(target))
    cell = doc.paragraphs[-1].tables[0].rows[0].cells[0]
    span = cell.element.find("{http://www.hancom.co.kr/hwpml/2011/paragraph}cellSpan")
    assert span is not None
    assert span.get("rowSpan") == "2"
    assert span.get("colSpan") == "2"


def test_copy_document(tmp_path: Path):
    source = tmp_path / "source.hwpx"
    create_document(str(source))
    add_paragraph(str(source), "복사 테스트")

    copied = copy_document(str(source))
    copied_path = tmp_path / copied["destination"]

    assert copied_path.exists()
    assert open_doc(str(source)) is not None
    assert open_doc(str(copied_path)) is not None
