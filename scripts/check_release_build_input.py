#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reject generated residue in an explicitly supplied release source tree.

Release builds call this against a fresh ``git archive`` extraction.  The root
is deliberately required instead of defaulting to the repository checkout:
ignored development residue is common and must not make ordinary test runs
fail, while any such residue in the actual release input is a hard error.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, order=True)
class Pollution:
    """One generated path that makes a release source tree unsafe."""

    kind: str
    path: str


_DIRECTORY_KINDS = {
    "build": "BUILD_DIR",
    "dist": "DIST_DIR",
    "__pycache__": "PYCACHE_DIR",
}


def find_pollution(root: Path) -> list[Pollution]:
    """Return all release-input pollution below *root*.

    Filesystem read errors are intentionally allowed to propagate.  A caller
    must never interpret an incomplete scan as a clean release input.
    """

    if root.is_symlink():
        return [Pollution(kind="SYMLINK", path=".")]

    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise NotADirectoryError(f"release build input is not a directory: {root}")

    findings: list[Pollution] = []

    def raise_walk_error(error: OSError) -> None:
        raise error

    for current, dirnames, filenames in os.walk(
        resolved,
        topdown=True,
        followlinks=False,
        onerror=raise_walk_error,
    ):
        current_path = Path(current)
        dirnames.sort()
        filenames.sort()

        for name in dirnames:
            candidate = current_path / name
            if candidate.is_symlink():
                relative = candidate.relative_to(resolved).as_posix()
                findings.append(Pollution(kind="SYMLINK", path=relative))
                continue
            kind = _DIRECTORY_KINDS.get(name)
            if kind is None and name.endswith(".egg-info"):
                kind = "EGG_INFO"
            if kind is not None:
                relative = candidate.relative_to(resolved).as_posix()
                findings.append(Pollution(kind=kind, path=relative))

        for name in filenames:
            candidate = current_path / name
            if candidate.is_symlink():
                relative = candidate.relative_to(resolved).as_posix()
                findings.append(Pollution(kind="SYMLINK", path=relative))
                continue
            if name.endswith(".egg-info"):
                relative = candidate.relative_to(resolved).as_posix()
                findings.append(Pollution(kind="EGG_INFO", path=relative))

    return sorted(findings)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail if a supplied release source tree contains build residue.",
    )
    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help="release source root to scan (normally a fresh git-archive extraction)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        findings = find_pollution(args.root)
    except (OSError, RuntimeError) as error:
        print(
            f"release build input scan failed closed: {error}",
            file=sys.stderr,
        )
        return 2

    if findings:
        print(
            "release build input is polluted; refusing to build:",
            file=sys.stderr,
        )
        for finding in findings:
            print(f"  [{finding.kind}] {finding.path}", file=sys.stderr)
        return 1

    print(f"release build input is clean: {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
