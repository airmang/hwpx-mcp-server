# SPDX-License-Identifier: Apache-2.0
"""Prove the release path states no version of its own.

This is deliberately a structural proof rather than a count. An earlier
proposal was to track "hand-edited release coordinates: 87 -> N"; a count
invites Goodhart, because moving a literal from one file to another improves it
without changing anything. The property that actually matters is that no file
on the release path can decide a version by itself.

The failure record this guards, from ``CHANGELOG.md``:

* stale train literal in the workflow -- ``v6.6.0``, ``v6.6.3``
* stale embedded probe pin in the compat matrix -- ``v6.3.0``, ``v6.5.0``,
  ``v6.6.2``
* stale frozen ``currentPublic`` dictionary -- ``v6.1.2``, ``v6.4.1``,
  ``v6.7.0``
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from release_coordinates import (
    Stack,
    check_promotion_not_premature,
    coordinates,
    handoff_summary,
    load_identity,
)

#: Any dotted release version. Matches 5.7.0, 6.7.1, 1.7.0, 4.2.0 alike.
VERSION_PATTERN = re.compile(r"\b\d+\.\d+\.\d+\b")

#: Files the release path executes. A version literal in any of them is a
#: coordinate that file decided on its own.
RELEASE_PATH_FILES = (
    Path(".github/workflows/release.yml"),
    Path("scripts/check_compat_install_matrix.py"),
    Path("scripts/release_coordinates.py"),
    Path("scripts/check_current_public_remote.py"),
)

#: Version-shaped strings that are not train coordinates.
#:
#: Each entry needs a reason. Tool pins are the versions of third-party
#: software the release path installs; they are unrelated to which train is
#: shipping and do not move train to train.
ALLOWED_NON_COORDINATE_VERSIONS = {
    "0.7.0",  # historical stack named in a code comment
    "7.3.0",  # cyclonedx-bom pin
    "1.2.0",  # historical stack named in a code comment
    "6.2.1",  # failure-tag citation in a comment
    "6.1.2",  # failure-tag citation in a comment
    "6.3.0",  # failure-tag citation in a comment
    "6.4.0",  # failure-tag citation in a comment
    "6.4.1",  # failure-tag citation in a comment
    "6.5.0",  # failure-tag citation in a comment
    "6.6.0",  # failure-tag citation in a comment
    "6.6.2",  # failure-tag citation in a comment
    "6.6.3",  # failure-tag citation in a comment
    "6.7.0",  # failure-tag citation in a comment
    "5.0.2",  # non-promotion currentPublic advance cited in a comment
    "1.0.1",  # non-promotion currentPublic advance cited in a comment
    "5.5.0",  # non-promotion currentPublic advance cited in a comment
    "1.4.1",  # non-promotion currentPublic advance cited in a comment
    "6.4.2",  # non-promotion currentPublic advance cited in a comment
    "5.6.0",  # tag-derivation refutation cited in a comment
    "6.6.4",  # tag-derivation refutation cited in a comment
    "6.0.0",  # major-establishment citation in a comment
    "5.1.0",  # legacy-modules-5.1.0.json: a frozen artifact's filename
}


def _identity() -> dict:
    return load_identity()


def _live_coordinate_values() -> set[str]:
    coords = coordinates(_identity())
    return {
        coords.candidate.core,
        coords.candidate.automation,
        coords.candidate.plugin,
        coords.compatibility_version,
        coords.current_public.core,
        coords.current_public.automation,
        coords.current_public.plugin,
        coords.legacy_core_version,
        coords.legacy_compatibility_version,
    }


@pytest.mark.parametrize("relative", RELEASE_PATH_FILES, ids=lambda p: str(p))
def test_release_path_states_no_train_coordinate(relative: Path) -> None:
    """No release-path file may contain a live train version literal."""

    text = (ROOT / relative).read_text(encoding="utf-8")
    live = _live_coordinate_values()
    found = sorted(
        {
            match
            for match in VERSION_PATTERN.findall(text)
            if match in live and match not in ALLOWED_NON_COORDINATE_VERSIONS
        }
    )
    assert not found, (
        f"{relative} restates the live train coordinates {found}. "
        "Read them from identity.json through scripts/release_coordinates.py "
        "instead; a literal here is how v6.6.0, v6.6.3, and v6.7.0 happened."
    )


def test_every_version_literal_is_declared() -> None:
    """A version-shaped string must be a declared allowance, not a surprise."""

    undeclared: dict[str, list[str]] = {}
    live = _live_coordinate_values()
    for relative in RELEASE_PATH_FILES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        for match in sorted(set(VERSION_PATTERN.findall(text))):
            if match in live or match in ALLOWED_NON_COORDINATE_VERSIONS:
                continue
            undeclared.setdefault(str(relative), []).append(match)
    assert not undeclared, (
        f"undeclared version literals on the release path: {undeclared}. "
        "Add each to ALLOWED_NON_COORDINATE_VERSIONS with the reason it is "
        "not a train coordinate, or derive it."
    )


def test_compat_matrix_probes_use_the_substitution_token() -> None:
    """The embedded probe sources must not restate the train version."""

    text = (SCRIPTS / "check_compat_install_matrix.py").read_text(encoding="utf-8")
    assert "@@TRAIN_VERSION@@" in text
    assert "@@BASE_PUBLIC_MODULE_COUNT@@" in text

    sys.path.insert(0, str(SCRIPTS))
    import check_compat_install_matrix as matrix

    resolved = matrix._resolve_probe('assert version("x") == "@@TRAIN_VERSION@@"')
    assert matrix.TRAIN_VERSION in resolved
    assert "@@" not in resolved

    with pytest.raises(RuntimeError, match="unsubstituted"):
        matrix._resolve_probe("assert x == @@NOT_DECLARED@@")


def test_base_public_module_count_is_derived_not_pinned() -> None:
    """The probe's module count must match this repository's own manifest."""

    sys.path.insert(0, str(SCRIPTS))
    import check_compat_install_matrix as matrix

    manifest = json.loads(
        (ROOT / "src" / "hwpx_automation" / "public-modules.json").read_text(
            encoding="utf-8"
        )
    )
    assert matrix.BASE_PUBLIC_MODULE_COUNT == str(
        manifest["basePublicModuleCount"]
    )


def test_premature_promotion_is_rejected() -> None:
    """A currentPublic copied from the candidate must fail before a tag."""

    identity = _identity()
    release_state = identity["releaseState"]
    release_state["status"] = "release-approved"
    candidate = release_state["candidate"]
    release_state["currentPublic"] = {
        "pythonHwpx": candidate["pythonHwpx"],
        "primaryDistribution": candidate["canonicalDistribution"],
        "primaryApplication": candidate["canonicalAutomation"],
        "plugin": candidate["plugin"],
        "contractHash": candidate["contractHash"],
    }
    errors = check_promotion_not_premature(coordinates(identity))
    assert errors
    assert "before the full" in errors[0]


def test_in_flight_train_passes() -> None:
    """The shape every published train actually had must pass."""

    identity = _identity()
    release_state = identity["releaseState"]
    release_state["status"] = "release-approved"
    release_state["currentPublic"] = {
        "pythonHwpx": "5.6.0",
        "primaryDistribution": "python-hwpx-automation",
        "primaryApplication": "6.6.4",
        "plugin": "1.6.0",
        "contractHash": "19898dba41495c47",
    }
    assert check_promotion_not_premature(coordinates(identity)) == []


def test_released_state_requires_the_promotion() -> None:
    """A released identity whose currentPublic lags the candidate fails."""

    identity = _identity()
    release_state = identity["releaseState"]
    release_state["status"] = "released"
    release_state["currentPublic"]["plugin"] = "0.0.1"
    errors = check_promotion_not_premature(coordinates(identity))
    assert errors
    assert "must point currentPublic at the promoted candidate" in errors[0]


def test_handoff_summary_reports_the_public_stack() -> None:
    """The prose the workflow prints is a projection, not a written string."""

    identity = _identity()
    identity["releaseState"]["currentPublic"].update(
        {"pythonHwpx": "9.9.9", "primaryApplication": "8.8.8", "plugin": "7.7.7"}
    )
    assert handoff_summary(coordinates(identity)).endswith("9.9.9/8.8.8/7.7.7.")


def test_coordinates_cli_verify_succeeds_on_the_committed_identity() -> None:
    """The dry run the release path uses must pass on what is committed."""

    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "release_coordinates.py"), "--verify"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert completed.returncode == 0, completed.stderr
    assert "release coordinates ok" in completed.stdout


def test_stack_triple_rendering() -> None:
    stack = Stack(core="1.2.3", automation="4.5.6", plugin="7.8.9", contract_hash="ab")
    assert stack.as_triple() == "1.2.3/4.5.6/7.8.9"
