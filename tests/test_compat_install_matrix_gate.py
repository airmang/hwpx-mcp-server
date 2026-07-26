from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

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
    assert "--skip-public-upgrade" in completed.stdout


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
