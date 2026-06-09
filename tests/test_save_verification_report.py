from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import hwpx_mcp_server.server as server_module
import hwpx_mcp_server.storage as storage_module
from hwpx_mcp_server.core.document import create_blank, open_doc
from hwpx_mcp_server.hwpx_ops import HwpxOps
from hwpx_mcp_server.storage import (
    LocalDocumentStorage,
    build_hwpx_open_safety_report,
    build_hwpx_presave_snapshot,
    build_hwpx_verification_report,
    require_hwpx_editor_open_safe,
)
from hwpx_mcp_server.upstream import new_document


def _write_minimal_hwpx(path: Path, section_xml: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr(
            "Contents/content.hpf",
            "<?xml version='1.0' encoding='UTF-8'?><hpf:content xmlns:hpf='http://www.hancom.co.kr/schema/2011/hpf' version='1.0'><hpf:metadata/></hpf:content>",
        )
        archive.writestr(
            "Contents/header.xml",
            "<?xml version='1.0' encoding='UTF-8'?><hh:head xmlns:hh='http://www.hancom.co.kr/hwpml/2011/head'><hh:docInfo/></hh:head>",
        )
        archive.writestr("Contents/section0.xml", section_xml)
        archive.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0' encoding='UTF-8'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'><Default Extension='xml' ContentType='application/xml'/></Types>",
        )


def _replace_zip_part(path: Path, part_name: str, payload: bytes) -> None:
    source_bytes = path.read_bytes()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(path, "r") as source:
        with zipfile.ZipFile(tmp_path, "w") as target:
            for info in source.infolist():
                data = payload if info.filename == part_name else source.read(info.filename)
                target.writestr(info, data)
    path.write_bytes(tmp_path.read_bytes())
    tmp_path.unlink()


def test_save_verification_report_tracks_pre_post_diff(tmp_path: Path) -> None:
    target = tmp_path / "verification-clean.hwpx"
    create_blank(str(target))

    storage = LocalDocumentStorage(base_directory=tmp_path, auto_backup=False)
    pre_save_snapshot = build_hwpx_presave_snapshot(target)

    doc = open_doc(str(target))
    doc.add_paragraph("verification paragraph")
    report = storage.save_document(doc, target)

    assert report["ok"] is True
    assert report["filePath"] == str(target)
    assert report["warnings"] == []
    assert report["totals"]["paragraphs"] >= 1
    assert report["diffSummary"]["paragraphs"] >= 1
    assert pre_save_snapshot is not None


def test_save_verification_report_flags_placeholders_and_suspicious_patterns(tmp_path: Path) -> None:
    target = tmp_path / "verification-suspicious.hwpx"
    _write_minimal_hwpx(
        target,
        """<?xml version='1.0' encoding='UTF-8'?>
<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section'
        xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>
  <hp:p id='1'><hp:run><hp:t>[NAME]</hp:t></hp:run></hp:p>
  <hp:p id='2'><hp:run><hp:t>Tom & Jerry</hp:t></hp:run></hp:p>
</hs:sec>""",
    )

    report = build_hwpx_verification_report(target)

    assert report["ok"] is False
    assert report["totals"]["placeholders"] > 0
    assert report["totals"]["suspiciousPatterns"] > 0
    assert any("placeholder-like tokens remain" in warning for warning in report["warnings"])
    assert any("suspicious XML/text patterns detected" in warning for warning in report["warnings"])


def test_open_safety_report_rejects_stale_lineseg_textpos(tmp_path: Path) -> None:
    target = tmp_path / "stale-lineseg.hwpx"
    create_blank(str(target))
    stale_section = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section' "
        "xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>"
        "<hp:p id='1' paraPrIDRef='0' styleIDRef='0' pageBreak='0' columnBreak='0' merged='0'>"
        "<hp:run charPrIDRef='0'><hp:t>Short</hp:t></hp:run>"
        "<hp:linesegarray><hp:lineseg textpos='40'/></hp:linesegarray>"
        "</hp:p></hs:sec>"
    ).encode("utf-8")
    _replace_zip_part(target, "Contents/section0.xml", stale_section)

    report = build_hwpx_open_safety_report(target)

    assert report["ok"] is False
    assert report["validatePackage"]["ok"] is False
    assert any("stale lineseg" in error for error in report["validatePackage"]["errors"])


def test_open_safety_report_fails_closed_when_package_validation_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "package-validation-raises.hwpx"
    create_blank(str(target))

    def fail_package_validation(_path: Path) -> object:
        raise RuntimeError("package validator crashed")

    monkeypatch.setattr(storage_module, "validate_package", fail_package_validation)

    report = build_hwpx_open_safety_report(target)

    assert report["ok"] is False
    assert report["summary"] == "package validation failed"
    assert report["validatePackage"]["ok"] is False
    assert report["validatePackage"]["errors"] == ["package validator crashed"]
    assert report["reopen"]["ok"] is True

    with pytest.raises(RuntimeError, match="package validation failed"):
        require_hwpx_editor_open_safe(target, role="test open")


def test_open_safety_report_fails_closed_when_reopen_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "reopen-fails.hwpx"
    create_blank(str(target))

    def fail_reopen(_path: Path) -> object:
        raise RuntimeError("cannot reopen generated file")

    monkeypatch.setattr(storage_module, "open_document", fail_reopen)

    report = build_hwpx_open_safety_report(target)

    assert report["ok"] is False
    assert report["summary"] == "reopen failed"
    assert report["validatePackage"]["ok"] is True
    assert report["reopen"] == {
        "ok": False,
        "error": "cannot reopen generated file",
    }

    with pytest.raises(RuntimeError, match="reopen failed"):
        require_hwpx_editor_open_safe(target, role="test open")


def test_open_doc_rejects_unsafe_local_hwpx(tmp_path: Path) -> None:
    target = tmp_path / "unsafe-open-doc.hwpx"
    create_blank(str(target))
    stale_section = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section' "
        "xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>"
        "<hp:p id='1' paraPrIDRef='0' styleIDRef='0' pageBreak='0' columnBreak='0' merged='0'>"
        "<hp:run charPrIDRef='0'><hp:t>Short</hp:t></hp:run>"
        "<hp:linesegarray><hp:lineseg textpos='40'/></hp:linesegarray>"
        "</hp:p></hs:sec>"
    ).encode("utf-8")
    _replace_zip_part(target, "Contents/section0.xml", stale_section)

    with pytest.raises(RuntimeError, match="open-safety"):
        open_doc(str(target))


def test_local_storage_open_rejects_unsafe_local_hwpx(tmp_path: Path) -> None:
    target = tmp_path / "unsafe-storage-open.hwpx"
    create_blank(str(target))
    stale_section = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section' "
        "xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>"
        "<hp:p id='1' paraPrIDRef='0' styleIDRef='0' pageBreak='0' columnBreak='0' merged='0'>"
        "<hp:run charPrIDRef='0'><hp:t>Short</hp:t></hp:run>"
        "<hp:linesegarray><hp:lineseg textpos='40'/></hp:linesegarray>"
        "</hp:p></hs:sec>"
    ).encode("utf-8")
    _replace_zip_part(target, "Contents/section0.xml", stale_section)
    storage = LocalDocumentStorage(base_directory=tmp_path, auto_backup=False)

    with pytest.raises(RuntimeError, match="open-safety"):
        storage.open_document(target.name)


def test_auto_backup_rejects_unsafe_hwpx_source_without_creating_backup(
    tmp_path: Path,
) -> None:
    target = tmp_path / "unsafe-backup-source.hwpx"
    create_blank(str(target))
    stale_section = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section' "
        "xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>"
        "<hp:p id='1' paraPrIDRef='0' styleIDRef='0' pageBreak='0' columnBreak='0' merged='0'>"
        "<hp:run charPrIDRef='0'><hp:t>Short</hp:t></hp:run>"
        "<hp:linesegarray><hp:lineseg textpos='40'/></hp:linesegarray>"
        "</hp:p></hs:sec>"
    ).encode("utf-8")
    _replace_zip_part(target, "Contents/section0.xml", stale_section)
    original_bytes = target.read_bytes()
    storage = LocalDocumentStorage(base_directory=tmp_path, auto_backup=True)
    doc = new_document()

    try:
        with pytest.raises(RuntimeError, match="backup source failed open-safety"):
            storage.save_document(doc, target)
    finally:
        doc.close()

    assert target.read_bytes() == original_bytes
    assert not target.with_suffix(".hwpx.bak").exists()


def test_open_safety_report_rejects_document_validation_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "document-validation-fail.hwpx"
    create_blank(str(target))

    class FailedDocumentReport:
        ok = False
        errors = ("broken section XML",)
        warnings = ()

    monkeypatch.setattr(
        "hwpx_mcp_server.storage.validate_document_path",
        lambda _path: FailedDocumentReport(),
    )

    report = build_hwpx_open_safety_report(target)

    assert report["ok"] is False
    assert report["summary"] == "document validation failed"
    assert report["validatePackage"]["ok"] is True
    assert report["validateDocument"]["ok"] is False
    assert report["reopen"]["ok"] is True


def test_local_open_allows_document_validation_only_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "document-validation-warning-open.hwpx"
    create_blank(str(target))

    class FailedDocumentReport:
        ok = False
        errors = ("schema warning only for open gate",)
        warnings = ()

    monkeypatch.setattr(
        "hwpx_mcp_server.storage.validate_document_path",
        lambda _path: FailedDocumentReport(),
    )

    doc = open_doc(str(target))
    try:
        assert doc is not None
    finally:
        doc.close()

    storage = LocalDocumentStorage(base_directory=tmp_path, auto_backup=False)
    doc, resolved = storage.open_document(target.name)
    try:
        assert resolved == target
    finally:
        doc.close()


def test_open_safety_report_fails_closed_when_classifier_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "missing-classifier.hwpx"
    create_blank(str(target))
    monkeypatch.setattr(storage_module, "is_editor_open_blocking_issue", None, raising=False)
    monkeypatch.setattr(
        storage_module,
        "_OPEN_SAFETY_CLASSIFIER_IMPORT_ERROR",
        ImportError("missing open-safety classifier"),
        raising=False,
    )

    report = build_hwpx_open_safety_report(target)

    assert report["ok"] is False
    assert report["validatePackage"]["ok"] is False
    assert "python-hwpx>=2.10.3 is required" in report["validatePackage"]["errors"][0]


def test_local_storage_does_not_replace_target_when_open_safety_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "preserve-on-fail.hwpx"
    create_blank(str(target))
    original_bytes = target.read_bytes()
    storage = LocalDocumentStorage(base_directory=tmp_path, auto_backup=False)
    doc = open_doc(str(target))
    doc.add_paragraph("this save should not replace target")

    monkeypatch.setattr(
        "hwpx_mcp_server.storage.build_hwpx_open_safety_report",
        lambda path: {
            "ok": False,
            "summary": "forced failure",
            "validatePackage": {"ok": False, "errors": ["forced failure"], "warnings": []},
            "validateDocument": {"ok": True, "errors": [], "warnings": []},
            "reopen": {"ok": True, "error": None},
        },
    )

    try:
        try:
            storage.save_document(doc, target)
        except RuntimeError as exc:
            assert "open-safety" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("save should fail")
    finally:
        doc.close()

    assert target.read_bytes() == original_bytes


def test_save_as_returns_verification_report(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    target = tmp_path / "saved.hwpx"
    create_blank(str(source))

    ops = HwpxOps(base_directory=tmp_path, auto_backup=False)
    result = ops.save_as(str(source), str(target))

    assert result["outPath"] == str(target)
    assert result["verificationReport"]["ok"] is True
    assert "diffSummary" in result["verificationReport"]


def _assert_save_evidence(result: dict, target: Path) -> None:
    assert result["openSafety"]["ok"] is True
    assert result["verificationReport"]["openSafety"]["ok"] is True
    assert result["verificationReport"]["filePath"] == str(target)


def test_public_edit_tools_return_open_safety_evidence(tmp_path: Path) -> None:
    tools = server_module

    cases = []

    paragraph_doc = tmp_path / "paragraph-tools.hwpx"
    tools.create_document(str(paragraph_doc))
    cases.append((tools.add_paragraph(str(paragraph_doc), "First paragraph"), paragraph_doc))
    cases.append((tools.add_heading(str(paragraph_doc), "Heading", level=1), paragraph_doc))
    cases.append((tools.insert_paragraph(str(paragraph_doc), 1, "Inserted"), paragraph_doc))
    cases.append((tools.delete_paragraph(str(paragraph_doc), 1), paragraph_doc))

    replace_doc = tmp_path / "replace-tools.hwpx"
    tools.create_document(str(replace_doc))
    tools.add_paragraph(str(replace_doc), "Alpha TOKEN")
    cases.append((tools.search_and_replace(str(replace_doc), "TOKEN", "VALUE"), replace_doc))
    cases.append(
        (
            tools.batch_replace(
                str(replace_doc),
                [{"find": "Alpha", "replace": "Beta"}, {"find": "VALUE", "replace": "DONE"}],
            ),
            replace_doc,
        )
    )
    anchor = tools.find_text(str(replace_doc), "DONE")["matches"][0]["anchor"]
    cases.append((tools.replace_by_anchor(str(replace_doc), anchor, "DONE", "FINAL"), replace_doc))
    cases.append((tools.replace_in_paragraph(str(replace_doc), "Beta", "Gamma", paragraph_index=1), replace_doc))
    cases.append((tools.format_text(str(replace_doc), 1, 0, 5, bold=True), replace_doc))
    cases.append((tools.create_custom_style(str(replace_doc), "EvidenceStyle", bold=True), replace_doc))
    cases.append((tools.add_page_break(str(replace_doc)), replace_doc))
    memo = tools.add_memo(str(replace_doc), 1, "memo")
    cases.append((memo, replace_doc))
    cases.append((tools.remove_memo(str(replace_doc), 1), replace_doc))

    table_doc = tmp_path / "table-tools.hwpx"
    tools.create_document(str(table_doc))
    cases.append((tools.add_table(str(table_doc), 2, 2, [["A", "B"], ["C", "D"]]), table_doc))
    cases.append((tools.set_table_cell_text(str(table_doc), 0, 0, 1, "changed"), table_doc))
    cases.append((tools.format_table(str(table_doc), 0, has_header_row=True), table_doc))
    cases.append((tools.merge_table_cells(str(table_doc), 0, 0, 0, 1, 1), table_doc))
    cases.append((tools.split_table_cell(str(table_doc), 0, 0, 0), table_doc))

    form_doc = tmp_path / "form-tools.hwpx"
    tools.create_document(str(form_doc))
    tools.add_table(str(form_doc), 1, 2, [["성명", ""]])
    cases.append((tools.fill_by_path(str(form_doc), {"성명 > right": "홍길동"}), form_doc))

    for result, target in cases:
        _assert_save_evidence(result, target)


def test_generated_document_save_preserves_existing_target_when_open_safety_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "generated.hwpx"
    create_blank(str(target))
    original = target.read_bytes()

    def blocked_verification(_path: Path) -> dict:
        return {
            "openSafety": {
                "ok": False,
                "summary": "forced generated save failure",
            }
        }

    monkeypatch.setattr(server_module, "build_hwpx_verification_report", blocked_verification)
    doc = new_document()
    try:
        with pytest.raises(RuntimeError, match="generated HWPX failed open-safety"):
            server_module._save_generated_document(doc, str(target))
    finally:
        doc.close()

    assert target.read_bytes() == original
    assert not list(tmp_path.glob(".generated.*.hwpx"))
