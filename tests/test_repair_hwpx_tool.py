from __future__ import annotations

import os
import shutil
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import pytest
import hwpx
from hwpx.tools.package_validator import validate_editor_open_safety
from hwpx.tools.package_validator import validate_package

import hwpx_mcp_server.server as server
from hwpx_mcp_server.hwpx_ops import HwpxOps
from hwpx_mcp_server.tool_contract import bound_tool_registry


def _sample_hwpx() -> Path:
    explicit_repo = os.environ.get("PYTHON_HWPX_REPO")
    core_repo = (
        Path(explicit_repo).expanduser().resolve()
        if explicit_repo
        else Path(hwpx.__file__).resolve().parents[2]
    )
    core_sample = core_repo / "examples" / "Skeleton.hwpx"
    return core_sample if core_sample.is_file() else Path(__file__).parent / "sample.hwpx"


_THINKFIRST_REGRESSION_RAW = os.environ.get("HWPX_STALE_LINESEG_REGRESSION_FIXTURE")
_THINKFIRST_REGRESSION = (
    Path(_THINKFIRST_REGRESSION_RAW).expanduser().resolve()
    if _THINKFIRST_REGRESSION_RAW
    else None
)


def _truncate_central_directory(source: Path, destination: Path) -> None:
    data = source.read_bytes()
    offset = data.index(b"PK\x01\x02")
    destination.write_bytes(data[:offset])


def test_repair_hwpx_tool_is_bound_to_the_canonical_registry() -> None:
    binding = bound_tool_registry().by_name()["repair_hwpx"]

    assert binding.function is server.repair_hwpx


def test_repair_hwpx_repack_produces_valid_package(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    shutil.copy2(_sample_hwpx(), source)
    output = tmp_path / "repaired.hwpx"
    ops = HwpxOps(base_directory=tmp_path)

    result = ops.repair_hwpx(str(source), str(output))

    assert result["recovered"] is False
    assert result["crcOk"] is True
    assert result["validatePackage"]["ok"] is True
    assert result["openSafety"]["ok"] is True
    assert output.is_file()
    assert validate_package(output).ok


@pytest.mark.skipif(
    _THINKFIRST_REGRESSION is None or not _THINKFIRST_REGRESSION.exists(),
    reason="set HWPX_STALE_LINESEG_REGRESSION_FIXTURE for the private regression fixture",
)
def test_repair_hwpx_repack_fixes_thinkfirst_stale_lineseg_regression(
    tmp_path: Path,
) -> None:
    assert _THINKFIRST_REGRESSION is not None
    initial = validate_editor_open_safety(_THINKFIRST_REGRESSION)
    assert not initial.ok
    assert "stale lineseg textpos" in initial.summary

    source = tmp_path / "stale-lineseg-source.hwpx"
    shutil.copy2(_THINKFIRST_REGRESSION, source)
    output = tmp_path / "stale-lineseg-repaired.hwpx"
    ops = HwpxOps(base_directory=tmp_path)

    result = ops.repair_hwpx(str(source), str(output))

    assert result["recovered"] is False
    assert result["crcOk"] is True
    assert result["validatePackage"]["ok"] is True
    assert result["openSafety"]["ok"] is True
    assert output.is_file()
    assert validate_editor_open_safety(output).ok


def test_repair_hwpx_fastmcp_function_accepts_new_output_path(tmp_path: Path) -> None:
    source = _sample_hwpx()
    output = tmp_path / "server-repaired.hwpx"

    result = server.repair_hwpx(str(source), str(output))

    assert result["outputPath"] == str(output)
    assert result["recovered"] is False
    assert result["crcOk"] is True
    assert result["validatePackage"]["ok"] is True
    assert result["openSafety"]["ok"] is True
    assert output.is_file()


def test_repair_hwpx_recover_rebuilds_truncated_central_directory(
    tmp_path: Path,
) -> None:
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
    assert result["openSafety"]["ok"] is True
    assert validate_package(output).ok
