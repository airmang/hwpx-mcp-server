from __future__ import annotations

import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_compat_install_matrix.py"
SPEC = importlib.util.spec_from_file_location("compat_install_matrix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)


def test_compat_install_matrix_has_an_executable_cli() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/check_compat_install_matrix.py", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--core-wheel" in completed.stdout
    assert "--legacy-core-version" in completed.stdout
    assert "--skip-public-upgrade" in completed.stdout


def _phase0_wheel(
    tmp_path: Path,
    *,
    requirement: str = "python-hwpx>=4.2.0,<5",
    extra_requirement: str | None = None,
) -> tuple[Path, list[str]]:
    path = tmp_path / "hwpx_mcp_server-5.1.1-py3-none-any.whl"
    modules = ["hwpx_mcp_server", "hwpx_mcp_server.office"]
    metadata_lines = [
        "Metadata-Version: 2.4",
        "Name: hwpx-mcp-server",
        "Version: 5.1.1",
        f"Requires-Dist: {requirement}",
    ]
    if extra_requirement is not None:
        metadata_lines.append(f"Requires-Dist: {extra_requirement}")
    metadata = "\n".join((*metadata_lines, "", ""))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("hwpx_mcp_server/__init__.py", "")
        archive.writestr("hwpx_mcp_server/office/__init__.py", "")
        archive.writestr(
            "hwpx_mcp_server-5.1.1.dist-info/METADATA",
            metadata,
        )
    return path, modules


def test_phase0_wheel_gate_requires_the_cap_and_frozen_modules(
    tmp_path: Path,
) -> None:
    wheel, modules = _phase0_wheel(tmp_path)
    receipt = matrix._validate_phase0_legacy_wheel(
        wheel,
        expected_version="5.1.1",
        expected_modules=modules,
    )
    assert receipt["coreRequirement"] == "python-hwpx<5,>=4.2.0"
    assert receipt["moduleCount"] == 2

    unsafe, _ = _phase0_wheel(tmp_path, requirement="python-hwpx>=4.2.0")
    with pytest.raises(RuntimeError, match=r">=4\.2\.0,<5"):
        matrix._validate_phase0_legacy_wheel(
            unsafe,
            expected_version="5.1.1",
            expected_modules=modules,
        )

    unsafe_extra, _ = _phase0_wheel(
        tmp_path,
        extra_requirement=(
            'python-hwpx[visual]>=4.2.0; extra == "oracle"'
        ),
    )
    with pytest.raises(RuntimeError, match="including extras"):
        matrix._validate_phase0_legacy_wheel(
            unsafe_extra,
            expected_version="5.1.1",
            expected_modules=modules,
        )

    near_major, _ = _phase0_wheel(
        tmp_path,
        requirement="python-hwpx>=4.2.0,<5.1,!=5.0.0",
    )
    with pytest.raises(RuntimeError, match=r">=4\.2\.0,<5"):
        matrix._validate_phase0_legacy_wheel(
            near_major,
            expected_version="5.1.1",
            expected_modules=modules,
        )


def test_phase0_install_pins_both_legacy_and_core(monkeypatch) -> None:
    calls: list[tuple[Path, tuple[str, ...]]] = []

    def record(python: Path, *arguments: str) -> None:
        calls.append((python, arguments))

    monkeypatch.setattr(matrix, "_pip", record)
    interpreter = Path("/isolated/bin/python")
    matrix._install_phase0_legacy(
        interpreter,
        legacy_version="5.1.1",
        core_version="4.2.0",
    )

    assert calls == [
        (
            interpreter,
            (
                "install",
                "python-hwpx==4.2.0",
                "hwpx-mcp-server==5.1.1",
            ),
        ),
        (interpreter, ("check",)),
    ]


def test_matrix_subprocesses_reject_poisoned_python_source_paths(
    tmp_path: Path,
) -> None:
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "checkout_shadow.py").write_text(
        "raise AssertionError('poisoned PYTHONPATH was imported')\n",
        encoding="utf-8",
    )
    completed = matrix._run(
        [
            sys.executable,
            "-c",
            (
                "import importlib.util, os; "
                "assert 'PYTHONPATH' not in os.environ; "
                "assert 'PYTHONHOME' not in os.environ; "
                "assert 'VIRTUAL_ENV' not in os.environ; "
                "assert importlib.util.find_spec('checkout_shadow') is None"
            ),
        ],
        cwd=tmp_path,
        env={
            "PYTHONPATH": str(poison),
            "PYTHONHOME": str(poison),
            "VIRTUAL_ENV": str(poison),
        },
    )
    assert completed.returncode == 0
