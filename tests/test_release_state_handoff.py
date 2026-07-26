from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _release_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
    )
    return workflow["jobs"]["release"]["steps"]


def _prepublish_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
    )
    return workflow["jobs"]["prepublish"]["steps"]


def test_prepublish_full_suite_installs_the_owned_oracle_runtime() -> None:
    install = next(
        step
        for step in _prepublish_steps()
        if step.get("name") == "Install test dependencies"
    )
    assert 'python -m pip install -e ".[mcp,oracle,test,typecheck]"' in str(
        install["run"]
    )


def test_automation_release_hands_off_without_global_promotion() -> None:
    steps = _release_steps()
    names = [str(step.get("name", "")) for step in steps]
    compat_observed = names.index("Observe compatibility PyPI truth")
    receipt_written = names.index("Write release-approved plugin handoff receipt")
    github_created = names.index("Create GitHub Release")
    github_observed = names.index(
        "Observe automation GitHub Release and record plugin handoff"
    )
    assert compat_observed < receipt_written < github_created < github_observed

    receipt_run = str(steps[receipt_written]["run"])
    assert "python-hwpx-automation.plugin-handoff/v1" in receipt_run
    assert '"globalReleaseState": "release-approved"' in receipt_run
    assert '"currentPublic": current_public' in receipt_run
    assert '"promotionForbiddenUntilRemainingObserved": True' in receipt_run
    assert all(
        requirement in receipt_run
        for requirement in (
            "pluginGitHubRelease",
            "marketplaceEntry",
            "realMarketplaceInstall",
        )
    )

    handoff_run = str(steps[github_observed]["run"])
    assert "release-approved and currentPublic remains 4.2/5.1/0.8" in handoff_run
    assert "plugin GitHub Release, marketplace entry, and a real marketplace" in (
        handoff_run
    )
    whole_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "from release-approved to released" not in whole_workflow
    assert "promotes currentPublic" not in whole_workflow


def test_tag_release_requires_a_dated_changelog_heading() -> None:
    steps = _release_steps()
    validation = next(
        step
        for step in steps
        if step.get("name") == "Validate tag/version consistency"
    )
    run = str(validation["run"])
    assert r"(\d{{4}}-\d{{2}}-\d{{2}})" in run
    assert "'## [x.y.z] - YYYY-MM-DD'" in run
    assert "CHANGELOG_VERSION" in run


def test_identity_requires_complete_three_stack_remote_truth() -> None:
    identity = json.loads(
        (ROOT / "src" / "hwpx_automation" / "identity.json").read_text(
            encoding="utf-8"
        )
    )
    release = identity["releaseState"]
    assert release["status"] in {
        "unreleased-candidate",
        "release-approved",
        "released",
    }
    if release["status"] != "released":
        assert release["currentPublic"]["plugin"] == "0.8.0"
    else:
        assert release["currentPublic"]["plugin"] == release["candidate"]["plugin"]
    gate = release["promotionGate"]
    assert all(
        requirement in gate
        for requirement in (
            "core",
            "canonical automation",
            "compatibility distribution",
            "plugin GitHub release",
            "marketplace entry",
            "real marketplace install",
            "leaves currentPublic unchanged",
            "attached receipt",
        )
    )
