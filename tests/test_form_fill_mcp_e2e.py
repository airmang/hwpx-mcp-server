from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

import hwpx_mcp_server.server as server
from hwpx.tools.package_validator import validate_package
from hwpx.tools.validator import validate_document


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_template(path: Path, *, duplicate: bool = False) -> None:
    server.create_document(str(path))
    server.add_heading(str(path), "교과협의회 회의록", level=1)
    server.add_table(
        str(path),
        3,
        2,
        [["일시", ""], ["장소", ""], ["참석자", ""]],
    )
    if duplicate:
        server.add_heading(str(path), "후속 회의", level=2)
        server.add_table(str(path), 1, 2, [["일시", ""]])


def _structured_input() -> dict:
    return {
        "schemaVersion": "hwpx.formfill.v1",
        "source": {"type": "structured"},
        "fields": [
            {"key": "meeting.date", "label": "일시", "value": "2026-05-05 10:00"},
            {"key": "meeting.place", "label": "장소", "value": "AI실"},
            {"key": "meeting.attendees", "label": "참석자", "value": "김교사, 이교사"},
        ],
    }


def _write_minimal_docx(path: Path, rows: list[tuple[str, str]]) -> None:
    row_xml = []
    for label, value in rows:
        row_xml.append(
            "<w:tr>"
            f"<w:tc><w:p><w:r><w:t>{escape(label)}</w:t></w:r></w:p></w:tc>"
            f"<w:tc><w:p><w:r><w:t>{escape(value)}</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
        )
    document_xml = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
        "<w:body><w:tbl>" + "".join(row_xml) + "</w:tbl></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>")
        archive.writestr("word/document.xml", document_xml)


def test_form_fill_tools_are_exposed() -> None:
    names = set(server.mcp._tool_manager._tools.keys())

    assert {"analyze_form_fill", "apply_form_fill"}.issubset(names)


def test_analyze_form_fill_is_non_mutating_and_apply_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "meeting-template.hwpx"
    destination = tmp_path / "meeting-filled.hwpx"
    _build_template(source)
    source_hash_before = _sha256(source)
    source_mtime_before = source.stat().st_mtime_ns

    analysis = server.analyze_form_fill(
        str(source),
        input_json=_structured_input(),
        destination_filename=str(destination),
    )

    assert analysis["mutated"] is False
    assert analysis["source"]["unchanged_after_analysis"] is True
    assert analysis["resolved_count"] == 3
    assert analysis["unresolved_count"] == 0
    assert not destination.exists(), "analysis must not copy or fill the destination"
    assert _sha256(source) == source_hash_before

    result = server.apply_form_fill(analysis=analysis, confirm=True)

    assert result["handoff_status"] == "ready"
    assert result["source"]["preserved"] is True
    assert result["source"]["sha256_before"] == source_hash_before
    assert result["source"]["mtime_ns_before"] == source_mtime_before
    assert destination.exists()
    assert result["destination"]["changed"] is True
    assert result["validation"]["validate_structure"]["ok"] is True
    assert result["validation"]["validate_package"]["ok"] is True
    assert result["validation"]["validate_document"]["ok"] is True
    assert validate_package(destination).ok
    assert validate_document(destination).ok
    assert _sha256(source) == source_hash_before
    assert source.stat().st_mtime_ns == source_mtime_before

    filled = server.get_table_text(str(destination), 0)["data"]
    assert filled == [
        ["일시", "2026-05-05 10:00"],
        ["장소", "AI실"],
        ["참석자", "김교사, 이교사"],
    ]
    assert all(item["style_preserved"] for item in result["applied"])
    assert {item["text"] for item in result["touched"]} >= {"2026-05-05 10:00", "AI실", "김교사, 이교사"}


def test_duplicate_label_analysis_blocks_apply_until_explicit_coordinate(tmp_path: Path) -> None:
    source = tmp_path / "duplicate-template.hwpx"
    destination = tmp_path / "duplicate-filled.hwpx"
    _build_template(source, duplicate=True)

    ambiguous = server.analyze_form_fill(
        str(source),
        input_json={
            "schemaVersion": "hwpx.formfill.v1",
            "fields": [{"key": "date", "label": "일시", "value": "2026-05-05"}],
        },
        destination_filename=str(destination),
    )

    assert ambiguous["resolved_count"] == 0
    assert ambiguous["unresolved_count"] == 1
    assert ambiguous["mappings"]["unresolved"][0]["reason"] == "ambiguous label"
    assert ambiguous["mappings"]["unresolved"][0]["candidate_count"] == 2

    blocked = server.apply_form_fill(analysis=ambiguous, confirm=True)
    assert blocked["handoff_status"] == "blocked"
    assert not destination.exists()

    explicit = server.analyze_form_fill(
        str(source),
        input_json={
            "schemaVersion": "hwpx.formfill.v1",
            "fields": [
                {
                    "key": "date",
                    "label": "일시",
                    "value": "2026-05-05",
                    "target": {"kind": "cell", "table_index": 1, "row": 0, "col": 1},
                }
            ],
        },
        destination_filename=str(destination),
    )
    result = server.apply_form_fill(analysis=explicit, confirm=True)

    assert result["handoff_status"] == "ready"
    assert server.get_table_text(str(destination), 1)["data"][0][1] == "2026-05-05"
    assert server.get_table_text(str(destination), 0)["data"][0][1] == ""


def test_docx_origin_normalizes_to_same_fill_result_as_structured_json(tmp_path: Path) -> None:
    source = tmp_path / "docx-template.hwpx"
    from_json = tmp_path / "from-json.hwpx"
    from_docx = tmp_path / "from-docx.hwpx"
    docx = tmp_path / "meeting-input.docx"
    _build_template(source)
    _write_minimal_docx(
        docx,
        [("일시", "2026-05-05 10:00"), ("장소", "AI실"), ("참석자", "김교사, 이교사")],
    )

    json_analysis = server.analyze_form_fill(
        str(source),
        input_json=_structured_input(),
        destination_filename=str(from_json),
    )
    docx_analysis = server.analyze_form_fill(
        str(source),
        input_docx=str(docx),
        destination_filename=str(from_docx),
    )

    assert docx_analysis["canonicalInput"]["source"]["type"] == "docx"
    assert docx_analysis["resolved_count"] == json_analysis["resolved_count"] == 3

    json_result = server.apply_form_fill(analysis=json_analysis, confirm=True)
    docx_result = server.apply_form_fill(analysis=docx_analysis, confirm=True)

    assert json_result["handoff_status"] == "ready"
    assert docx_result["handoff_status"] == "ready"
    assert server.get_table_text(str(from_json), 0)["data"] == server.get_table_text(str(from_docx), 0)["data"]


def test_paragraph_placeholder_fill_preserves_paragraph_style(tmp_path: Path) -> None:
    source = tmp_path / "placeholder-template.hwpx"
    destination = tmp_path / "placeholder-filled.hwpx"
    _build_template(source)
    placeholder_index = server.add_paragraph(str(source), "안건: {{agenda_1}}")["paragraph_index"]
    source_hash_before = _sha256(source)

    analysis = server.analyze_form_fill(
        str(source),
        input_json={
            "schemaVersion": "hwpx.formfill.v1",
            "source": {"type": "structured"},
            "paragraphs": [
                {
                    "key": "agenda.1",
                    "text": "AI 활용 수업 설계",
                    "target": {"kind": "placeholder", "token": "{{agenda_1}}"},
                    "stylePolicy": "preserve-placeholder",
                }
            ],
        },
        destination_filename=str(destination),
    )
    result = server.apply_form_fill(analysis=analysis, confirm=True)

    assert result["handoff_status"] == "ready"
    assert result["source"]["preserved"] is True
    assert _sha256(source) == source_hash_before
    assert result["applied"][0]["replaced_count"] == 1
    assert result["applied"][0]["style_preserved"] is True
    assert result["touched"][0]["kind"] == "placeholder"
    assert result["touched"][0]["paragraph_index"] == placeholder_index
    assert server.get_paragraph_text(str(destination), placeholder_index)["text"] == "안건: AI 활용 수업 설계"


def test_existing_sample_hwpx_safe_coordinate_fill_smoke(tmp_path: Path) -> None:
    source = Path("tests/sample.hwpx")
    destination = tmp_path / "sample-filled.hwpx"
    source_hash_before = _sha256(source)
    source_mtime_before = source.stat().st_mtime_ns

    analysis = server.analyze_form_fill(
        str(source),
        input_json={
            "schemaVersion": "hwpx.formfill.v1",
            "source": {"type": "structured"},
            "fields": [
                {
                    "key": "sample.a2",
                    "label": "A2",
                    "value": "샘플 회귀 검증",
                    "target": {"kind": "cell", "table_index": 0, "row": 0, "col": 1},
                }
            ],
        },
        destination_filename=str(destination),
    )
    result = server.apply_form_fill(analysis=analysis, confirm=True)

    assert result["handoff_status"] == "ready"
    assert result["source"]["preserved"] is True
    assert _sha256(source) == source_hash_before
    assert source.stat().st_mtime_ns == source_mtime_before
    assert result["validation"]["validate_structure"]["ok"] is True
    assert validate_package(destination).ok
    assert validate_document(destination).ok
    assert server.get_table_text(str(destination), 0)["data"][0][1] == "샘플 회귀 검증"


def test_invalid_docx_returns_structured_recovery_hint(tmp_path: Path) -> None:
    source = tmp_path / "template.hwpx"
    bad_docx = tmp_path / "bad.docx"
    _build_template(source)
    bad_docx.write_text("not a zip", encoding="utf-8")

    with pytest.raises(ValueError, match="provide input_json"):
        server.analyze_form_fill(str(source), input_docx=str(bad_docx))
