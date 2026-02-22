from pathlib import Path

from hwpx_mcp_server.core.document import open_doc
from hwpx_mcp_server.server import (
    add_heading,
    add_memo,
    add_page_break,
    add_paragraph,
    add_table,
    create_document,
    delete_paragraph,
    get_document_outline,
    get_document_text,
    get_paragraphs_text,
    get_table_text,
    insert_paragraph,
    list_available_documents,
    remove_memo,
    set_table_cell_text,
)


def test_add_paragraph(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    add_paragraph(str(target), "안녕하세요")
    text_result = get_document_text(str(target))

    assert "안녕하세요" in text_result["text"]


def test_add_heading(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    add_heading(str(target), "1장 서론", level=1)
    outline = get_document_outline(str(target))["outline"]

    assert any(item["level"] == 1 and "1장 서론" in item["text"] for item in outline)


def test_insert_paragraph(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    add_paragraph(str(target), "첫 문단")
    add_paragraph(str(target), "둘 문단")
    add_paragraph(str(target), "셋 문단")

    insert_paragraph(str(target), 1, "삽입 문단")
    rows = get_paragraphs_text(str(target), 0, 6)["paragraphs"]
    texts = [entry["text"] for entry in rows]

    assert texts.index("삽입 문단") < texts.index("둘 문단")


def test_delete_paragraph(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    add_paragraph(str(target), "삭제 대상")
    before = len(get_paragraphs_text(str(target))["paragraphs"])
    result = delete_paragraph(str(target), 1)
    after = len(get_paragraphs_text(str(target))["paragraphs"])

    assert result["remaining_paragraphs"] == after
    assert after == before - 1
    texts = [entry["text"] for entry in get_paragraphs_text(str(target))["paragraphs"]]
    assert "삭제 대상" not in texts


def test_add_table(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    add_table(str(target), 2, 3, [["A", "B", "C"], ["1", "2", "3"]])
    table = get_table_text(str(target), table_index=0)

    assert table["data"][0] == ["A", "B", "C"]


def test_set_table_cell_text(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    add_table(str(target), 1, 1, [["초기값"]])
    set_table_cell_text(str(target), 0, 0, 0, "변경값")
    table = get_table_text(str(target), 0)

    assert table["data"][0][0] == "변경값"


def test_add_and_remove_memo(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    add_paragraph(str(target), "메모 대상")
    added = add_memo(str(target), 1, "검토 필요")
    assert len(open_doc(str(target)).memos) == 1

    removed = remove_memo(str(target), 1)
    assert len(open_doc(str(target)).memos) == 0

    assert added["memo_added"] is True
    assert removed["memo_removed"] is True


def test_add_page_break(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    result = add_page_break(str(target))

    assert result["success"] is True


def test_list_available_documents(tmp_path: Path):
    create_document(str(tmp_path / "test1.hwpx"))
    create_document(str(tmp_path / "test2.hwpx"))

    result = list_available_documents(str(tmp_path))

    assert result["count"] == 2
    names = {entry["filename"] for entry in result["documents"]}
    assert {"test1.hwpx", "test2.hwpx"}.issubset(names)
