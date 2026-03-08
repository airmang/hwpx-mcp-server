from pathlib import Path

import pytest

from hwpx_mcp_server.core.document import open_doc, save_doc
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


def test_format_text_persists_run_level_style_changes(tmp_path: Path):
    target = tmp_path / "format_text.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "Hello World")

    result = format_text(
        str(target),
        paragraph_index=1,
        start_pos=6,
        end_pos=11,
        bold=True,
        color="FF0000",
        font_size=14,
    )

    assert result["formatted"] is True

    doc = open_doc(str(target))
    paragraph = doc.paragraphs[1]
    runs = list(paragraph.runs)

    assert [run.text for run in runs] == ["Hello ", "World"]
    assert runs[0].char_pr_id_ref != runs[1].char_pr_id_ref

    plain_style = runs[0].style
    accent_style = runs[1].style
    assert plain_style is not None
    assert accent_style is not None
    assert plain_style.text_color() == "#000000"
    assert accent_style.text_color() == "#FF0000"
    assert "bold" not in plain_style.child_attributes
    assert "bold" in accent_style.child_attributes
    assert accent_style.attributes.get("height") == "1400"


def test_create_custom_style_creates_distinct_style_and_name_resolves_on_insert(tmp_path: Path):
    target = tmp_path / "custom_style.hwpx"
    create_document(str(target))

    before = list_styles(str(target))["styles"]
    body_style = next(style for style in before if style.get("name") in {"본문", "Body"})

    created = create_custom_style(
        str(target),
        "AccentStyle",
        bold=True,
        color="FF0000",
        font_size=14,
    )
    add_paragraph(str(target), "Styled paragraph", style="AccentStyle")

    after = list_styles(str(target))["styles"]
    assert len(after) == len(before) + 1
    assert created["created"] is True
    assert created["style_name"] == "AccentStyle"
    assert created["style_id"] != body_style["id"]
    assert created["char_pr_id_ref"] != body_style["char_pr_id_ref"]

    doc = open_doc(str(target))
    paragraph = doc.paragraphs[-1]
    assert paragraph.style_id_ref == created["style_id"]

    style = doc.style(paragraph.style_id_ref)
    assert style is not None
    assert style.name == "AccentStyle"
    assert str(style.char_pr_id_ref) == created["char_pr_id_ref"]

    char_style = doc.char_property(style.char_pr_id_ref)
    assert char_style is not None
    assert char_style.text_color() == "#FF0000"
    assert "bold" in char_style.child_attributes
    assert char_style.attributes.get("height") == "1400"


def test_create_custom_style_reuses_existing_name(tmp_path: Path):
    target = tmp_path / "style_reuse.hwpx"
    create_document(str(target))

    first = create_custom_style(str(target), "AccentStyle", bold=True)
    second = create_custom_style(str(target), "AccentStyle", bold=True)

    styles = list_styles(str(target))["styles"]
    assert len([style for style in styles if style.get("name") == "AccentStyle"]) == 1
    assert first["style_id"] == second["style_id"]
    assert second["created"] is False


def test_save_doc_uses_atomic_write_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "atomic.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "original")
    original_bytes = target.read_bytes()

    doc = open_doc(str(target))
    doc.add_paragraph("updated")

    def flaky_save(path: str | Path) -> None:
        broken_path = Path(path)
        broken_path.write_text("not-a-valid-hwpx", encoding="utf-8")
        raise RuntimeError("forced save failure")

    monkeypatch.setattr(doc, "save_to_path", flaky_save)

    with pytest.raises(RuntimeError, match="forced save failure"):
        save_doc(doc, str(target))

    assert target.read_bytes() == original_bytes
    assert target.with_suffix(".hwpx.bak").exists()
    assert [path.name for path in tmp_path.glob("*.hwpx")] == ["atomic.hwpx"]


def test_list_styles(tmp_path: Path):
    target = tmp_path / "list_styles.hwpx"
    create_document(str(target))

    result = list_styles(str(target))

    assert result["count"] > 0


def test_merge_table_cells(tmp_path: Path):
    target = tmp_path / "merge_table.hwpx"
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
    add_paragraph(str(source), "copy me")

    copied = copy_document(str(source))
    copied_path = tmp_path / copied["destination"]

    assert copied_path.exists()
    assert open_doc(str(source)) is not None
    assert open_doc(str(copied_path)) is not None
