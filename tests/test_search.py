from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from hwpx_mcp_server.core.document import open_doc, save_doc
from hwpx_mcp_server.server import (
    batch_replace,
    create_document,
    find_text,
    get_document_text,
    search_and_replace,
)


@pytest.fixture()
def sample_file(tmp_path: Path) -> Path:
    target = tmp_path / "sample.hwpx"
    create_document(str(target))
    return target


def _append_paragraph(path: Path, text: str) -> None:
    doc = open_doc(str(path))
    doc.add_paragraph(text)
    save_doc(doc, str(path))


def _set_split_runs(path: Path, paragraph_index: int, chunks: list[str]) -> None:
    doc = open_doc(str(path))
    paragraph = doc.paragraphs[paragraph_index]
    for run in list(paragraph.runs):
        run.remove()
    for index, chunk in enumerate(chunks):
        paragraph.add_run(chunk, bold=(index == 0), italic=(index == 1))
    save_doc(doc, str(path))


def test_search_and_replace_basic(sample_file: Path):
    _append_paragraph(sample_file, "2025학년도 운영계획")

    result = search_and_replace(str(sample_file), "2025", "2026")
    text_result = get_document_text(str(sample_file))

    assert "2026학년도" in text_result["text"]
    assert result["replaced_count"] == 1


def test_batch_replace_order(sample_file: Path):
    _append_paragraph(sample_file, "2025. 3. 1. ~ 2026. 2. 28.")

    result = batch_replace(
        str(sample_file),
        [
            {"find": "2026", "replace": "2027"},
            {"find": "2025", "replace": "2026"},
        ],
    )
    text_result = get_document_text(str(sample_file))

    assert "2026. 3. 1. ~ 2027. 2. 28." in text_result["text"]
    assert result["total_replaced"] == 2


def test_search_and_replace_in_table(sample_file: Path):
    doc = open_doc(str(sample_file))
    table = doc.add_table(rows=1, cols=1)
    table.rows[0].cells[0].text = "표 안의 2025 데이터"
    save_doc(doc, str(sample_file))

    result = search_and_replace(str(sample_file), "2025", "2026")
    text_result = get_document_text(str(sample_file))

    assert result["replaced_count"] == 1
    assert "표 안의 2026 데이터" in text_result["text"]


def test_search_and_replace_cross_run_in_paragraph(sample_file: Path):
    _append_paragraph(sample_file, "")
    _set_split_runs(sample_file, 1, ["20", "26학년도 운영"])

    result = search_and_replace(str(sample_file), "2026", "2027")
    find_result = find_text(str(sample_file), "2026")
    doc = open_doc(str(sample_file))
    paragraph = doc.paragraphs[1]

    assert result["replaced_count"] == 1
    assert find_result["total_matches"] == 0
    assert paragraph.text.startswith("2027")
    assert len(paragraph.runs) >= 2


def test_search_and_replace_cross_run_in_table_cell(sample_file: Path):
    doc = open_doc(str(sample_file))
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    cell.text = "2026 데이터"

    hp = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
    para_element = cell.element.find(f".//{hp}p")
    assert para_element is not None
    run_elements = list(para_element.findall(f"{hp}run"))
    assert run_elements
    first_run = run_elements[0]
    for run_element in run_elements[1:]:
        para_element.remove(run_element)

    text_node = first_run.find(f"{hp}t")
    if text_node is None:
        text_node = ET.SubElement(first_run, f"{hp}t")
    text_node.text = "20"

    maker = getattr(para_element, "makeelement", None)
    if callable(maker):
        second_run = maker(f"{hp}run", {"charPrIDRef": "0"})
        second_text = second_run.makeelement(f"{hp}t", {})
        second_text.text = "26 데이터"
        second_run.append(second_text)
    else:
        second_run = ET.Element(f"{hp}run", {"charPrIDRef": "0"})
        ET.SubElement(second_run, f"{hp}t").text = "26 데이터"
    para_element.append(second_run)
    save_doc(doc, str(sample_file))

    result = search_and_replace(str(sample_file), "2026", "2027")
    table_result = get_document_text(str(sample_file))

    assert result["replaced_count"] == 1
    assert "2027 데이터" in table_result["text"]
    assert "2026 데이터" not in table_result["text"]


def test_get_document_text_truncation(sample_file: Path):
    _append_paragraph(sample_file, "가" * 200)

    result = get_document_text(str(sample_file), max_chars=30)

    assert result["truncated"] is True
    assert len(result["text"]) == 30


def test_create_document(tmp_path: Path):
    target = tmp_path / "new.hwpx"
    result = create_document(str(target))

    assert result["created"] is True
    assert open_doc(str(target)) is not None


def test_find_text(sample_file: Path):
    _append_paragraph(sample_file, "검색 대상 문장입니다")

    result = find_text(str(sample_file), "대상")

    assert result["total_matches"] >= 1
    first = result["matches"][0]
    assert "paragraph_index" in first
    assert "context" in first


def test_search_and_replace_rejects_empty_find(sample_file: Path):
    _append_paragraph(sample_file, "abc")

    with pytest.raises(ValueError):
        search_and_replace(str(sample_file), "", "X")


def test_batch_replace_rejects_empty_find(sample_file: Path):
    _append_paragraph(sample_file, "abc")

    with pytest.raises(ValueError):
        batch_replace(str(sample_file), [{"find": "", "replace": "X"}])


def test_get_document_text_uses_env_default_limit(sample_file: Path, monkeypatch: pytest.MonkeyPatch):
    _append_paragraph(sample_file, "가" * 300)
    monkeypatch.setenv("HWPX_MCP_MAX_CHARS", "40")

    result = get_document_text(str(sample_file))

    assert result["truncated"] is True
    assert len(result["text"]) == 40
