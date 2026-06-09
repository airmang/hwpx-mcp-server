import subprocess

import pytest
from hwpx.document import HwpxDocument

from hwpx_mcp_server.hwp_converter import convert_hwp_to_hwpx
from hwpx_mcp_server.hwpx_ops import HwpxOps


def _table_count(document: HwpxDocument) -> int:
    tables = []
    for paragraph in document.paragraphs:
        tables.extend(paragraph.tables)
    return len(tables)


def test_convert_hwp_to_hwpx_maps_text_and_table(monkeypatch, tmp_path):
    source = tmp_path / "sample.hwp"
    source.write_bytes(b"dummy")
    output = tmp_path / "sample.hwpx"

    xml_payload = """
    <HwpDoc>
      <Paragraph><Text>첫 문단</Text></Paragraph>
      <Paragraph><Text>둘째 문단</Text></Paragraph>
      <TableControl>
        <TableRow>
          <TableCell><Paragraph><Text>A1</Text></Paragraph></TableCell>
          <TableCell><Paragraph><Text>B1</Text></Paragraph></TableCell>
        </TableRow>
        <TableRow>
          <TableCell><Paragraph><Text>A2</Text></Paragraph></TableCell>
          <TableCell><Paragraph><Text>B2</Text></Paragraph></TableCell>
        </TableRow>
      </TableControl>
      <OLE />
    </HwpDoc>
    """.strip()

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=["hwp5proc"], returncode=0, stdout=xml_payload, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = convert_hwp_to_hwpx(str(source), str(output))

    assert result.success is True
    assert result.paragraphs_converted == 2
    assert result.tables_converted == 1
    assert "OLE 개체" in result.skipped_elements
    assert result.open_safety["ok"] is True
    assert result.verification["openSafety"]["ok"] is True

    converted = HwpxDocument.open(output)
    assert len(list(converted.paragraphs)) >= 2
    assert _table_count(converted) == 1

    ops = HwpxOps(base_directory=tmp_path)
    validation = ops.validate_structure(output.name)
    assert validation["ok"] is True


def test_convert_hwp_to_hwpx_warns_for_unsupported(monkeypatch, tmp_path):
    source = tmp_path / "warn.hwp"
    source.write_bytes(b"dummy")
    output = tmp_path / "warn.hwpx"

    xml_payload = """
    <HwpDoc>
      <Paragraph><Text>본문</Text></Paragraph>
      <FootNote><Text>주석</Text></FootNote>
      <Revision><Text>변경</Text></Revision>
    </HwpDoc>
    """.strip()

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=["hwp5proc"], returncode=0, stdout=xml_payload, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = convert_hwp_to_hwpx(str(source), str(output))

    assert "각주/미주" in result.skipped_elements
    assert "변경 추적" in result.skipped_elements
    assert any("변환 제외 요소" in item for item in result.warnings)


def test_convert_hwp_to_hwpx_preserves_existing_output_when_open_safety_fails(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "unsafe-conversion.hwp"
    source.write_bytes(b"dummy")
    output = tmp_path / "existing.hwpx"
    HwpxDocument.new().save_to_path(output)
    original_output = output.read_bytes()
    xml_payload = "<HwpDoc><Paragraph><Text>본문</Text></Paragraph></HwpDoc>"

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=["hwp5proc"], returncode=0, stdout=xml_payload, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "hwpx_mcp_server.storage.build_hwpx_open_safety_report",
        lambda path: {
            "ok": False,
            "summary": "forced conversion safety failure",
            "validatePackage": {
                "ok": False,
                "errors": ["forced conversion safety failure"],
                "warnings": [],
            },
            "validateDocument": {"ok": True, "errors": [], "warnings": []},
            "reopen": {"ok": True, "error": None},
        },
    )

    with pytest.raises(RuntimeError, match="open-safety"):
        convert_hwp_to_hwpx(str(source), str(output))

    assert output.read_bytes() == original_output


def test_hwp_ops_convert_hwp_to_hwpx_returns_open_safety_evidence(monkeypatch, tmp_path):
    source = tmp_path / "ops.hwp"
    source.write_bytes(b"dummy")

    xml_payload = "<HwpDoc><Paragraph><Text>본문</Text></Paragraph></HwpDoc>"

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=["hwp5proc"], returncode=0, stdout=xml_payload, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ops = HwpxOps(base_directory=tmp_path)

    result = ops.convert_hwp_to_hwpx("ops.hwp", "ops.hwpx")

    assert result["success"] is True
    assert result["openSafety"]["ok"] is True
    assert result["verification"]["openSafety"]["ok"] is True
