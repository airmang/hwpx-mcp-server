from pathlib import Path

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
