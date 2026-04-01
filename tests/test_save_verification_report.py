from __future__ import annotations

import zipfile
from pathlib import Path

from hwpx_mcp_server.core.document import create_blank, open_doc
from hwpx_mcp_server.hwpx_ops import HwpxOps
from hwpx_mcp_server.storage import (
    LocalDocumentStorage,
    build_hwpx_presave_snapshot,
    build_hwpx_verification_report,
)


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


def test_save_verification_report_tracks_pre_post_diff(tmp_path: Path) -> None:
    target = tmp_path / "verification-clean.hwpx"
    create_blank(str(target))

    storage = LocalDocumentStorage(base_directory=tmp_path, auto_backup=False)
    pre_save_snapshot = build_hwpx_presave_snapshot(target)

    doc = open_doc(str(target))
    doc.add_paragraph("verification paragraph")
    report = storage.save_document(doc, target)

    assert report["ok"] is True
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


def test_save_as_returns_verification_report(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    target = tmp_path / "saved.hwpx"
    create_blank(str(source))

    ops = HwpxOps(base_directory=tmp_path, auto_backup=False)
    result = ops.save_as(str(source), str(target))

    assert result["outPath"] == str(target)
    assert result["verificationReport"]["ok"] is True
    assert "diffSummary" in result["verificationReport"]
