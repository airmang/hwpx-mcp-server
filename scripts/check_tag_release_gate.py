#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run the tag-time release gate, with or without an actual tag.

This logic used to live as shell and heredocs inside
``.github/workflows/release.yml``. Logic embedded in a workflow can only ever
be exercised by pushing a tag, which is why every mistake in it became a
preserved failure tag instead of a red test. Here it is ordinary Python that a
dry run can execute, so the gate can fail on a laptop instead of on a tag.

The gate answers four questions:

1. Is the identity state one a tag may publish?
2. Do the tag, both ``pyproject.toml`` files, and ``CHANGELOG.md`` agree on the
   version?
3. Is the changelog heading dated, rather than still an open section?
4. Do the structural promotion invariants hold?

In dry-run mode the tag is taken from the candidate coordinates instead of the
environment, so the run answers "would the tag I am about to push pass".
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_coordinates import (
    PUBLISHABLE_STATUS,
    check_promotion_not_premature,
    coordinates,
    load_identity,
)

ROOT = Path(__file__).resolve().parents[1]


def _project_version(relative: str) -> str:
    data = tomllib.loads((ROOT / relative).read_text(encoding="utf-8"))
    return data["project"]["version"]


def _changelog_version() -> str:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # Skip the conventional [Unreleased] heading so keep-a-changelog workflow
    # (pre-collecting notes above the released version) survives alongside
    # tag-based release automation.
    matches = re.findall(r"^## \[([^\]]+)\]", changelog, re.MULTILINE)
    version = next(
        (item.strip() for item in matches if item.strip().lower() != "unreleased"),
        None,
    )
    if version is None:
        raise SystemExit("CHANGELOG.md has no '## [x.y.z]' version section")
    return version


def _changelog_heading_is_dated(version: str) -> bool:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    pattern = rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$"
    return re.search(pattern, changelog, re.MULTILINE) is not None


def _readme_declares_release_approved() -> bool:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    return "<!-- release-state: release-approved -->" in readme


def run_gate(tag_name: str, *, dry_run: bool) -> list[str]:
    """Return the list of gate failures; empty means the tag may publish."""

    errors: list[str] = []
    coords = coordinates(load_identity())
    tag_version = tag_name.removeprefix("v")

    errors.extend(check_promotion_not_premature(coords))

    if coords.status != PUBLISHABLE_STATUS:
        message = (
            f"a tag publish requires the {PUBLISHABLE_STATUS} state reached "
            "after separate owner approval; 'released' describes the stack "
            "only after plugin, marketplace, and a real install have been "
            f"observed, so it must not precede a tag (found {coords.status!r})"
        )
        if dry_run:
            # A dry run is normally invoked while the tree is still 'released'
            # from the previous train. Report it without failing, so the dry
            # run stays usable at any point in the cycle.
            print(f"dry-run note: {message}")
        else:
            errors.append(message)

    pyproject_version = _project_version("pyproject.toml")
    compat_version = _project_version("compat/hwpx-mcp-server/pyproject.toml")
    changelog_version = _changelog_version()

    print(f"tag version:              {tag_version}")
    print(f"pyproject version:        {pyproject_version}")
    print(f"compat pyproject version: {compat_version}")
    print(f"changelog version:        {changelog_version}")
    print(f"candidate automation:     {coords.candidate.automation}")

    if tag_version != pyproject_version:
        errors.append(
            f"tag {tag_version} does not match pyproject {pyproject_version}"
        )
    if tag_version != compat_version:
        errors.append(
            f"tag {tag_version} does not match compat pyproject {compat_version}"
        )
    if tag_version != changelog_version:
        errors.append(
            f"tag {tag_version} does not match changelog {changelog_version}"
        )
    if tag_version not in {
        coords.candidate.automation,
        coords.compatibility_version,
    }:
        errors.append(
            f"tag {tag_version} does not match the automation train candidate "
            f"{coords.candidate.automation}"
        )
    if not _changelog_heading_is_dated(changelog_version):
        errors.append(
            f"changelog heading for {changelog_version} must be dated as "
            "'## [x.y.z] - YYYY-MM-DD' before a tag release"
        )
    if coords.status == PUBLISHABLE_STATUS and not _readme_declares_release_approved():
        errors.append("README is missing the release-state: release-approved marker")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "take the tag from the candidate coordinates instead of "
            "GITHUB_REF_NAME and tolerate a not-yet-approved state"
        ),
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="override the tag name (defaults to GITHUB_REF_NAME or candidate)",
    )
    args = parser.parse_args(argv)

    coords = coordinates(load_identity())
    if args.tag:
        tag_name = args.tag
    elif args.dry_run:
        tag_name = f"v{coords.candidate.automation}"
    else:
        tag_name = os.environ["GITHUB_REF_NAME"]

    mode = "dry run" if args.dry_run else "tag release"
    print(f"release gate ({mode}) for {tag_name}")
    errors = run_gate(tag_name, dry_run=args.dry_run)

    for error in errors:
        print(f"release gate error: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"release gate passed for {tag_name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
