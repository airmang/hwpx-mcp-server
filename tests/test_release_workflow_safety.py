# SPDX-License-Identifier: Apache-2.0
"""Freeze the canonical/compatibility release provenance chain."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
PYPI_ACTION = "pypa/gh-action-pypi-publish@"
GITHUB_ACTION = "softprops/action-gh-release@"
EXPECTED_TRIGGER = {
    "push": {
        "tags": [
            "v*",
            "[0-9]*",
        ],
    },
}
EXPECTED_STEP_IDENTITY_SHA256 = {
    "prepublish": (
        "acaff21a247f4466c9b744a5257c58304f71d2aa27b19b51dbe752c38545f521"
    ),
    "release": (
        "70f1a6828a7c6866764731eb837da0869769f00c83ec917793969cfd2138c618"
    ),
}
EXPECTED_RUN_SHA256 = {
    "prepublish": {
        "Observe Phase-0 legacy cap before resolving core 5": (
            "eeaa0090d37e4b8deb891f75111b21f16b632f9e8b47f72aaba89dbb6d568082"
        ),
        "Install test dependencies": (
            "84986e601ad2070b451fedfe1aff906f11b90a06a46ec4755d4bb2cdff012fbd"
        ),
        "Install uv for the clean installed-wheel gates": (
            "a528bb5ef5a4d3201a325ff8ca5170f2e72d275a5fb8a31d95ccd0dd2cab5e6b"
        ),
        "Observe public core dependency remote truth": (
            "6f6ec96a0d485b388bd1977bddb35ea037143b488fc6df903c0b8d876f2bee92"
        ),
        "Check public repository hygiene": (
            "d217752235a4a35f99b76ef367b28bc309a23efd6b9004666615f27291d8acc2"
        ),
        "Run first-stage Ruff gate": (
            "174e5ab8ced80696ebbd1f22745dd92c04645911d776e64130af215722fd5eb7"
        ),
        "Run release type and architecture gates": (
            "31ae80dacdb42300a1f2504ae4887f99306f213cb960749b200ba31895909632"
        ),
        "Run release-facing tests": (
            "03b2d5596d232a4210386ff5610b26c7bb59cd1aa14f9531d5121955881b3d13"
        ),
        "Run public 5.3.0 compatibility install matrix": (
            "4ab55ada9a5fa9951fd79cdd20f2ef55831e0b08803c8530d28d0d98b19a4a1c"
        ),
        "Verify generated ToolSpec documentation": (
            "c7a25a48d6118f2932df62fe23211794b2729991ce6354671c8524f20e838254"
        ),
        "Gate minimum-Python clean wheel and optional boundaries": (
            "e1e0bd65686b98c86ff313f58cf2e5d3d808ca5312cf37993ad2d06dad66be55"
        ),
    },
    "release": {
        "Validate tag/version consistency": (
            "2749450d3b7b12ac17739c8de0056af466033f4f384ddce08ea970e64433899c"
        ),
        "Extract latest changelog section for release notes": (
            "13596e1604f36a15a740b8f64332da6aab4e032740bf33a890aae4befadb5314"
        ),
        "Prepare exact git-archive release input": (
            "b5f410911948c927b49427106e5566d4adcc881fac98e3a2a4fb2379e0a76414"
        ),
        "Build canonical distribution": (
            "5dcd20cf8da94cc9a39d86473647244c326d40d2aa75294298480f4fd7f14b7b"
        ),
        "Prebuild compatibility distribution before any publish": (
            "db0dbc398d6a6344b83313a33491171cd7e6d28020bdca11d2a44d2eed81208a"
        ),
        "Observe canonical PyPI truth before compatibility publish": (
            "aba882953b7b710e5499e8a2986ecba2f80d28e2fd5158bb33804fb3e8fe88ee"
        ),
        "Verify compatibility artifact resolves canonical remote": (
            "3d04e80f1cd3121907b3e20462810cb001efc5e8afbc975cbdc55e60b1421e9d"
        ),
        "Observe compatibility PyPI truth": (
            "42525afa5049f350e9abe5050c0f5562c1ffc60c135c3615d645123a98a59452"
        ),
        "Write release-approved plugin handoff receipt": (
            "6608fd4b6ca4ac1685cd0af5a9edb3ec101bdc2e9d3a165119d5c340d33ddc9f"
        ),
        "Generate release SBOM after both automation PyPI observations": (
            "c758216698c1c22d1a9706eca43e21d12825dcc61f24b6a6a7776b3c6cffbdaa"
        ),
        "Observe automation GitHub Release and record plugin handoff": (
            "e648df41778bef22db7952b5e23312b6d0a95de106f052d1703df4ebca5729c2"
        ),
    },
}
EXPECTED_BUILD_RUNS = {
    "Build canonical distribution": """\
python -m pip install --upgrade build twine
rm -rf dist
python -m build --outdir "${GITHUB_WORKSPACE}/dist/canonical" \\
  "${RELEASE_SOURCE}"
twine check dist/canonical/*
python scripts/check_public_hygiene.py
python - <<'CHECK'
import pathlib, sys
names = {
    p.name.split("-")[0]
    for p in pathlib.Path("dist/canonical").glob("*.whl")
}
if names != {"python_hwpx_automation"}:
    sys.exit(f"canonical artifact selection failed: {sorted(names)}")
CHECK
""",
    "Prebuild compatibility distribution before any publish": """\
rm -rf dist/compat
python -m build --outdir "${GITHUB_WORKSPACE}/dist/compat" \\
  "${RELEASE_SOURCE}/compat/hwpx-mcp-server"
twine check dist/compat/*
# The hygiene scanner walks dist recursively. Run it again only after
# the compatibility archives exist so all four release artifacts are
# covered before the first upload.
python scripts/check_public_hygiene.py
python - <<'CHECK'
import pathlib, sys
names = {
    p.name.split("-")[0]
    for p in pathlib.Path("dist/compat").glob("*.whl")
}
if names != {"hwpx_mcp_server"}:
    sys.exit(f"compat artifact selection failed: {sorted(names)}")
CHECK
""",
}
EXPECTED_PUBLISHERS = {
    "Publish canonical automation to PyPI": "dist/canonical/",
    "Publish compatibility shell to PyPI": "dist/compat/",
}
FAIL_OPEN_RUN = re.compile(
    r"(?:^|[;&|])\s*(?:exit|return)\s+0+\b"
    r"|\|\|\s*(?:true|:)(?:\s|;|$)"
    r"|(?:^|\s)set\s+\+e(?:\s|;|$)"
    r"|(?:^|\s)trap\b[^\n]*\bERR\b",
    re.MULTILINE,
)


def _trigger(parsed: dict[object, Any]) -> object:
    """Return the literal ``on`` block despite PyYAML's YAML-1.1 bool key."""

    return parsed["on"] if "on" in parsed else parsed.get(True)


def _step_identity_digest(steps: list[dict[str, Any]]) -> str:
    keys = ("name", "uses", "with", "env", "shell", "working-directory")
    identities = [
        {
            **{key: step[key] for key in keys if key in step},
            "has_run": "run" in step,
        }
        for step in steps
    ]
    payload = json.dumps(
        identities,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _run_digests(steps: list[dict[str, Any]]) -> dict[str, str]:
    runs: dict[str, str] = {}
    for step in steps:
        run = step.get("run")
        name = step.get("name")
        if not isinstance(run, str) or not isinstance(name, str):
            continue
        if name in runs:
            return {}
        runs[name] = hashlib.sha256(run.encode()).hexdigest()
    return runs


def _workflow_safety_failures(workflow: str) -> list[str]:
    failures: list[str] = []
    try:
        parsed = yaml.safe_load(workflow)
        if not isinstance(parsed, dict):
            raise TypeError("workflow must be a mapping")
        jobs: dict[str, dict[str, Any]] = parsed["jobs"]
        prepublish = jobs["prepublish"]
        release = jobs["release"]
    except (KeyError, TypeError, yaml.YAMLError) as exc:
        return [f"invalid release job structure: {exc}"]

    if _trigger(parsed) != EXPECTED_TRIGGER:
        failures.append("release trigger must be exactly tag-push-only")
    if set(jobs) != {"prepublish", "release"}:
        failures.append("release workflow must contain only the two expected jobs")
    if parsed.get("defaults"):
        failures.append("release workflow must not override the default run shell")
    if release.get("needs") != "prepublish":
        failures.append("release must need prepublish")

    for job_name, job in (("prepublish", prepublish), ("release", release)):
        if job.get("runs-on") != "ubuntu-latest":
            failures.append(f"{job_name} must use the frozen runner")
        if job.get("defaults"):
            failures.append(f"{job_name} must not override the default run shell")
        if "if" in job:
            failures.append(f"{job_name} must not override dependency status")
        if "continue-on-error" in job:
            failures.append(f"{job_name} must not declare continue-on-error")
        steps = job.get("steps")
        if not isinstance(steps, list) or not all(
            isinstance(step, dict) for step in steps
        ):
            failures.append(f"{job_name} steps must be a list of mappings")
            continue

        for step in steps:
            label = step.get("name", step.get("uses", "<unnamed>"))
            shell = step.get("shell")
            if shell not in (None, "bash"):
                failures.append(
                    f"{job_name} step has an unsafe custom shell: {label}"
                )
            if "if" in step:
                failures.append(f"{job_name} step must not have a condition: {label}")
            if "continue-on-error" in step:
                failures.append(
                    f"{job_name} step must not declare continue-on-error: {label}"
                )
            run = step.get("run")
            if isinstance(run, str) and FAIL_OPEN_RUN.search(run):
                failures.append(
                    f"{job_name} step contains a fail-open shell construct: {label}"
                )
            uses = step.get("uses")
            if isinstance(uses, str) and not uses.startswith(("./", "docker://")):
                ref = uses.rsplit("@", 1)[-1] if "@" in uses else ""
                if not re.fullmatch(r"[0-9a-f]{40}", ref):
                    failures.append(
                        f"{job_name} action must use a 40-hex pin: {label}"
                    )

        if (
            _step_identity_digest(steps)
            != EXPECTED_STEP_IDENTITY_SHA256[job_name]
        ):
            failures.append(f"{job_name} steps must match the exact frozen chain")
        if _run_digests(steps) != EXPECTED_RUN_SHA256[job_name]:
            failures.append(f"{job_name} run commands must remain frozen")

    release_steps = release.get("steps", [])
    if not isinstance(release_steps, list):
        return failures

    named_release_steps = {
        step.get("name"): step
        for step in release_steps
        if isinstance(step, dict) and isinstance(step.get("name"), str)
    }
    for name, expected_run in EXPECTED_BUILD_RUNS.items():
        step = named_release_steps.get(name)
        if step is None or step.get("run") != expected_run:
            failures.append(f"build command must remain exact: {name}")

    publishers = [
        (job_name, step)
        for job_name, job in jobs.items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith(PYPI_ACTION)
    ]
    if len(publishers) != 2:
        failures.append("release workflow must have exactly two PyPI publishers")
    else:
        observed_publishers = {
            str(step.get("name")): step.get("with", {}).get("packages-dir")
            for job_name, step in publishers
            if job_name == "release"
        }
        if (
            len(observed_publishers) != 2
            or observed_publishers != EXPECTED_PUBLISHERS
        ):
            failures.append(
                "PyPI publishers must use the distinct canonical and compat roots"
            )

    github_steps = [
        (job_name, step)
        for job_name, job in jobs.items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith(GITHUB_ACTION)
    ]
    if len(github_steps) != 1 or github_steps[0][0] != "release":
        failures.append("release workflow must have exactly one GitHub publisher")
    else:
        release_with = github_steps[0][1].get("with", {})
        if (
            release_with.get("draft") is not False
            or release_with.get("prerelease") is not False
        ):
            failures.append(
                "GitHub Release must explicitly set draft and prerelease false"
            )
    return failures


def test_release_workflow_provenance_is_frozen() -> None:
    assert _workflow_safety_failures(RELEASE.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("mutate", "expected"),
    (
        (
            lambda text: text.replace("          draft: false", "          draft: true"),
            "GitHub Release must explicitly set draft and prerelease false",
        ),
        (
            lambda text: text.replace(
                "      - name: Create GitHub Release\n",
                "      - name: Publish whole dist to PyPI\n"
                "        uses: pypa/gh-action-pypi-publish@"
                "ba38be9e461d3875417946c167d0b5f3d385a247\n"
                "        with:\n"
                "          packages-dir: dist/\n\n"
                "      - name: Create GitHub Release\n",
                1,
            ),
            "exactly two PyPI publishers",
        ),
        (
            lambda text: text.replace(
                "      - name: Prebuild compatibility distribution before any publish\n",
                "      - name: Rewrite checked canonical wheel\n"
                "        run: python scripts/rewrite_dist.py dist/canonical/*.whl\n\n"
                "      - name: Prebuild compatibility distribution before any publish\n",
                1,
            ),
            "release steps must match the exact frozen chain",
        ),
        (
            lambda text: text.replace(
                "  push:\n    tags:",
                '  push:\n    branches: ["**"]\n    tags:',
                1,
            ),
            "release trigger must be exactly tag-push-only",
        ),
        (
            lambda text: text.replace(
                "          done\n\n      - name: Verify compatibility",
                "          done || true\n\n      - name: Verify compatibility",
                1,
            ),
            "release step contains a fail-open shell construct",
        ),
        (
            lambda text: text.replace(
                '            | grep -Fx "${GITHUB_REF_NAME}"',
                '            | grep -Fx "${GITHUB_REF_NAME}" || true',
                1,
            ),
            "release step contains a fail-open shell construct",
        ),
        (
            lambda text: text.replace(
                "      - name: Publish canonical automation to PyPI\n",
                "      - name: Publish canonical automation to PyPI\n"
                "        continue-on-error: true\n",
                1,
            ),
            "release step must not declare continue-on-error",
        ),
        (
            lambda text: text.replace("    needs: prepublish\n", "", 1),
            "release must need prepublish",
        ),
        (
            lambda text: text.replace(
                "  release:\n    needs: prepublish",
                "  release:\n    if: always()\n    needs: prepublish",
                1,
            ),
            "release must not override dependency status",
        ),
        (
            lambda text: text.replace(
                "\npermissions:",
                "\n  workflow_dispatch:\n\npermissions:",
                1,
            ),
            "release trigger must be exactly tag-push-only",
        ),
        (
            lambda text: text.replace('      - "v*"', '      - "*"', 1),
            "release trigger must be exactly tag-push-only",
        ),
        (
            lambda text: text.replace(
                "      - name: Observe canonical PyPI truth before compatibility publish\n"
                "        shell: bash\n",
                "      - name: Observe canonical PyPI truth before compatibility publish\n"
                "        shell: bash -c 'bash \"$1\" || true' -- {0}\n",
                1,
            ),
            "release step has an unsafe custom shell",
        ),
    ),
    ids=(
        "draft-github-release",
        "third-publisher",
        "post-build-rewrite-step",
        "branch-and-tag-trigger",
        "pypi-readback-fail-open",
        "github-readback-fail-open",
        "canonical-publish-continue-on-error",
        "remove-prepublish-needs",
        "release-always",
        "workflow-dispatch-trigger",
        "widen-tag-glob",
        "custom-shell-wrapper",
    ),
)
def test_release_workflow_mutations_are_rejected(
    mutate,
    expected: str,
) -> None:
    workflow = RELEASE.read_text(encoding="utf-8")
    mutated = mutate(workflow)

    assert mutated != workflow
    failures = _workflow_safety_failures(mutated)

    assert any(expected in failure for failure in failures), failures
