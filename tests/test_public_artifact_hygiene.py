from __future__ import annotations

import importlib.util
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_public_hygiene.py"
SPEC = importlib.util.spec_from_file_location("automation_public_hygiene", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
hygiene = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hygiene)


@pytest.fixture(scope="module")
def public_artifacts(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, ...]:
    output = tmp_path_factory.mktemp("automation-public-artifacts")
    canonical = output / "canonical"
    compat = output / "compat"
    canonical.mkdir()
    compat.mkdir()
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(canonical)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--outdir",
            str(compat),
            str(ROOT / "compat" / "hwpx-mcp-server"),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(sorted((*canonical.iterdir(), *compat.iterdir())))


def test_wheel_and_sdist_text_is_public_clean(
    public_artifacts: tuple[Path, ...],
) -> None:
    failures: list[str] = []
    for artifact in public_artifacts:
        if artifact.suffix == ".whl":
            with zipfile.ZipFile(artifact) as archive:
                for name in archive.namelist():
                    failures.extend(
                        hygiene._artifact_text_failure(
                            artifact,
                            name,
                            archive.read(name),
                        )
                    )
        else:
            with tarfile.open(artifact, "r:gz") as archive:
                assert not any(
                    "/tests/" in member.name for member in archive.getmembers()
                )
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    extracted = archive.extractfile(member)
                    assert extracted is not None
                    failures.extend(
                        hygiene._artifact_text_failure(
                            artifact,
                            member.name,
                            extracted.read(),
                        )
                    )
    assert failures == []


def test_artifact_hygiene_distinguishes_versions_from_internal_labels() -> None:
    artifact = ROOT / "dist" / "fixture.whl"
    assert not hygiene._artifact_text_failure(
        artifact,
        "PKG-INFO",
        b"public versions 5.0.0, 6.0.0, and 1.0.0",
    )
    assert hygiene._artifact_text_failure(
        artifact,
        "module.py",
        b"internal label " + b"S-" + b"108",
    )
    assert hygiene._artifact_text_failure(
        artifact,
        "module.py",
        b"internal id " + b"STG-" + b"deadbeef",
    )
    assert hygiene._artifact_text_failure(
        artifact,
        "module.py",
        b"/" + b"Users" + b"/example/private.py",
    )


def test_current_shipped_source_has_no_internal_stage_labels() -> None:
    tracked = [
        "pyproject.toml",
        *(
            str(path.relative_to(ROOT))
            for path in (ROOT / "src" / "hwpx_automation").rglob("*")
            if path.is_file()
        ),
    ]
    assert hygiene._shipped_source_stage_failures(tracked) == []
