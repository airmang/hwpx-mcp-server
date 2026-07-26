# SPDX-License-Identifier: Apache-2.0
"""The one publish build must carry immutable hashes into remote verification."""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 CI lane
    import tomli as tomllib

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
VERIFY_SCRIPT = ROOT / "scripts" / "verify_release_hashes.py"
BUILD_STEP = "Build distributions"
PYPI_ACTION = "pypa/gh-action-pypi-publish@"
GITHUB_ACTION = "softprops/action-gh-release@"
REMOTE_HASH_STEP = "Verify PyPI and GitHub release hashes"
REMOTE_HASH_COMMAND = (
    "python scripts/verify_release_hashes.py "
    "--manifest release-artifacts/SHA256SUMS "
    '--asset-dir "${RUNNER_TEMP}/hwpx-mcp-server-release-assets" '
    '--tag "${GITHUB_REF_NAME}"'
)
EXPECTED_BUILD_RUN = """\
set -euo pipefail
export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"
test -n "${SOURCE_DATE_EPOCH}"
python -m pip install "build==1.5.0" "twine==6.2.0"
rm -rf dist build
python -m build
twine check dist/*
mkdir -p release-artifacts
python - <<'PY'
import hashlib
from pathlib import Path

artifacts = sorted(path for path in Path("dist").iterdir() if path.is_file())
if not artifacts:
    raise SystemExit("dist/ has no release artifacts")
lines = [
    f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
    for path in artifacts
]
Path("release-artifacts/SHA256SUMS").write_text(
    "\\n".join(lines) + "\\n",
    encoding="utf-8",
)
PY
python scripts/check_public_hygiene.py
"""


def _verifier_module():
    spec = importlib.util.spec_from_file_location(
        "verify_release_hashes",
        VERIFY_SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _replace_last(text: str, before: str, after: str) -> str:
    prefix, separator, suffix = text.rpartition(before)
    assert separator
    return prefix + after + suffix


def _shell_run_is_fail_closed(run: str) -> bool:
    lines = [line.strip() for line in run.splitlines() if line.strip()]
    if not lines or lines[0] != "set -euo pipefail":
        return False
    early_success = re.compile(
        r"(?:^|[;&|])\s*(?:exit|return)\s+0(?:\s|;|$)|\|\|\s*true(?:\s|;|$)"
    )
    return not any(early_success.search(line) for line in lines)


def _release_safety_failures(workflow: str) -> list[str]:
    failures: list[str] = []
    try:
        jobs: dict[str, dict[str, Any]] = yaml.safe_load(workflow)["jobs"]
        prepublish = jobs["prepublish"]
        release = jobs["release"]
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        return [f"invalid release workflow structure: {exc}"]

    if release.get("needs") != "prepublish":
        failures.append("release must need prepublish")
    for job_name, job in (("prepublish", prepublish), ("release", release)):
        if "if" in job:
            failures.append(f"{job_name} must not override dependency status")
        if job.get("continue-on-error", False):
            failures.append(f"{job_name} must not continue on error")
        for step in job.get("steps", []):
            if "if" in step:
                failures.append(
                    f"{job_name} step must not have a condition: "
                    f"{step.get('name', step.get('uses', '<unnamed>'))}"
                )
            if step.get("continue-on-error", False):
                failures.append(
                    f"{job_name} step must not continue on error: "
                    f"{step.get('name', step.get('uses', '<unnamed>'))}"
                )

    steps = release.get("steps", [])
    build_steps = [step for step in steps if step.get("name") == BUILD_STEP]
    if len(build_steps) != 1:
        failures.append("release must have exactly one distribution build step")
        return failures
    build = build_steps[0].get("run", "")
    if build != EXPECTED_BUILD_RUN:
        failures.append("build step must match the frozen single-build procedure")
    if not _shell_run_is_fail_closed(build):
        failures.append("build step shell must fail closed")
    build_tokens = (
        'export SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"',
        "python -m build",
        "twine check dist/*",
        'artifacts = sorted(path for path in Path("dist").iterdir() if path.is_file())',
        'Path("release-artifacts/SHA256SUMS").write_text(',
        "python scripts/check_public_hygiene.py",
    )
    if any(token not in build for token in build_tokens):
        failures.append("build step must build, hash, and hygiene-check the same dist")
    else:
        offsets = [build.index(token) for token in build_tokens]
        if offsets != sorted(offsets):
            failures.append("build/hash/hygiene operations are out of order")

    pypi = [
        (index, step)
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith(PYPI_ACTION)
    ]
    github = [
        (index, step)
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith(GITHUB_ACTION)
    ]
    build_index = steps.index(build_steps[0])
    if len(pypi) != 1 or pypi[0][0] <= build_index:
        failures.append("PyPI publish must consume the one checked build")
    elif pypi[0][1].get("with", {}).get("packages-dir", "dist/") != "dist/":
        failures.append("PyPI publish must use dist/")
    if len(github) != 1 or github[0][0] <= build_index:
        failures.append("GitHub release must consume the one checked build")
    elif not {"dist/*", "release-artifacts/*"} <= {
        line.strip()
        for line in github[0][1].get("with", {}).get("files", "").splitlines()
    }:
        failures.append("GitHub release must upload dist and provenance manifest")

    remote_steps = [
        (index, step)
        for index, step in enumerate(steps)
        if step.get("name") == REMOTE_HASH_STEP
    ]
    if (
        len(remote_steps) != 1
        or len(pypi) != 1
        or len(github) != 1
        or remote_steps[0][0] <= max(pypi[0][0], github[0][0])
    ):
        failures.append("remote hash verification must follow both publications")
    else:
        remote = remote_steps[0][1].get("run", "")
        if remote != REMOTE_HASH_COMMAND:
            failures.append("remote hash verifier command must be exact")
    return failures


def test_release_build_inputs_are_bounded_and_hash_manifest_is_uploaded() -> None:
    pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["build-system"]["requires"] == [
        "setuptools==83.0.0",
        "wheel==0.47.0",
    ]

    workflow = RELEASE.read_text(encoding="utf-8")
    assert _release_safety_failures(workflow) == []
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


@pytest.mark.parametrize(
    ("mutate", "expected"),
    (
        (
            lambda text: text.replace("needs: prepublish", "needs: []", 1),
            "release must need prepublish",
        ),
        (
            lambda text: text.replace(
                "  release:\n    needs:",
                "  release:\n    if: always()\n    needs:",
                1,
            ),
            "release must not override dependency status",
        ),
        (
            lambda text: text.replace(
                "      - name: Build distributions",
                "      - name: Build distributions\n        continue-on-error: true",
                1,
            ),
            "release step must not continue on error",
        ),
        (
            lambda text: text.replace(
                "      - name: Verify PyPI and GitHub release hashes",
                "      - name: Verify PyPI and GitHub release hashes\n"
                "        if: false",
                1,
            ),
            "release step must not have a condition",
        ),
        (
            lambda text: text.replace(
                "          set -euo pipefail\n"
                "          export SOURCE_DATE_EPOCH=",
                "          exit 0\n"
                "          set -euo pipefail\n"
                "          export SOURCE_DATE_EPOCH=",
                1,
            ),
            "build step shell must fail closed",
        ),
        (
            lambda text: _replace_last(
                text,
                "python scripts/check_public_hygiene.py",
                "python -m pip --version",
            ),
            "build step must build, hash, and hygiene-check the same dist",
        ),
        (
            lambda text: text.replace(
                'Path("dist").iterdir()',
                'Path("other-dist").iterdir()',
                1,
            ),
            "build step must build, hash, and hygiene-check the same dist",
        ),
        (
            lambda text: text.replace(
                "            dist/*\n",
                "            other-dist/*\n",
                1,
            ),
            "GitHub release must upload dist and provenance manifest",
        ),
        (
            lambda text: text.replace(
                "      - name: Verify PyPI and GitHub release hashes",
                "      - name: Hash verification removed",
                1,
            ),
            "remote hash verification must follow both publications",
        ),
        (
            lambda text: _replace_last(
                text,
                REMOTE_HASH_COMMAND,
                f"exit 00; {REMOTE_HASH_COMMAND}",
            ),
            "remote hash verifier command must be exact",
        ),
        (
            lambda text: text.replace(
                "python scripts/verify_release_hashes.py",
                "python -c pass",
                1,
            ),
            "remote hash verifier command must be exact",
        ),
    ),
    ids=(
        "remove-needs",
        "always-run-release",
        "continue-on-error",
        "conditional-remote-verification",
        "early-success-build",
        "remove-artifact-hygiene",
        "hash-another-directory",
        "upload-another-directory",
        "remove-remote-verification",
        "early-success-remote-verification",
        "remove-github-hash-check",
    ),
)
def test_release_provenance_mutations_fail_closed(
    mutate,
    expected: str,
) -> None:
    workflow = RELEASE.read_text(encoding="utf-8")

    failures = _release_safety_failures(mutate(workflow))

    assert any(expected in failure for failure in failures)


def _manifest_bytes(payloads: dict[str, bytes]) -> bytes:
    return (
        "\n".join(
            f"{hashlib.sha256(data).hexdigest()}  {name}"
            for name, data in payloads.items()
        )
        + "\n"
    ).encode()


def test_release_hash_verifier_checks_manifest_and_downloaded_assets(
    tmp_path: Path,
) -> None:
    verifier = _verifier_module()
    payloads = {
        "hwpx_mcp_server-5.1.1-py3-none-any.whl": b"wheel",
        "hwpx_mcp_server-5.1.1.tar.gz": b"sdist",
    }
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_bytes(_manifest_bytes(payloads))
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    (asset_dir / "SHA256SUMS").write_bytes(manifest.read_bytes())
    for name, data in payloads.items():
        (asset_dir / name).write_bytes(data)

    expected = verifier.read_manifest(manifest)
    verifier.verify_github_assets(
        expected,
        manifest=manifest,
        asset_dir=asset_dir,
    )

    (asset_dir / next(iter(payloads))).write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="GitHub hash differs"):
        verifier.verify_github_assets(
            expected,
            manifest=manifest,
            asset_dir=asset_dir,
        )


@pytest.mark.parametrize(
    "manifest_text",
    (
        "not-a-hash  package.whl\n",
        f"{'0' * 64}  nested/package.whl\n{'1' * 64}  package.tar.gz\n",
        f"{'0' * 64}  one.whl\n{'1' * 64}  two.whl\n",
    ),
    ids=("malformed", "path", "missing-sdist"),
)
def test_release_hash_verifier_rejects_ambiguous_manifests(
    tmp_path: Path,
    manifest_text: str,
) -> None:
    verifier = _verifier_module()
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(manifest_text, encoding="utf-8")

    with pytest.raises(ValueError):
        verifier.read_manifest(manifest)


def test_release_hash_verifier_main_wires_every_remote_check(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    verifier = _verifier_module()
    manifest = tmp_path / "SHA256SUMS"
    asset_dir = tmp_path / "assets"
    expected = {"package.whl": "0" * 64, "package.tar.gz": "1" * 64}
    calls: list[object] = []
    monkeypatch.setattr(verifier, "read_manifest", lambda path: expected)
    monkeypatch.setattr(
        verifier,
        "verify_pypi",
        lambda observed: calls.append(("pypi", observed)),
    )
    monkeypatch.setattr(
        verifier,
        "verify_github_release",
        lambda observed, **kwargs: calls.append(
            ("github", observed, kwargs)
        ),
    )

    assert (
        verifier.main(
            [
                "--manifest",
                str(manifest),
                "--asset-dir",
                str(asset_dir),
                "--tag",
                "v5.1.1",
            ]
        )
        == 0
    )
    assert calls == [
        ("pypi", expected),
        (
            "github",
            expected,
            {
                "manifest": manifest,
                "asset_dir": asset_dir,
                "tag": "v5.1.1",
            },
        ),
    ]


def test_github_release_readback_retries_with_fresh_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    verifier = _verifier_module()
    payloads = {
        "hwpx_mcp_server-5.1.1-py3-none-any.whl": b"wheel",
        "hwpx_mcp_server-5.1.1.tar.gz": b"sdist",
    }
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_bytes(_manifest_bytes(payloads))
    expected = verifier.read_manifest(manifest)
    attempts: list[Path] = []

    def download(tag: str, directory: Path) -> None:
        assert tag == "v5.1.1"
        attempts.append(directory)
        if len(attempts) == 1:
            raise verifier.subprocess.CalledProcessError(1, ["gh"])
        (directory / "SHA256SUMS").write_bytes(manifest.read_bytes())
        for name, data in payloads.items():
            (directory / name).write_bytes(data)

    monkeypatch.setattr(verifier, "download_github_assets", download)
    monkeypatch.setattr(verifier.time, "sleep", lambda _: None)
    asset_dir = tmp_path / "assets"

    verifier.verify_github_release(
        expected,
        manifest=manifest,
        asset_dir=asset_dir,
        tag="v5.1.1",
        attempts=2,
        retry_seconds=0,
    )

    assert len(attempts) == 2
    assert attempts[0] != attempts[1]
    assert asset_dir.is_dir()
