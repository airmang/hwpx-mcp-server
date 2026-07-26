#!/usr/bin/env python3
"""Fail when public repository hygiene regresses."""

from __future__ import annotations

import os
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTERNAL_STAGE_CODE = re.compile(
    rb"(?<![A-Za-z0-9])(?:S-[0-9]{3}(?![0-9])|STG-[A-Za-z0-9]+)"
)
WORKSTATION_PATH = re.compile(
    ("/" + "Users" + r"/[^/\s]+/").encode()
    + b"|"
    + ("/" + "home" + r"/[^/\s]+/").encode()
    + b"|[A-Za-z]:\\\\[Uu]sers\\\\"
)
TEXT_ARTIFACT_SUFFIXES = (
    ".cfg",
    ".json",
    ".md",
    ".py",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
)

_AUTOMATION_INTERNAL_RUNTIME_MARKERS = (
    b"hwpx_automation.practice",
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
    # 이름 하나로 판별하면 배포명이 바뀌는 순간 이 저장소가 core로 분류되고
    # automation 전용 금지 규칙이 통째로 건너뛰어진다. 실제로 6.0.0 재명명 뒤 게이트가
    # "[OK] public hygiene: core"를 내며 아무것도 검사하지 않았다.
    #
    # 이름 목록이 아니라 **구조**로 판별한다. 이 저장소만 응용 소유자 트리를
    # 갖는다.
    if (ROOT / "src" / "hwpx_automation").is_dir():
        return "automation"
    # The legacy distribution name still identifies this same application
    # repository while the 6.x compatibility line remains supported.
    if 'name = "hwpx-mcp-server"' in metadata:
        return "automation"
    return "core"


def _forbidden_path(path: str, kind: str) -> bool:
    common_prefixes = (".harness/", ".omx/",)
    if path.startswith(common_prefixes):
        return True
    if kind == "core":
        return path.startswith(("shared/hwpx/", "docs/superpowers/", "tests/evidence/", "examples/out/"))
    if kind == "automation":
        return (
            path.startswith("docs/superpowers/")
            or path.startswith("src/hwpx_automation/practice/")
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


def _artifact_text_failure(
    artifact: Path,
    member: str,
    data: bytes,
) -> list[str]:
    basename = Path(member).name
    if not (
        member.casefold().endswith(TEXT_ARTIFACT_SUFFIXES)
        or basename in {"METADATA", "PKG-INFO"}
    ):
        return []
    try:
        artifact_name: Path | str = artifact.relative_to(ROOT)
    except ValueError:
        artifact_name = artifact
    display = f"{artifact_name}!{member}"
    failures: list[str] = []
    if INTERNAL_STAGE_CODE.search(data):
        failures.append(f"internal Stage code in public artifact: {display}")
    if WORKSTATION_PATH.search(data):
        failures.append(f"workstation-shaped path in public artifact: {display}")
    return failures


def _wheel_failures() -> list[str]:
    failures: list[str] = []
    rejected = (
        "tests/",
        "shared/hwpx/",
        "docs/superpowers/",
        "examples/out/",
        ".harness/",
        ".omx/",
        "hwpx_automation/practice/",
    )
    for wheel in sorted((ROOT / "dist").rglob("*.whl")):
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            for name in names:
                if name.startswith(rejected) or any(f"/{part}" in f"/{name}" for part in rejected):
                    failures.append(f"{wheel.relative_to(ROOT)} contains {name}")
                if name.endswith(".py"):
                    data = archive.read(name)
                    for marker in _AUTOMATION_INTERNAL_RUNTIME_MARKERS:
                        if marker in data:
                            failures.append(
                                f"{wheel.relative_to(ROOT)} contains internal runtime marker "
                                f"{marker.decode('ascii')!r} in {name}"
                            )
                failures.extend(
                    _artifact_text_failure(wheel, name, archive.read(name))
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


def _sdist_failures() -> list[str]:
    failures: list[str] = []
    for sdist in sorted((ROOT / "dist").rglob("*.tar.gz")):
        with tarfile.open(sdist, "r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                failures.extend(
                    _artifact_text_failure(sdist, member.name, extracted.read())
                )
    return failures


def _shipped_source_stage_failures(tracked: list[str]) -> list[str]:
    failures: list[str] = []
    for rel in tracked:
        if rel != "pyproject.toml" and not rel.startswith("src/hwpx_automation/"):
            continue
        data = _text_bytes(ROOT / rel)
        if data is not None and INTERNAL_STAGE_CODE.search(data):
            failures.append(f"internal Stage code in shipped source: {rel}")
    return failures


def _automation_runtime_failures(tracked: list[str]) -> list[str]:
    failures: list[str] = []
    for rel in tracked:
        if not rel.startswith("src/") or not rel.endswith(".py"):
            continue
        data = (ROOT / rel).read_bytes()
        for marker in _AUTOMATION_INTERNAL_RUNTIME_MARKERS:
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
        if WORKSTATION_PATH.search(data):
            failures.append(f"workstation-shaped path: {rel}")
        if any(marker in data for marker in private_markers):
            failures.append(f"private-origin marker: {rel}")

    failures.extend(_hwpx_member_failures(tracked, WORKSTATION_PATH, private_markers))
    failures.extend(_action_pin_failures(tracked))
    failures.extend(_shipped_source_stage_failures(tracked))
    if kind == "automation":
        failures.extend(_automation_runtime_failures(tracked))
    failures.extend(_wheel_failures())
    failures.extend(_sdist_failures())
    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        return 1
    print(f"[OK] public hygiene: {kind}; {len(tracked)} tracked files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
