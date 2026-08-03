#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Derive every release-path version coordinate from ``identity.json``.

Why this module exists
======================

Before this script the release path restated the same coordinates as hand
edited literals in three files: ``.github/workflows/release.yml`` (the core
version to observe, the frozen ``currentPublic`` dictionary, and a prose
summary line), ``scripts/check_transition_identity.py`` (a verbatim assertion
on that prose), and ``tests/test_release_state_handoff.py`` (two more verbatim
assertions plus a hardcoded plugin version).  Roughly seventeen literals had to
advance in lockstep every train.  Nine of the thirteen preserved failure tags
recorded in ``CHANGELOG.md`` came from advancing some of them and missing one.

The single source of truth is ``identity.json``.  Everything the release path
needs is a projection of ``releaseState`` or ``releaseFloors``.

Two derivations that look correct and are not
---------------------------------------------

Both were checked against real history before this module was written; both are
recorded here so they are not proposed again.

1. *Derive the expected ``currentPublic`` from the most recent released tag.*
   Refuted: ``git show v6.6.4:src/hwpx_automation/identity.json`` reports
   ``pythonHwpx == "5.5.0"`` while the tree that produced the following train
   reports ``"5.6.0"``.  Promotion happens in a follow-up commit *after* the
   tag, so a tag-derived expectation is always one train stale and reproduces
   the exact failure it is meant to prevent.

2. *Derive it from the most recent ``promote to released`` commit.*  Also
   refuted: ``currentPublic`` legitimately advanced in three commits that are
   not promotions -- ``bdb606b`` (the 6.0 establishment), ``b294337`` (a
   core-only patch train that moved core to 5.0.2 and the plugin to 1.0.1
   without an automation release), and ``254a2c0`` (a core-and-plugin train
   that moved core to 5.5.0 and the plugin to 1.4.1 while automation stayed at
   6.4.2).  A commit-message derivation fails on exactly those trains.

``currentPublic`` is a claim about the outside world -- what is actually
installable right now -- not a fact about this repository's history.  So the
witness that the old frozen dictionary provided is preserved here by two
mechanisms that need no hand editing:

* :func:`check_promotion_not_premature` -- structural.  While the state is
  ``release-approved`` the public stack must still differ from the candidate.
* :func:`remote_witness_targets` -- external.  Every coordinate
  ``currentPublic`` names must be observable on a service we do not control.

The second is the stronger witness and satisfies the standing gate rule from
``docs/2026-08-03-twelve-month-direction.md``: a gate must include an input we
did not author or an observer we do not control.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = ROOT / "src" / "hwpx_automation" / "identity.json"

#: Release states in which a tag may publish.
PUBLISHABLE_STATUS = "release-approved"

#: Every state the release machinery understands.
KNOWN_STATUSES = ("unreleased-candidate", PUBLISHABLE_STATUS, "released")


@dataclass(frozen=True)
class Stack:
    """One coherent three-repository stack coordinate."""

    core: str
    automation: str
    plugin: str
    contract_hash: str

    def as_triple(self) -> str:
        """Render the human-facing ``core/automation/plugin`` summary."""

        return f"{self.core}/{self.automation}/{self.plugin}"


@dataclass(frozen=True)
class ReleaseCoordinates:
    """Every version decision the release path is allowed to make."""

    status: str
    current_public: Stack
    candidate: Stack
    compatibility_version: str
    canonical_distribution: str
    compatibility_distribution: str
    legacy_compatibility_distribution: str
    legacy_compatibility_version: str
    legacy_core_version: str
    legacy_core_specifiers: tuple[str, ...]
    legacy_core_requirement_count: int

    @property
    def publishable(self) -> bool:
        return self.status == PUBLISHABLE_STATUS

    def github_outputs(self) -> dict[str, str]:
        """Flat name/value pairs for ``$GITHUB_OUTPUT``.

        The workflow reads only these.  A literal in the YAML that is not one
        of these keys is a coordinate the release path invented on its own,
        which is what the structural test forbids.
        """

        return {
            "status": self.status,
            "candidate_core": self.candidate.core,
            "candidate_automation": self.candidate.automation,
            "candidate_compatibility": self.compatibility_version,
            "candidate_plugin": self.candidate.plugin,
            "candidate_contract_hash": self.candidate.contract_hash,
            "candidate_core_tag": f"v{self.candidate.core}",
            "current_core": self.current_public.core,
            "current_automation": self.current_public.automation,
            "current_plugin": self.current_public.plugin,
            "current_contract_hash": self.current_public.contract_hash,
            "current_triple": self.current_public.as_triple(),
            "candidate_triple": self.candidate.as_triple(),
            "canonical_distribution": self.canonical_distribution,
            "compatibility_distribution": self.compatibility_distribution,
            "legacy_compatibility_distribution": (
                self.legacy_compatibility_distribution
            ),
            "legacy_compatibility_version": self.legacy_compatibility_version,
            "legacy_core_version": self.legacy_core_version,
            "legacy_core_requirement_count": str(
                self.legacy_core_requirement_count
            ),
        }


def load_identity(path: Path | None = None) -> dict[str, Any]:
    """Read ``identity.json`` from *path* or the repository default."""

    target = path or IDENTITY_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def _stack(payload: dict[str, Any], core_key: str, automation_key: str) -> Stack:
    return Stack(
        core=payload["pythonHwpx"],
        automation=payload[automation_key],
        plugin=payload["plugin"],
        contract_hash=payload["contractHash"],
    )


def coordinates(identity: dict[str, Any] | None = None) -> ReleaseCoordinates:
    """Project *identity* into the coordinates the release path may use."""

    data = identity if identity is not None else load_identity()
    release_state = data["releaseState"]
    floors = data["releaseFloors"]
    candidate = release_state["candidate"]
    current = release_state["currentPublic"]
    return ReleaseCoordinates(
        status=release_state["status"],
        current_public=_stack(current, "pythonHwpx", "primaryApplication"),
        candidate=_stack(candidate, "pythonHwpx", "canonicalAutomation"),
        compatibility_version=candidate["compatibility"],
        canonical_distribution=candidate["canonicalDistribution"],
        compatibility_distribution=candidate["compatibilityDistribution"],
        legacy_compatibility_distribution=floors[
            "legacyCompatibilityDistribution"
        ],
        legacy_compatibility_version=floors["legacyCompatibilityVersion"],
        legacy_core_version=floors["legacyCoreVersion"],
        legacy_core_specifiers=tuple(floors["legacyCoreSpecifiers"]),
        legacy_core_requirement_count=floors["legacyCoreRequirementCount"],
    )


def check_promotion_not_premature(coords: ReleaseCoordinates) -> list[str]:
    """Structural replacement for the old frozen ``currentPublic`` literal.

    While a tag is publishing, the state is ``release-approved`` and the public
    stack must still name the previously observed stack.  If any candidate
    coordinate has already been copied into ``currentPublic`` the promotion ran
    before the three-stack remote truth was observed, which is the failure the
    original frozen dictionary was written to catch.
    """

    errors: list[str] = []
    if coords.status not in KNOWN_STATUSES:
        errors.append(f"unknown release status: {coords.status!r}")
        return errors
    if coords.status == "released":
        if coords.current_public != coords.candidate:
            errors.append(
                "released identity must point currentPublic at the promoted "
                f"candidate; currentPublic={coords.current_public.as_triple()} "
                f"candidate={coords.candidate.as_triple()}"
            )
        return errors

    if coords.current_public == coords.candidate:
        errors.append(
            "currentPublic was promoted to the candidate before the full "
            "three-stack remote truth was observed; while the state is "
            f"{coords.status} it must still name the previous public stack "
            f"(both are {coords.candidate.as_triple()})"
        )
        return errors

    overlaps = [
        name
        for name, public, cand in (
            ("pythonHwpx", coords.current_public.core, coords.candidate.core),
            (
                "primaryApplication",
                coords.current_public.automation,
                coords.candidate.automation,
            ),
            ("plugin", coords.current_public.plugin, coords.candidate.plugin),
            (
                "contractHash",
                coords.current_public.contract_hash,
                coords.candidate.contract_hash,
            ),
        )
        if public == cand
    ]
    # A partial overlap is legitimate: a train that ships core and plugin
    # without moving automation leaves primaryApplication equal, and a train
    # that changes no tool contract leaves contractHash equal. Only a total
    # overlap, handled above, is a premature promotion.
    if len(overlaps) == 4:  # pragma: no cover - unreachable, kept explicit
        errors.append(f"currentPublic fully mirrors candidate: {overlaps}")
    return errors


def remote_witness_targets(coords: ReleaseCoordinates) -> list[dict[str, str]]:
    """Describe what an outside service must confirm about ``currentPublic``.

    These are the observations that replace the hand-copied witness.  They are
    intentionally about ``currentPublic``, not the candidate: the candidate is
    what this run is about to publish, while ``currentPublic`` is a standing
    claim that something is already installable.  A stale or invented
    ``currentPublic`` fails here without anyone having to remember to edit a
    dictionary.
    """

    return [
        {
            "kind": "pypi",
            "project": "python-hwpx",
            "version": coords.current_public.core,
            "url": (
                "https://pypi.org/pypi/python-hwpx/"
                f"{coords.current_public.core}/json"
            ),
        },
        {
            "kind": "pypi",
            "project": coords.canonical_distribution,
            "version": coords.current_public.automation,
            "url": (
                f"https://pypi.org/pypi/{coords.canonical_distribution}/"
                f"{coords.current_public.automation}/json"
            ),
        },
        {
            "kind": "github-release",
            "project": "airmang/hwpx-plugins",
            "version": coords.current_public.plugin,
            "url": f"v{coords.current_public.plugin}",
        },
    ]


def handoff_summary(coords: ReleaseCoordinates) -> str:
    """The prose the workflow prints, derived instead of hand written."""

    return (
        "release-approved and currentPublic remains "
        f"{coords.current_public.as_triple()}."
    )


def _emit_github_output(coords: ReleaseCoordinates) -> None:
    destination = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{key}={value}" for key, value in coords.github_outputs().items()]
    payload = "\n".join(lines) + "\n"
    if destination:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(payload)
    else:
        sys.stdout.write(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="append the derived coordinates to $GITHUB_OUTPUT (or stdout)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the derived coordinates as JSON",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="run the structural promotion invariants and fail closed",
    )
    parser.add_argument(
        "--handoff-summary",
        action="store_true",
        help="print the derived plugin-handoff prose line",
    )
    parser.add_argument(
        "--candidate-triple",
        action="store_true",
        help="print the candidate core/automation/plugin triple",
    )
    parser.add_argument(
        "--identity",
        type=Path,
        default=None,
        help="read an alternate identity.json (used by tests)",
    )
    args = parser.parse_args(argv)

    coords = coordinates(load_identity(args.identity))

    if args.handoff_summary:
        print(handoff_summary(coords))
        return 0
    if args.candidate_triple:
        print(coords.candidate.as_triple())
        return 0

    if args.verify:
        errors = check_promotion_not_premature(coords)
        for error in errors:
            print(f"release coordinate error: {error}", file=sys.stderr)
        if errors:
            return 1
        print(
            f"release coordinates ok: status={coords.status} "
            f"currentPublic={coords.current_public.as_triple()} "
            f"candidate={coords.candidate.as_triple()}"
        )

    if args.json:
        print(json.dumps(coords.github_outputs(), indent=2, sort_keys=True))
    if args.github_output:
        _emit_github_output(coords)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
