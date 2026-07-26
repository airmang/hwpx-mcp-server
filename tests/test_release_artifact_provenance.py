# SPDX-License-Identifier: Apache-2.0
"""The one publish build must carry immutable hashes into remote verification."""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path
from typing import Any
from urllib.error import URLError

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
SBOM_STEP = "Generate release SBOM"
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
EXPECTED_SBOM_RUN = """\
python -m venv .sbom-runtime
.sbom-runtime/bin/python -m pip install dist/*.whl
python -m venv .sbom-tool
.sbom-tool/bin/python -m pip install "cyclonedx-bom==7.3.0"
mkdir -p release-artifacts
.sbom-tool/bin/cyclonedx-py environment .sbom-runtime/bin/python \\
  --pyproject pyproject.toml \\
  --mc-type application \\
  --output-reproducible \\
  --output-format JSON \\
  --output-file "release-artifacts/hwpx-mcp-server-${GITHUB_REF_NAME}.cdx.json"
"""
EXPECTED_RELEASE_STEPS = (
    (
        "Checkout repository",
        "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
        False,
        None,
        None,
        None,
        None,
    ),
    (
        "Set up Python",
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        False,
        {"python-version": "3.12"},
        None,
        None,
        None,
    ),
    (
        "Validate tag/version consistency",
        None,
        True,
        None,
        None,
        "bash",
        None,
    ),
    (
        "Extract latest changelog section for release notes",
        None,
        True,
        None,
        None,
        None,
        None,
    ),
    (BUILD_STEP, None, True, None, None, None, None),
    (SBOM_STEP, None, True, None, None, None, None),
    (
        "Publish package to PyPI",
        "pypa/gh-action-pypi-publish@ba38be9e461d3875417946c167d0b5f3d385a247",
        False,
        None,
        None,
        None,
        None,
    ),
    (
        "Create GitHub Release",
        "softprops/action-gh-release@3d0d9888cb7fd7b750713d6e236d1fcb99157228",
        False,
        {
            "body_path": "release_notes.md",
            "draft": False,
            "files": "dist/*\nrelease-artifacts/*\n",
            "prerelease": False,
        },
        None,
        None,
        None,
    ),
    (
        REMOTE_HASH_STEP,
        None,
        True,
        None,
        {"GH_TOKEN": "${{ github.token }}"},
        None,
        None,
    ),
)
EXPECTED_PREBUILD_RUN_SHA256 = {
    "Validate tag/version consistency": (
        "66946bfaef9961ce25e30cf8aed42ed2094b280f8972b65b3489d31e4fd12212"
    ),
    "Extract latest changelog section for release notes": (
        "13596e1604f36a15a740b8f64332da6aab4e032740bf33a890aae4befadb5314"
    ),
}
EXPECTED_PREPUBLISH_RUNS = {
    "Install test dependencies": """\
python -m pip install -e "../python-hwpx[visual,preview]"
python -m pip install -e ".[test,typecheck]"
""",
    "Check public repository hygiene": "python scripts/check_public_hygiene.py",
    "Run first-stage Ruff gate": "ruff check --select E9,F .",
    "Run release type and architecture gates": """\
python -m mypy
pyright --pythonpath "$(command -v python)"
python scripts/check_architecture_ratchets.py
""",
    "Run release-facing tests": "python -m pytest -q",
    "Verify generated ToolSpec documentation": """\
python scripts/render_tool_contract.py --check --skip-skill
python scripts/render_contract_delta.py --check
""",
}
FAIL_OPEN_RUN = re.compile(
    r"(?:^|[;&|])\s*(?:exit|return)\s+0+\b"
    r"|\|\|\s*(?:true|:)(?:\s|;|$)"
    r"|(?:^|\s)set\s+\+e(?:\s|;|$)"
    r"|(?:^|\s)trap\b[^\n]*\bERR\b",
    re.MULTILINE,
)


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


def _require_exact_named_runs(
    failures: list[str],
    *,
    job_name: str,
    job: dict[str, Any],
    expected: dict[str, str],
) -> None:
    for step_name, expected_run in expected.items():
        matches = [
            step
            for step in job.get("steps", [])
            if step.get("name") == step_name
        ]
        if len(matches) != 1 or matches[0].get("run") != expected_run:
            failures.append(f"{job_name} step must be exact: {step_name}")


def _release_safety_failures(workflow: str) -> list[str]:
    failures: list[str] = []
    try:
        parsed = yaml.safe_load(workflow)
        jobs: dict[str, dict[str, Any]] = parsed["jobs"]
        prepublish = jobs["prepublish"]
        release = jobs["release"]
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        return [f"invalid release workflow structure: {exc}"]

    if set(jobs) != {"prepublish", "release"}:
        failures.append("release workflow must contain only the two expected jobs")
    if parsed.get("defaults"):
        failures.append("release workflow must not override the default run shell")
    if release.get("needs") != "prepublish":
        failures.append("release must need prepublish")
    for job_name, job in (("prepublish", prepublish), ("release", release)):
        if job.get("defaults"):
            failures.append(f"{job_name} must not override the default run shell")
        if "if" in job:
            failures.append(f"{job_name} must not override dependency status")
        if job.get("continue-on-error", False):
            failures.append(f"{job_name} must not continue on error")
        for step in job.get("steps", []):
            shell = step.get("shell")
            if shell not in (None, "bash"):
                failures.append(
                    f"{job_name} step has an unsafe custom shell: "
                    f"{step.get('name', step.get('uses', '<unnamed>'))}"
                )
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
            run = step.get("run")
            if isinstance(run, str) and FAIL_OPEN_RUN.search(run):
                failures.append(
                    f"{job_name} step contains a fail-open shell construct: "
                    f"{step.get('name', '<unnamed>')}"
                )

    _require_exact_named_runs(
        failures,
        job_name="prepublish",
        job=prepublish,
        expected=EXPECTED_PREPUBLISH_RUNS,
    )
    for job_name, job in jobs.items():
        if job_name == "release":
            continue
        if any(
            str(step.get("uses", "")).startswith(
                (PYPI_ACTION, GITHUB_ACTION)
            )
            for step in job.get("steps", [])
        ):
            failures.append("publisher actions must exist only in release")

    steps = release.get("steps", [])
    release_step_identities = tuple(
        (
            step.get("name"),
            step.get("uses"),
            "run" in step,
            step.get("with"),
            step.get("env"),
            step.get("shell"),
            step.get("working-directory"),
        )
        for step in steps
    )
    if release_step_identities != EXPECTED_RELEASE_STEPS:
        failures.append("release steps must match the exact order and actions")
    for step_name, expected_digest in EXPECTED_PREBUILD_RUN_SHA256.items():
        matches = [step for step in steps if step.get("name") == step_name]
        run = matches[0].get("run") if len(matches) == 1 else None
        observed_digest = (
            hashlib.sha256(run.encode()).hexdigest()
            if isinstance(run, str)
            else None
        )
        if observed_digest != expected_digest:
            failures.append(f"release run must be frozen: {step_name}")
    build_steps = [step for step in steps if step.get("name") == BUILD_STEP]
    if len(build_steps) != 1:
        failures.append("release must have exactly one distribution build step")
        return failures
    build = build_steps[0].get("run", "")
    if build != EXPECTED_BUILD_RUN:
        failures.append("build step must match the frozen single-build procedure")
    if not _shell_run_is_fail_closed(build):
        failures.append("build step shell must fail closed")
    sbom_steps = [step for step in steps if step.get("name") == SBOM_STEP]
    if len(sbom_steps) != 1 or sbom_steps[0].get("run") != EXPECTED_SBOM_RUN:
        failures.append("SBOM step must match the frozen post-build procedure")
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
        (
            lambda text: text.replace(
                "jobs:\n",
                "defaults:\n"
                "  run:\n"
                "    shell: bash -c 'bash \"$1\" || true' -- {0}\n"
                "jobs:\n",
                1,
            ),
            "release workflow must not override the default run shell",
        ),
        (
            lambda text: text.replace(
                "  release:\n    needs:",
                "  release:\n"
                "    defaults:\n"
                "      run:\n"
                "        shell: bash -c 'bash \"$1\" || true' -- {0}\n"
                "    needs:",
                1,
            ),
            "release must not override the default run shell",
        ),
        (
            lambda text: text.replace(
                "        run: python -m pytest -q",
                "        run: |\n"
                "          python -m pytest -q || :",
                1,
            ),
            "prepublish step contains a fail-open shell construct",
        ),
        (
            lambda text: text.replace(
                "        run: python -m pytest -q",
                "        run: |\n"
                "          set +e\n"
                "          python -m pytest -q",
                1,
            ),
            "prepublish step contains a fail-open shell construct",
        ),
        (
            lambda text: text.replace(
                "      - name: Verify PyPI and GitHub release hashes\n",
                "      - name: Verify PyPI and GitHub release hashes\n"
                "        shell: bash -c 'bash \"$1\" || true' -- {0}\n",
                1,
            ),
            "release step has an unsafe custom shell",
        ),
        (
            lambda text: (
                text
                + "\n  rogue-publish:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - uses: pypa/gh-action-pypi-publish@"
                "ba38be9e461d3875417946c167d0b5f3d385a247\n"
            ),
            "release workflow must contain only the two expected jobs",
        ),
        (
            lambda text: text.replace(
                "      - name: Check public repository hygiene",
                "      - uses: pypa/gh-action-pypi-publish@"
                "ba38be9e461d3875417946c167d0b5f3d385a247\n"
                "      - name: Check public repository hygiene",
                1,
            ),
            "publisher actions must exist only in release",
        ),
        (
            lambda text: text.replace(
                "      - name: Generate release SBOM",
                "      - name: Replace checked artifacts\n"
                "        run: python scripts/rewrite_dist_and_manifest.py\n\n"
                "      - name: Generate release SBOM",
                1,
            ),
            "release steps must match the exact order and actions",
        ),
        (
            lambda text: text.replace(
                '          --output-file "release-artifacts/'
                'hwpx-mcp-server-${GITHUB_REF_NAME}.cdx.json"',
                '          --output-file "release-artifacts/'
                'hwpx-mcp-server-${GITHUB_REF_NAME}.cdx.json"\n'
                "          python scripts/rewrite_dist_and_manifest.py",
                1,
            ),
            "SBOM step must match the frozen post-build procedure",
        ),
        (
            lambda text: re.sub(
                r"\n      - name: Validate tag/version consistency\n.*?"
                r"(?=\n      - name: Extract latest changelog section)",
                "",
                text,
                count=1,
                flags=re.DOTALL,
            ),
            "release steps must match the exact order and actions",
        ),
        (
            lambda text: re.sub(
                r"(      - name: Validate tag/version consistency\n"
                r"        shell: bash\n)"
                r"        run: \|.*?"
                r"(?=\n      - name: Extract latest changelog section)",
                r"\1        run: true\n",
                text,
                count=1,
                flags=re.DOTALL,
            ),
            "release run must be frozen: Validate tag/version consistency",
        ),
        (
            lambda text: text.replace(
                "          draft: false",
                "          draft: true",
                1,
            ),
            "release steps must match the exact order and actions",
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
        "workflow-default-shell-wrapper",
        "job-default-shell-wrapper",
        "prepublish-colon-fallback",
        "prepublish-set-plus-e",
        "remote-step-shell-wrapper",
        "extra-publish-job",
        "prepublish-publisher",
        "extra-post-build-rewrite",
        "sbom-appended-rewrite",
        "remove-tag-gate",
        "replace-tag-gate",
        "draft-github-release",
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


class _PyPIResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


def test_verify_pypi_fetches_and_requires_the_exact_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier_module()
    expected = {
        "hwpx_mcp_server-5.1.1-py3-none-any.whl": "0" * 64,
        "hwpx_mcp_server-5.1.1.tar.gz": "1" * 64,
    }
    payload = {
        "urls": [
            {
                "filename": filename,
                "digests": {"sha256": digest},
            }
            for filename, digest in expected.items()
        ]
    }
    lookups: list[tuple[str, int]] = []

    def open_pypi(url: str, *, timeout: int):
        lookups.append((url, timeout))
        return _PyPIResponse()

    monkeypatch.setattr(verifier.urllib.request, "urlopen", open_pypi)
    monkeypatch.setattr(verifier.json, "load", lambda _response: payload)

    verifier.verify_pypi(expected, attempts=1, retry_seconds=0)

    assert lookups == [(verifier.PYPI_URL, 20)]


def test_verify_pypi_rejects_a_digest_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier_module()
    expected = {
        "hwpx_mcp_server-5.1.1-py3-none-any.whl": "0" * 64,
        "hwpx_mcp_server-5.1.1.tar.gz": "1" * 64,
    }
    payload = {
        "urls": [
            {
                "filename": filename,
                "digests": {"sha256": "f" * 64},
            }
            for filename in expected
        ]
    }
    monkeypatch.setattr(
        verifier.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _PyPIResponse(),
    )
    monkeypatch.setattr(verifier.json, "load", lambda _response: payload)

    with pytest.raises(RuntimeError, match="PyPI hashes differ"):
        verifier.verify_pypi(expected, attempts=1, retry_seconds=0)


def test_verify_pypi_exhausts_lookup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _verifier_module()
    attempts: list[str] = []

    def fail_lookup(*_args, **_kwargs):
        attempts.append("lookup")
        raise URLError("offline")

    monkeypatch.setattr(verifier.urllib.request, "urlopen", fail_lookup)
    monkeypatch.setattr(verifier.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="PyPI hash lookup failed"):
        verifier.verify_pypi(
            {"package.whl": "0" * 64, "package.tar.gz": "1" * 64},
            attempts=3,
            retry_seconds=0,
        )
    assert attempts == ["lookup", "lookup", "lookup"]


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
    states: list[str] = []

    def verify_state(tag: str) -> None:
        states.append(tag)

    def download(tag: str, directory: Path) -> None:
        assert tag == "v5.1.1"
        attempts.append(directory)
        if len(attempts) == 1:
            raise verifier.subprocess.CalledProcessError(1, ["gh"])
        (directory / "SHA256SUMS").write_bytes(manifest.read_bytes())
        for name, data in payloads.items():
            (directory / name).write_bytes(data)

    monkeypatch.setattr(verifier, "verify_github_release_state", verify_state)
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
    assert states == ["v5.1.1", "v5.1.1"]
    assert attempts[0] != attempts[1]
    assert asset_dir.is_dir()


@pytest.mark.parametrize(
    "stdout",
    (
        '{"tagName":"v5.1.1","isDraft":true,"isPrerelease":false}',
        '{"tagName":"v5.1.1","isDraft":false,"isPrerelease":true}',
        '{"tagName":"wrong","isDraft":false,"isPrerelease":false}',
    ),
    ids=("draft", "prerelease", "wrong-tag"),
)
def test_github_release_state_rejects_nonfinal_truth(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    verifier = _verifier_module()

    def view(command, **kwargs):
        assert command[-2:] == ["--json", "tagName,isDraft,isPrerelease"]
        assert kwargs == {
            "check": True,
            "capture_output": True,
            "text": True,
        }
        return verifier.subprocess.CompletedProcess(command, 0, stdout=stdout)

    monkeypatch.setattr(verifier.subprocess, "run", view)

    with pytest.raises(RuntimeError, match="GitHub release state differs"):
        verifier.verify_github_release_state("v5.1.1")
