import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

import hwpx_mcp_server.server as server_module
from hwpx_mcp_server.core.document import open_doc, save_doc
from hwpx_mcp_server.server import (
    add_heading,
    add_memo,
    add_page_break,
    add_paragraph,
    add_table,
    byte_preserving_patch,
    copy_document,
    create_document,
    delete_paragraph,
    fill_by_path,
    find_cell_by_label,
    find_text,
    get_document_outline,
    get_document_text,
    get_paragraph_text,
    get_paragraphs_text,
    get_table_map,
    get_table_text,
    insert_paragraph,
    insert_picture,
    list_available_documents,
    remove_memo,
    replace_picture,
    replace_by_anchor,
    replace_in_paragraph,
    set_table_cell_text,
)
from hwpx_mcp_server.utils.helpers import resolve_path

_FORM_ROWS = [["성명:", ""], ["소속", ""], ["합계", "100"]]
HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

PNG_1X1_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/axwAqkAAAAASUVORK5CYII="
PNG_1X1_ALT_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR42mP8z8BQDwAFgwJ/l8EydgAAAABJRU5ErkJggg=="


def _create_form_document(target: Path) -> None:
    create_document(str(target))
    add_paragraph(str(target), "기본정보")
    add_table(str(target), len(_FORM_ROWS), len(_FORM_ROWS[0]), _FORM_ROWS)


def _create_ambiguous_form_document(target: Path) -> None:
    _create_form_document(target)
    add_paragraph(str(target), "추가정보")
    add_table(str(target), 1, 2, [["성명", ""]])


def _replace_zip_part(path: Path, part_name: str, payload: bytes) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(path, "r") as source:
        with zipfile.ZipFile(tmp_path, "w") as target:
            for info in source.infolist():
                data = (
                    payload
                    if info.filename == part_name
                    else source.read(info.filename)
                )
                target.writestr(info, data)
    path.write_bytes(tmp_path.read_bytes())
    tmp_path.unlink()


def test_add_paragraph(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))

    result = add_paragraph(str(target), "안녕하세요")
    text_result = get_document_text(str(target))

    assert "안녕하세요" in text_result["text"]
    assert result["openSafety"]["ok"] is True
    assert result["verificationReport"]["filePath"] == str(target)


def test_insert_and_replace_picture_tools_preserve_safe_asset_graph(tmp_path: Path):
    target = tmp_path / "picture-tools.hwpx"
    create_document(str(target))

    inserted = insert_picture(
        str(target),
        PNG_1X1_B64,
        image_format="png",
        width=11111,
        height=22222,
    )

    assert inserted["openSafety"]["ok"] is True
    assert inserted["verificationReport"]["openSafety"]["ok"] is True
    assert inserted["idIntegrity"]["ok"] is True
    assert inserted["picture"]["binaryItemIDRef"] == "BIN0001"

    document = open_doc(str(target))
    assert document.package.has_part("BinData/BIN0001.png")
    assert any(
        item.get("id") == "BIN0001" for item in document.package._manifest_items()
    )

    replaced = replace_picture(str(target), PNG_1X1_ALT_B64, image_format="png")

    assert replaced["openSafety"]["ok"] is True
    assert replaced["verificationReport"]["openSafety"]["ok"] is True
    assert replaced["idIntegrity"]["ok"] is True
    assert replaced["replacement"]["geometryPreserved"] is True
    assert replaced["replacement"]["old_binaryItemIDRef"] == "BIN0001"
    assert replaced["replacement"]["new_binaryItemIDRef"] == "BIN0002"
    assert replaced["replacement"]["removedOldImage"] is True

    refreshed = open_doc(str(target))
    assert not refreshed.package.has_part("BinData/BIN0001.png")
    assert refreshed.package.has_part("BinData/BIN0002.png")
    assert refreshed.picture_references()[0]["binaryItemIDRef"] == "BIN0002"


def test_byte_preserving_patch_updates_paragraph_with_open_safety(tmp_path: Path):
    target = tmp_path / "patch.hwpx"
    create_document(str(target))
    added = add_paragraph(str(target), "원문")

    result = byte_preserving_patch(
        str(target),
        [
            {
                "sectionPath": "Contents/section0.xml",
                "paragraphIndex": added["paragraph_index"],
                "text": "패치본문",
            }
        ],
    )

    assert result["skipped"] == []
    assert result["changedParts"] == ["Contents/section0.xml"]
    assert result["openSafety"]["ok"] is True
    assert result["verificationReport"]["openSafety"]["ok"] is True
    assert result["verificationReport"]["filePath"] == str(target)
    assert "패치본문" in get_document_text(str(target))["text"]


def test_byte_preserving_patch_skips_unsupported_without_mutating(tmp_path: Path):
    target = tmp_path / "patch-skip.hwpx"
    create_document(str(target))
    added = add_paragraph(str(target), "원문")
    before = target.read_bytes()

    result = byte_preserving_patch(
        str(target),
        [
            {
                "sectionPath": "Contents/section0.xml",
                "paragraphIndex": added["paragraph_index"],
                "text": "첫 줄\n둘째 줄",
            }
        ],
    )

    assert result["skipped"][0]["reason"] == "line break insertion is unsupported"
    assert target.read_bytes() == before
    assert result["verificationReport"]["ok"] is False


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


def test_delete_only_paragraph_clears_layout_cache(tmp_path: Path):
    target = tmp_path / "test.hwpx"
    create_document(str(target))
    doc = open_doc(str(target))
    paragraph = doc.paragraphs[0]
    paragraph.runs[0].text = "삭제 대상"
    ET.SubElement(paragraph.element, f"{HP}lineSegArray")
    save_doc(doc, str(target))

    result = delete_paragraph(str(target), 0)
    doc = open_doc(str(target))

    assert result["remaining_paragraphs"] == 1
    assert doc.paragraphs[0].text == ""
    assert doc.paragraphs[0].element.find(f"{HP}lineSegArray") is None


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


def test_set_table_cell_text_preserves_char_pr_and_can_split_paragraphs(tmp_path: Path):
    target = tmp_path / "code.hwpx"
    create_document(str(target))
    add_table(str(target), 1, 1, [["old code"]])

    doc = open_doc(str(target))
    cell = doc.paragraphs[1].tables[0].cell(0, 0)
    paragraph = cell.paragraphs[0]
    paragraph.runs[0].char_pr_id_ref = "13"
    paragraph.add_run(" tail", char_pr_id_ref="21")
    server_module.save_doc(doc, str(target))

    set_table_cell_text(str(target), 0, 0, 0, "new code")
    refreshed = open_doc(str(target))
    refreshed_cell = refreshed.paragraphs[1].tables[0].cell(0, 0)
    refreshed_paragraph = refreshed_cell.paragraphs[0]

    assert refreshed_paragraph.runs[0].char_pr_id_ref == "13"
    assert refreshed_paragraph.runs[0].text == "new code"
    assert refreshed_paragraph.runs[1].char_pr_id_ref == "21"
    assert refreshed_paragraph.runs[1].text == ""

    set_table_cell_text(
        str(target), 0, 0, 0, "line one\nline two", split_paragraphs=True
    )
    split_cell = open_doc(str(target)).paragraphs[1].tables[0].cell(0, 0)
    assert [paragraph.text for paragraph in split_cell.paragraphs] == [
        "line one",
        "line two",
    ]
    assert [
        paragraph.runs[0].char_pr_id_ref for paragraph in split_cell.paragraphs
    ] == ["13", "13"]


def test_get_table_map_returns_stable_json_shape(tmp_path: Path):
    target = tmp_path / "form.hwpx"
    _create_form_document(target)

    result = get_table_map(str(target))

    assert {"tables", "count", "document_revision", "documentWarnings"}.issubset(result)
    assert result["count"] == 1
    assert result["document_revision"].startswith("sha256:")
    assert result["documentWarnings"] == []
    entry = result["tables"][0]
    assert set(entry) == {
        "table_index",
        "paragraph_index",
        "location",
        "rows",
        "cols",
        "caption_text",
        "preceding_paragraph_text",
        "header_text",
        "first_row_preview",
        "cells",
        "is_empty",
    }
    assert entry["table_index"] == 0
    assert entry["location"] == {
        "kind": "body_paragraph",
        "paragraph_index": entry["paragraph_index"],
    }
    assert entry["rows"] == 3
    assert entry["cols"] == 2
    assert entry["first_row_preview"] == ["성명:", ""]
    assert entry["caption_text"] == ""
    assert entry["preceding_paragraph_text"] == "기본정보"
    assert entry["header_text"] == "기본정보"
    assert entry["cells"][0]["paragraphs"][0]["location"] == {
        "kind": "table_cell_paragraph",
        "table_index": 0,
        "row": 0,
        "col": 0,
        "cell_paragraph_index": 0,
    }


def test_table_map_location_can_drive_text_lookup_and_memo(tmp_path: Path):
    target = tmp_path / "form.hwpx"
    _create_form_document(target)

    cell_location = get_table_map(str(target))["tables"][0]["cells"][0]["paragraphs"][
        0
    ]["location"]
    text_result = get_paragraph_text(str(target), location=cell_location)
    memo_result = add_memo(str(target), text="라벨 확인", location=cell_location)

    assert text_result["text"] == "성명:"
    assert text_result["location"] == cell_location
    assert memo_result["memo_added"] is True
    assert memo_result["location"] == cell_location
    assert len(open_doc(str(target)).memos) == 1


def test_replace_in_paragraph_uses_location_and_preserves_run_char_pr(tmp_path: Path):
    target = tmp_path / "code-cell.hwpx"
    create_document(str(target))
    add_table(str(target), 1, 1, [["REQUIRED_DATA_FILES = []"]])

    cell_location = get_table_map(str(target))["tables"][0]["cells"][0]["paragraphs"][
        0
    ]["location"]
    doc = open_doc(str(target))
    run = doc.paragraphs[1].tables[0].cell(0, 0).paragraphs[0].runs[0]
    run.char_pr_id_ref = "31"
    server_module.save_doc(doc, str(target))

    result = replace_in_paragraph(
        str(target),
        "[]",
        "['인천항_물동량.csv']",
        location=cell_location,
    )
    refreshed = open_doc(str(target))
    refreshed_run = refreshed.paragraphs[1].tables[0].cell(0, 0).paragraphs[0].runs[0]

    assert result["replaced_count"] == 1
    assert result["location"] == cell_location
    assert refreshed_run.char_pr_id_ref == "31"
    assert refreshed_run.text == "REQUIRED_DATA_FILES = ['인천항_물동량.csv']"


def test_replace_by_anchor_targets_exact_match_position(tmp_path: Path):
    target = tmp_path / "repeated-code.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "TOKEN = 1; TOKEN = 2")

    doc = open_doc(str(target))
    paragraph = doc.paragraphs[1]
    paragraph.runs[0].char_pr_id_ref = "41"
    server_module.save_doc(doc, str(target))

    matches = find_text(str(target), "TOKEN")
    result = replace_by_anchor(
        str(target), matches["matches"][1]["anchor"], "TOKEN", "VALUE"
    )
    refreshed = open_doc(str(target))

    assert result["replaced_count"] == 1
    assert refreshed.paragraphs[1].text == "TOKEN = 1; VALUE = 2"
    assert refreshed.paragraphs[1].runs[0].char_pr_id_ref == "41"


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


def test_resolve_path_allows_absolute_paths_inside_sandbox_and_guides_outside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    sandbox = tmp_path / "workspace"
    sandbox.mkdir()
    inside = sandbox / "doc.hwpx"
    outside = tmp_path / "outside.hwpx"
    monkeypatch.delenv("HWPX_MCP_WORKSPACE_ROOTS", raising=False)
    monkeypatch.setenv("HWPX_MCP_SANDBOX_ROOT", str(sandbox))

    assert resolve_path(str(inside)) == str(inside)
    with pytest.raises(PermissionError, match="outside the authorized"):
        resolve_path(str(outside))


def test_copy_document_rejects_unsafe_hwpx_source_and_preserves_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unsafe-source.hwpx"
    destination = tmp_path / "safe-destination.hwpx"
    create_document(str(source))
    create_document(str(destination))
    original_destination = destination.read_bytes()
    stale_section = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section' "
        "xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>"
        "<hp:p id='1' paraPrIDRef='0' styleIDRef='0' pageBreak='0' columnBreak='0' merged='0'>"
        "<hp:run charPrIDRef='0'><hp:t>Short</hp:t></hp:run>"
        "<hp:linesegarray><hp:lineseg textpos='40'/></hp:linesegarray>"
        "</hp:p></hs:sec>"
    ).encode("utf-8")
    _replace_zip_part(source, "Contents/section0.xml", stale_section)

    with pytest.raises(ValueError, match="source HWPX failed open-safety verification"):
        copy_document(str(source), str(destination))

    assert destination.read_bytes() == original_destination


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
    assert result["failed"] == [
        {"path": "합계 > down > right", "reason": "navigation out of bounds"}
    ]


def test_fill_by_path_saves_after_successful_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "saved_form.hwpx"
    _create_form_document(target)

    save_calls: list[str] = []
    original_save = server_module.save_doc

    def _tracking_save(doc, path: str, **kwargs) -> None:
        save_calls.append(path)
        original_save(doc, path, **kwargs)

    monkeypatch.setattr(server_module, "save_doc", _tracking_save)

    result = fill_by_path(str(target), {"성명 > right": "홍길동"})

    assert len(save_calls) == 1
    assert Path(save_calls[0]).resolve() == target.resolve()
    assert result["openSafety"]["ok"] is True
    assert result["verificationReport"]["filePath"] == str(target)
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
