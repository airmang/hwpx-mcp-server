from pathlib import Path

import pytest

import hwpx_mcp_server.server as server_module
from hwpx_mcp_server.core.document import open_doc
from hwpx_mcp_server.server import (
    add_heading,
    add_memo,
    add_page_break,
    add_paragraph,
    add_table,
    create_document,
    delete_paragraph,
    fill_by_path,
    find_cell_by_label,
    get_document_outline,
    get_document_text,
    get_paragraphs_text,
    get_table_map,
    get_table_text,
    insert_paragraph,
    list_available_documents,
    remove_memo,
    set_table_cell_text,
)

_FORM_ROWS = [["성명:", ""], ["소속", ""], ["합계", "100"]]


def _create_form_document(target: Path) -> None:
    create_document(str(target))
    add_paragraph(str(target), "기본정보")
    add_table(str(target), len(_FORM_ROWS), len(_FORM_ROWS[0]), _FORM_ROWS)


def _create_ambiguous_form_document(target: Path) -> None:
    _create_form_document(target)
    add_paragraph(str(target), "추가정보")
    add_table(str(target), 1, 2, [["성명", ""]])


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


def test_get_table_map_returns_stable_json_shape(tmp_path: Path):
    target = tmp_path / "form.hwpx"
    _create_form_document(target)

    result = get_table_map(str(target))

    assert set(result) == {"tables", "count"}
    assert result["count"] == 1
    entry = result["tables"][0]
    assert set(entry) == {
        "table_index",
        "paragraph_index",
        "rows",
        "cols",
        "header_text",
        "first_row_preview",
        "is_empty",
    }
    assert entry["table_index"] == 0
    assert entry["rows"] == 3
    assert entry["cols"] == 2
    assert entry["first_row_preview"] == ["성명:", ""]
    assert entry["header_text"] == "기본정보"


def test_find_cell_by_label_handles_label_normalization(tmp_path: Path):
    target = tmp_path / "form.hwpx"
    _create_form_document(target)

    plain = find_cell_by_label(str(target), "성명")
    normalized = find_cell_by_label(str(target), "성명:")

    assert plain["count"] == 1
    assert normalized["count"] == 1
    assert plain["matches"] == normalized["matches"]
    assert plain["matches"][0]["label_cell"]["text"] == "성명:"
    assert plain["matches"][0]["target_cell"] == {"row": 0, "col": 1, "text": ""}


def test_find_cell_by_label_rejects_unsupported_direction(tmp_path: Path):
    target = tmp_path / "form.hwpx"
    _create_form_document(target)

    with pytest.raises(ValueError, match="direction must be one of: right, down"):
        find_cell_by_label(str(target), "성명", direction="left")


def test_fill_by_path_applies_multiple_mappings_correctly(tmp_path: Path):
    target = tmp_path / "form.hwpx"
    _create_form_document(target)

    result = fill_by_path(
        str(target),
        {
            "성명 > right": "홍길동",
            "소속 > right": "AI연구소",
        },
    )

    assert result["applied_count"] == 2
    assert result["failed_count"] == 0

    table = get_table_text(str(target), 0)
    assert table["data"][0][1] == "홍길동"
    assert table["data"][1][1] == "AI연구소"


def test_fill_by_path_reports_ambiguous_label_as_failed_entry(tmp_path: Path):
    target = tmp_path / "ambiguous_form.hwpx"
    _create_ambiguous_form_document(target)

    result = fill_by_path(str(target), {"성명 > right": "홍길동"})

    assert result["applied"] == []
    assert result["applied_count"] == 0
    assert result["failed_count"] == 1
    assert result["failed"] == [{"path": "성명 > right", "reason": "ambiguous label"}]


def test_fill_by_path_reports_out_of_bounds_path_as_failed_entry(tmp_path: Path):
    target = tmp_path / "form.hwpx"
    _create_form_document(target)

    result = fill_by_path(str(target), {"합계 > down > right": "초과"})

    assert result["applied"] == []
    assert result["applied_count"] == 0
    assert result["failed_count"] == 1
    assert result["failed"] == [{"path": "합계 > down > right", "reason": "navigation out of bounds"}]


def test_fill_by_path_saves_after_successful_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "saved_form.hwpx"
    _create_form_document(target)

    save_calls: list[str] = []
    original_save = server_module.save_doc

    def _tracking_save(doc, path: str) -> None:
        save_calls.append(path)
        original_save(doc, path)

    monkeypatch.setattr(server_module, "save_doc", _tracking_save)

    fill_by_path(str(target), {"성명 > right": "홍길동"})

    assert len(save_calls) == 1
    assert Path(save_calls[0]).resolve() == target.resolve()
    assert get_table_text(str(target), 0)["data"][0][1] == "홍길동"


def test_fill_by_path_rejects_empty_mappings(tmp_path: Path):
    target = tmp_path / "form.hwpx"
    _create_form_document(target)

    with pytest.raises(ValueError, match="mappings must not be empty"):
        fill_by_path(str(target), {})


def test_add_and_remove_memo(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    add_paragraph(str(target), "메모 대상")
    original_text = open_doc(str(target)).paragraphs[1].text
    added = add_memo(str(target), 1, "검토 필요")
    assert len(open_doc(str(target)).memos) == 1

    removed = remove_memo(str(target), 1)
    refreshed = open_doc(str(target))
    assert len(refreshed.memos) == 0
    assert refreshed.paragraphs[1].text == original_text

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
