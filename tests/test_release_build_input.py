from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_release_build_input.py"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _run_checker(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("relative_path", "error_code"),
    [
        ("build", "BUILD_DIR"),
        ("dist", "DIST_DIR"),
        ("src/python_hwpx_automation.egg-info", "EGG_INFO"),
        ("src/hwpx_automation/__pycache__", "PYCACHE_DIR"),
    ],
)
def test_each_pollution_class_fails_closed_with_an_explicit_error(
    tmp_path: Path,
    relative_path: str,
    error_code: str,
) -> None:
    release_root = tmp_path / "archive"
    polluted = release_root / relative_path
    polluted.mkdir(parents=True)

    completed = _run_checker(release_root)

    assert completed.returncode == 1
    assert f"[{error_code}]" in completed.stderr
    assert relative_path in completed.stderr
    assert "refusing to build" in completed.stderr


def test_clean_supplied_root_passes_without_scanning_the_development_checkout(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "archive"
    (release_root / "src" / "hwpx_automation").mkdir(parents=True)
    (release_root / "src" / "hwpx_automation" / "__init__.py").write_text(
        "",
        encoding="utf-8",
    )

    completed = _run_checker(release_root)

    assert completed.returncode == 0, completed.stderr
    assert "release build input is clean" in completed.stdout


def test_missing_supplied_root_fails_closed(tmp_path: Path) -> None:
    completed = _run_checker(tmp_path / "missing")

    assert completed.returncode == 2
    assert "scan failed closed" in completed.stderr


def test_release_builds_canonical_and_compat_from_one_exact_git_archive() -> None:
    workflow = re.sub(
        r"\\\n\s*",
        "",
        RELEASE_WORKFLOW.read_text(encoding="utf-8"),
    )

    archive = 'git archive --format=tar "${GITHUB_SHA}"'
    checker = (
        'python "${RELEASE_SOURCE}/scripts/check_release_build_input.py" '
        '--root "${RELEASE_SOURCE}"'
    )
    canonical_build = (
        'python -m build --outdir "${GITHUB_WORKSPACE}/dist/canonical" '
        '"${RELEASE_SOURCE}"'
    )
    compat_build = (
        'python -m build --outdir "${GITHUB_WORKSPACE}/dist/compat" '
        '"${RELEASE_SOURCE}/compat/hwpx-mcp-server"'
    )

    assert -1 not in (
        workflow.find(archive),
        workflow.find(checker),
        workflow.find(canonical_build),
        workflow.find(compat_build),
    )
    assert (
        workflow.find(archive)
        < workflow.find(checker)
        < workflow.find(canonical_build)
        < workflow.find(compat_build)
    )

    # Keep the two upload roots and distribution-name selectors exact.  A
    # broad dist/ upload would mix canonical and compatibility artifacts.
    assert "packages-dir: dist/canonical/" in workflow
    assert "packages-dir: dist/compat/" in workflow
    assert 'names != {"python_hwpx_automation"}' in workflow
    assert 'names != {"hwpx_mcp_server"}' in workflow
    assert "packages-dir: dist/" not in workflow.replace(
        "packages-dir: dist/canonical/",
        "",
    ).replace(
        "packages-dir: dist/compat/",
        "",
    )
