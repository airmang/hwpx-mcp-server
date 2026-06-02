from __future__ import annotations

from pathlib import Path
from zipfile import BadZipFile, ZipFile

from hwpx.tools.package_validator import validate_package

import hwpx_mcp_server.server as server
from hwpx_mcp_server.hwpx_ops import HwpxOps
from hwpx_mcp_server.tools import build_tool_definitions


def _sample_hwpx() -> Path:
    return Path(__file__).resolve().parents[1].parent / "python-hwpx" / "examples" / "Skeleton.hwpx"


def _truncate_central_directory(source: Path, destination: Path) -> None:
    data = source.read_bytes()
    offset = data.index(b"PK\x01\x02")
    destination.write_bytes(data[:offset])


def test_repair_hwpx_tool_definition_is_exposed() -> None:
    names = {definition.name for definition in build_tool_definitions()}

    assert "repair_hwpx" in names


def test_repair_hwpx_repack_produces_valid_package(tmp_path: Path) -> None:
    source = _sample_hwpx()
    output = tmp_path / "repaired.hwpx"
    ops = HwpxOps(base_directory=tmp_path)

    result = ops.repair_hwpx(str(source), str(output))

    assert result["recovered"] is False
    assert result["crcOk"] is True
    assert result["validatePackage"]["ok"] is True
    assert output.is_file()
    assert validate_package(output).ok


def test_repair_hwpx_fastmcp_function_accepts_new_output_path(tmp_path: Path) -> None:
    source = _sample_hwpx()
    output = tmp_path / "server-repaired.hwpx"

    result = server.repair_hwpx(str(source), str(output))

    assert result["outputPath"] == str(output)
    assert result["recovered"] is False
    assert result["crcOk"] is True
    assert result["validatePackage"]["ok"] is True
    assert output.is_file()


def test_repair_hwpx_recover_rebuilds_truncated_central_directory(tmp_path: Path) -> None:
    source = _sample_hwpx()
    broken = tmp_path / "broken.hwpx"
    output = tmp_path / "recovered.hwpx"
    _truncate_central_directory(source, broken)
    ops = HwpxOps(base_directory=tmp_path)

    try:
        with ZipFile(broken, "r") as archive:
            archive.infolist()
        zipfile_opened = True
    except BadZipFile:
        zipfile_opened = False

    result = ops.repair_hwpx(str(broken), str(output), recover=True)

    assert zipfile_opened is False
    assert result["recovered"] is True
    assert result["crcOk"] is True
    assert result["validatePackage"]["ok"] is True
    assert validate_package(output).ok
