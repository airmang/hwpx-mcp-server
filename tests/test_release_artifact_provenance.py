# SPDX-License-Identifier: Apache-2.0
"""The one publish build must carry immutable hashes into remote verification."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 CI lane
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_release_build_inputs_are_bounded_and_hash_manifest_is_uploaded() -> None:
    pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["build-system"]["requires"] == [
        "setuptools==83.0.0",
        "wheel==0.47.0",
    ]

    workflow = (
        ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    build_name = "- name: Build distributions"
    publish_name = "- name: Generate release SBOM"
    build = workflow[workflow.index(build_name) : workflow.index(publish_name)]

    assert "set -euo pipefail" in build
    assert 'export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"' in build
    assert 'test -n "${SOURCE_DATE_EPOCH}"' in build
    assert '"build==1.5.0"' in build
    assert '"twine==6.2.0"' in build
    assert "python -m build" in build
    assert 'Path("release-artifacts/SHA256SUMS").write_text(' in build
    assert "hashlib.sha256(path.read_bytes()).hexdigest()" in build

    release = workflow[workflow.index(publish_name) :]
    assert "pypa/gh-action-pypi-publish@" in release
    assert "softprops/action-gh-release@" in release
    assert "release-artifacts/*" in release
