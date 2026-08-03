#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Confirm that ``currentPublic`` names a stack the outside world really has.

What this replaces
==================

The tag gate used to compare ``releaseState.currentPublic`` against a
dictionary of version literals embedded in ``.github/workflows/release.yml``.
That dictionary had to be advanced by hand every train.  Its record, from the
preserved failure tags in ``CHANGELOG.md``:

* ``v6.1.2`` -- the gate still held the 6.0-era stack, so an identity that
  correctly recorded the then-current public stack was rejected.
* ``v6.4.1`` -- the same thing one train later, holding the 6.2-era stack.
* ``v6.7.0`` -- three of the four literals advanced and ``pythonHwpx`` did not.

Three failures, no real defect caught.  The genuine catch attributed to this
area, ``v6.4.0``, came from the separate ``status != release-approved`` check,
which is untouched.

Why an external observation is the right replacement
====================================================

``currentPublic`` is not a fact about this repository; it is a claim that a
particular stack is installable right now.  Deriving it from git history does
not work either -- it advanced in three commits that are not promotions, so a
commit-message derivation fails on the core-only and core-plus-plugin trains
(see ``scripts/release_coordinates.py`` for the refutation).

So the claim is checked against services this project does not control.  A
``currentPublic`` that names something unpublished -- including one promoted to
the candidate before the candidate was actually released -- fails here, and
nobody has to remember to edit anything.

This satisfies the standing gate rule in
``docs/2026-08-03-twelve-month-direction.md``: every gate must include an input
we did not author or an observer we do not control.

Offline behaviour is deliberate.  Without network the script reports
``unverified`` and exits non-zero only when ``--require-network`` is passed,
which the release workflow does.  Local runs degrade instead of pretending.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_coordinates import coordinates, load_identity

PLUGIN_REPOSITORY = "airmang/hwpx-plugins"
TIMEOUT_SECONDS = 20


class Unverified(Exception):
    """The observation could not be made, which is not the same as a failure."""


def _observe_pypi(project: str, version: str) -> None:
    url = f"https://pypi.org/pypi/{project}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise AssertionError(
                f"currentPublic claims {project} {version} is public, but PyPI "
                "has no such release"
            ) from error
        raise Unverified(f"PyPI returned HTTP {error.code} for {project}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise Unverified(f"PyPI unreachable for {project}: {error}") from error

    observed = payload.get("info", {}).get("version")
    if observed != version:
        raise AssertionError(
            f"PyPI reports {project} {observed!r} for the {version} lookup"
        )
    if not payload.get("urls"):
        raise AssertionError(
            f"{project} {version} exists on PyPI with no downloadable artifact"
        )


def _observe_github_release(repository: str, tag: str) -> None:
    completed = subprocess.run(
        [
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            repository,
            "--json",
            "tagName,isDraft,isPrerelease",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT_SECONDS * 3,
    )
    if completed.returncode:
        combined = f"{completed.stdout}\n{completed.stderr}".lower()
        if "release not found" in combined or "not found" in combined:
            raise AssertionError(
                f"currentPublic claims plugin {tag} is public, but "
                f"{repository} has no such release"
            )
        raise Unverified(
            f"gh could not observe {repository} {tag}: {completed.stderr.strip()}"
        )
    payload = json.loads(completed.stdout)
    if payload.get("isDraft") or payload.get("isPrerelease"):
        raise AssertionError(f"{repository} {tag} is a draft or prerelease")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--require-network",
        action="store_true",
        help="treat an unreachable observer as a failure (release workflow)",
    )
    parser.add_argument("--identity", type=Path, default=None)
    args = parser.parse_args(argv)

    coords = coordinates(load_identity(args.identity))
    public = coords.current_public

    failures: list[str] = []
    unverified: list[str] = []

    checks = (
        ("python-hwpx", public.core, lambda: _observe_pypi("python-hwpx", public.core)),
        (
            coords.canonical_distribution,
            public.automation,
            lambda: _observe_pypi(coords.canonical_distribution, public.automation),
        ),
        (
            PLUGIN_REPOSITORY,
            public.plugin,
            lambda: _observe_github_release(PLUGIN_REPOSITORY, f"v{public.plugin}"),
        ),
    )

    for name, version, observe in checks:
        try:
            observe()
        except AssertionError as error:
            failures.append(str(error))
        except Unverified as error:
            unverified.append(f"{name} {version}: {error}")
        else:
            print(f"observed: {name} {version} is public")

    for message in failures:
        print(f"currentPublic error: {message}", file=sys.stderr)
    for message in unverified:
        print(f"currentPublic unverified: {message}", file=sys.stderr)

    if failures:
        return 1
    if unverified and args.require_network:
        print(
            "currentPublic could not be observed and --require-network was "
            "requested; refusing to report a pass",
            file=sys.stderr,
        )
        return 1
    if unverified:
        print(
            f"currentPublic {public.as_triple()}: "
            f"{len(checks) - len(unverified)}/{len(checks)} observed, "
            "remainder unverified (offline)"
        )
        return 0
    print(f"currentPublic {public.as_triple()} fully observed")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
