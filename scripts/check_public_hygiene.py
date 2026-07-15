#!/usr/bin/env python3
"""Fail when public repository hygiene regresses."""

from __future__ import annotations

import os
import re
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

_MCP_INTERNAL_RUNTIME_MARKERS = (
    b"hwpx_mcp_server.practice",
    b"hwpx.practice",
    b"private_practice",
    b"HWPX_PRACTICE_ROOT",
    b"HWPX_PRACTICE_RUNNER_MANIFEST",
    b"HWPX_PRACTICE_ENCRYPTION_KEY",
    b"HWPX_CORPUS_SOURCE",
    b"CAMPAIGN_UNAVAILABLE",
    b"CAMPAIGN_QUEUE_UNAVAILABLE",
    b"start_practice_scenario",
    b"apply_practice_scenario",
    b"start_practice_campaign",
    b"get_practice_campaign",
    b"continue_practice_campaign",
    b"cancel_practice_campaign",
    b"export_practice_campaign",
)


def _git_paths(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args, "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [item for item in result.stdout.decode("utf-8").split("\0") if item]


def _project_kind() -> str:
    if (ROOT / "packaging" / "hosts.json").is_file():
        return "plugin"
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return "mcp" if 'name = "hwpx-mcp-server"' in metadata else "core"


def _forbidden_path(path: str, kind: str) -> bool:
    common_prefixes = (".harness/", ".omx/",)
    if path.startswith(common_prefixes):
        return True
    if kind == "core":
        return path.startswith(("shared/hwpx/", "docs/superpowers/", "tests/evidence/", "examples/out/"))
    if kind == "mcp":
        return (
            path.startswith("docs/superpowers/")
            or path.startswith("src/hwpx_mcp_server/practice/")
            or path.startswith("tests/test_practice_")
            or bool(re.fullmatch(r"tests/(?:.*report.*|.*evidence.*)\.md", path))
        )
    generated_s070 = {
        "adjudication.json",
        "final-manifest.json",
        "private-routing.json",
        "result-manifest.json",
    }
    if path.startswith(("docs/", "tests/evidence/", "examples/out/")):
        return True
    if "/examples/s070_fixture_benchmark/" in path and path.startswith("plugins/"):
        return True
    prefix = "examples/s070_fixture_benchmark/"
    if path.startswith(prefix):
        tail = path.removeprefix(prefix)
        return tail.startswith(("blind/", "public/")) or tail in generated_s070
    return False


def _text_bytes(path: Path) -> bytes | None:
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return None
    return data


def _wheel_failures() -> list[str]:
    failures: list[str] = []
    rejected = (
        "tests/",
        "shared/hwpx/",
        "docs/superpowers/",
        "examples/out/",
        ".harness/",
        ".omx/",
        "hwpx_mcp_server/practice/",
    )
    for wheel in sorted((ROOT / "dist").glob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            for name in names:
                if name.startswith(rejected) or any(f"/{part}" in f"/{name}" for part in rejected):
                    failures.append(f"{wheel.relative_to(ROOT)} contains {name}")
                if name.endswith(".py"):
                    data = archive.read(name)
                    for marker in _MCP_INTERNAL_RUNTIME_MARKERS:
                        if marker in data:
                            failures.append(
                                f"{wheel.relative_to(ROOT)} contains internal runtime marker "
                                f"{marker.decode('ascii')!r} in {name}"
                            )
            for name in names:
                if not name.endswith(".dist-info/METADATA"):
                    continue
                requirements = [
                    line.casefold()
                    for line in archive.read(name).decode("utf-8", "replace").splitlines()
                    if line.startswith("Requires-Dist:")
                ]
                if any(line.startswith("requires-dist: modelcontextprotocol") for line in requirements):
                    failures.append(f"{wheel.relative_to(ROOT)} declares modelcontextprotocol")
    return failures


def _mcp_runtime_failures(tracked: list[str]) -> list[str]:
    failures: list[str] = []
    for rel in tracked:
        if not rel.startswith("src/") or not rel.endswith(".py"):
            continue
        data = (ROOT / rel).read_bytes()
        for marker in _MCP_INTERNAL_RUNTIME_MARKERS:
            if marker in data:
                failures.append(
                    f"internal runtime marker {marker.decode('ascii')!r}: {rel}"
                )
    return failures


def _action_pin_failures(tracked: list[str]) -> list[str]:
    failures: list[str] = []
    action_ref = re.compile(r"^\s*-?\s*uses:\s*([^@\s]+)@([^\s#]+)", re.MULTILINE)
    for rel in tracked:
        if not rel.startswith(".github/workflows/") or not rel.endswith((".yml", ".yaml")):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        for action, ref in action_ref.findall(text):
            if action.startswith(("./", "docker://")):
                continue
            if not re.fullmatch(r"[0-9a-f]{40}", ref):
                failures.append(f"mutable GitHub Action ref: {rel}: {action}@{ref}")
    return failures


def _hwpx_member_failures(
    tracked: list[str],
    workstation_path: re.Pattern[bytes],
    private_markers: list[bytes],
) -> list[str]:
    failures: list[str] = []
    for rel in tracked:
        if not rel.casefold().endswith(".hwpx"):
            continue
        try:
            with zipfile.ZipFile(ROOT / rel) as archive:
                for member in archive.namelist():
                    data = archive.read(member)
                    if workstation_path.search(data):
                        failures.append(f"workstation-shaped path: {rel}!{member}")
                    if any(marker in data for marker in private_markers):
                        failures.append(f"private-origin marker: {rel}!{member}")
        except zipfile.BadZipFile:
            # Some corruption fixtures are intentionally invalid packages.
            continue
    return failures


def main() -> int:
    kind = _project_kind()
    tracked = [
        path
        for path in _git_paths("ls-files", "--cached", "--others", "--exclude-standard")
        if (ROOT / path).is_file()
    ]
    failures = [
        f"forbidden tracked path: {path}"
        for path in tracked
        if _forbidden_path(path, kind)
    ]

    tracked_ignored = _git_paths("ls-files", "-ci", "--exclude-standard")
    failures.extend(f"tracked file is ignored: {path}" for path in tracked_ignored)

    workstation_path = re.compile(
        ("/" + "Users" + r"/[^/\s]+/").encode()
        + b"|"
        + ("/" + "home" + r"/[^/\s]+/").encode()
        + b"|[A-Za-z]:\\\\[Uu]sers\\\\"
    )
    private_markers = [b">" + b"ko" + b"kyu" + b"<"]
    private_markers.extend(
        value.strip().encode("utf-8")
        for value in os.environ.get("HWPX_PRIVATE_PII_NEEDLES", "").split(",")
        if value.strip()
    )

    for rel in tracked:
        data = _text_bytes(ROOT / rel)
        if data is None:
            continue
        if workstation_path.search(data):
            failures.append(f"workstation-shaped path: {rel}")
        if any(marker in data for marker in private_markers):
            failures.append(f"private-origin marker: {rel}")

    failures.extend(_hwpx_member_failures(tracked, workstation_path, private_markers))
    failures.extend(_action_pin_failures(tracked))
    if kind == "mcp":
        failures.extend(_mcp_runtime_failures(tracked))
    failures.extend(_wheel_failures())
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 1
    print(f"[OK] public hygiene: {kind}; {len(tracked)} tracked files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
