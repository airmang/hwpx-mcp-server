from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from hwpx import validate_editor_open_safety
from hwpx.builder import Document, Paragraph, Section
from hwpx.practice import PRACTICE_RUN_SCHEMA, build_campaign_manifest, practice_run_id
from hwpx_mcp_server import server
from hwpx_mcp_server.practice import runtime as runtime_module
from hwpx_mcp_server.practice.runtime import (
    PracticeRuntimeError,
    _TerminalArtifactStore,
    _reset_practice_campaign_runtime_for_tests,
    _startup_reap,
    _task_dispatch_sha256,
    build_practice_campaign_service,
    installed_runtime_provenance,
)


def _digest(value: str | bytes) -> str:
    payload = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _budgets() -> dict[str, int]:
    return {
        "toolCalls": 4,
        "attempts": 2,
        "repairRounds": 2,
        "elapsedSeconds": 120,
        "costMicrounits": 1_000,
        "artifactBytes": 1_000_000,
    }


def _provenance(skill_root: Path, skill_version: str) -> dict[str, Any]:
    return installed_runtime_provenance(skill_root, skill_version)


def _campaign(source_bytes: bytes, provenance: dict[str, Any]):
    source_hash = _digest(source_bytes)
    scenario_ref = {
        "scenarioId": "SCN-00000000000000000001",
        "scenarioSha256": _digest("scenario"),
        "runnerManifestSha256": _digest("runner-manifest"),
        "derivativeSha256": _digest("derivative"),
        "startArtifactId": "ART-00000000000000000001",
        "startArtifactSha256": source_hash,
    }
    task_material = {
        "scenarioRef": scenario_ref,
        "workflowFamily": "unknown_form_fill",
        "artifactScope": "practice",
        "sourceArtifactSha256": source_hash,
        "parameters": {"operationKind": "table", "operations": []},
        "privacy": {
            "syntheticInputsOnly": True,
            "highConfidencePiiCount": 0,
        },
    }
    dispatch = {
        "slot": 0,
        "dispatchKey": "DSP-00000000000000000001",
        "seedSha256": _task_dispatch_sha256(task_material),
    }
    run_id = practice_run_id(
        {
            "schema": PRACTICE_RUN_SCHEMA,
            "scenarioRef": scenario_ref,
            "dispatch": dispatch,
            "provenance": provenance,
            "budgets": _budgets(),
        }
    )
    run_ref = {
        "slot": 0,
        "runId": run_id,
        "scenarioId": scenario_ref["scenarioId"],
        "scenarioSha256": scenario_ref["scenarioSha256"],
        "runnerManifestSha256": scenario_ref["runnerManifestSha256"],
        "derivativeSha256": scenario_ref["derivativeSha256"],
        "startArtifactSha256": source_hash,
        "family": "unknown_form_fill",
        "difficulty": "routine",
        "budgets": _budgets(),
    }
    manifest = build_campaign_manifest(
        scenario_manifest_sha256=_digest("scenario-manifest"),
        selection={
            "seedSha256": _digest("selection"),
            "strategyVersion": "coverage-weakness/v1",
            "policySha256": _digest("policy"),
        },
        provenance=provenance,
        budgets={
            "runs": 1,
            "toolCalls": 4,
            "elapsedSeconds": 120,
            "costMicrounits": 1_000,
            "artifactBytes": 1_000_000,
        },
        runs=[run_ref],
    )
    task = {
        "schema": "hwpx.practice-campaign-task/v1",
        "campaignId": manifest["campaignId"],
        "runId": run_id,
        "scenarioRef": task_material["scenarioRef"],
        "dispatch": dispatch,
        "workflowFamily": task_material["workflowFamily"],
        "artifactScope": task_material["artifactScope"],
        "sourceArtifactSha256": task_material["sourceArtifactSha256"],
        "parameters": task_material["parameters"],
        "privacy": task_material["privacy"],
    }
    return manifest, run_ref, task


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _minimal_hwpx(path: Path, text: str = "Synthetic practice artifact") -> bytes:
    report = Document(
        sections=[Section(children=[Paragraph(text=text)])]
    ).save_to_path(path)
    assert report.editor_open_safety.ok is True
    return path.read_bytes()


def _mark_zip_encrypted(payload: bytes) -> bytes:
    mutated = bytearray(payload)
    signatures = ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8))
    for signature, flag_offset in signatures:
        cursor = 0
        while True:
            cursor = mutated.find(signature, cursor)
            if cursor < 0:
                break
            offset = cursor + flag_offset
            flags = int.from_bytes(mutated[offset : offset + 2], "little") | 0x1
            mutated[offset : offset + 2] = flags.to_bytes(2, "little")
            cursor += len(signature)
    return bytes(mutated)


def _replace_zip_member(payload: bytes, name: str, replacement: bytes) -> bytes:
    output = io.BytesIO()
    replaced = False
    with zipfile.ZipFile(io.BytesIO(payload), mode="r") as source:
        with zipfile.ZipFile(output, mode="w") as target:
            for info in source.infolist():
                member = source.read(info)
                if info.filename == name:
                    member = replacement
                    replaced = True
                target.writestr(info, member)
    assert replaced is True
    result = output.getvalue()
    assert validate_editor_open_safety(result).ok is True
    return result


def _layout(
    tmp_path: Path,
    monkeypatch,
    *,
    artifact_bytes: bytes | None = None,
    artifact_text: str = "Synthetic practice artifact",
):
    source_root = tmp_path / "source"
    practice_root = tmp_path / "practice"
    skill_root = tmp_path / "skill"
    runtime_root = practice_root / "runtime"
    source_root.mkdir()
    practice_root.mkdir()
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text("# Synthetic Leap B skill\n", encoding="utf-8")
    (skill_root / "references").mkdir()
    (skill_root / "references" / "runner.md").write_text(
        "synthetic runner contract\n", encoding="utf-8"
    )
    for name in ("campaigns", "queue", "results", "sandboxes", "workflow"):
        (runtime_root / name).mkdir(parents=True, exist_ok=True)
    skill_version = "0.1.28"
    monkeypatch.setenv("HWPX_CORPUS_SOURCE", str(source_root))
    monkeypatch.setenv("HWPX_PRACTICE_ROOT", str(practice_root))
    monkeypatch.setenv("HWPX_SKILL_VERSION", skill_version)
    monkeypatch.setenv("HWPX_SKILL_ROOT", str(skill_root))
    provenance = _provenance(skill_root, skill_version)
    _write_json(runtime_root / "provenance.json", provenance)
    if artifact_bytes is None:
        artifact_bytes = _minimal_hwpx(tmp_path / "synthetic-practice.hwpx", artifact_text)
    source_bytes = artifact_bytes
    manifest, run_ref, task = _campaign(source_bytes, provenance)
    digest = run_ref["startArtifactSha256"]
    source = source_root / "immutable-corpus-sentinel.bin"
    source.write_bytes(b"immutable corpus")
    artifact = (
        practice_root / "sanitized" / "objects" / digest[:2] / f"{digest}.hwpx"
    )
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(source_bytes)
    campaign_root = runtime_root / "campaigns" / manifest["campaignId"]
    tasks_root = campaign_root / "tasks"
    tasks_root.mkdir(parents=True)
    _write_json(campaign_root / "manifest.json", manifest)
    _write_json(tasks_root / f"{run_ref['runId']}.json", task)
    return {
        "sourceRoot": source_root,
        "practiceRoot": practice_root,
        "runtimeRoot": runtime_root,
        "skillRoot": skill_root,
        "source": source,
        "artifact": artifact,
        "manifest": manifest,
        "runRef": run_ref,
        "task": task,
        "taskPath": tasks_root / f"{run_ref['runId']}.json",
    }


def _build(namespace: Mapping[str, Any] | None = None):
    return build_practice_campaign_service(
        namespace or {}, _allow_same_device_for_tests=True
    )


@pytest.fixture(autouse=True)
def _reset_runtime():
    _reset_practice_campaign_runtime_for_tests()
    yield
    _reset_practice_campaign_runtime_for_tests()


def test_production_builder_requires_cross_device_roots_before_mutable_state(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    assert fixture["sourceRoot"].stat().st_dev == fixture["practiceRoot"].stat().st_dev

    with pytest.raises(PracticeRuntimeError, match="CAMPAIGN_UNAVAILABLE"):
        build_practice_campaign_service({})
    factory = server._PRACTICE_CAMPAIGN_RUNTIME_FACTORY
    assert callable(factory)
    with pytest.raises(PracticeRuntimeError, match="CAMPAIGN_UNAVAILABLE"):
        factory(vars(server))

    assert not (
        fixture["runtimeRoot"] / "queue" / "practice-campaign-queue.sqlite3"
    ).exists()
    assert not (fixture["runtimeRoot"] / "workflow" / "workflows.sqlite3").exists()
    assert not (fixture["runtimeRoot"] / "workflow" / "workflows.sqlite3.key").exists()

    synthetic = _build()
    assert synthetic.startup_reaper_receipt["supported"] is True


def test_env_runtime_lazily_wires_public_start_preview_and_queue(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "_PRACTICE_CAMPAIGN_SERVICE_OVERRIDE", None)
    service = _build(vars(server))
    monkeypatch.setattr(
        server, "_PRACTICE_CAMPAIGN_RUNTIME_FACTORY", lambda _namespace: service
    )
    source_before = fixture["source"].read_bytes()

    preview = server.start_practice_campaign(
        fixture["manifest"]["campaignId"], "runtime-start-001"
    )
    started = server.start_practice_campaign(
        fixture["manifest"]["campaignId"], "runtime-start-001", confirm=True
    )
    first = server._practice_campaign_service()
    replay = server._practice_campaign_service()

    assert preview["ok"] is True
    assert preview["requiresConfirmation"] is True
    assert started["ok"] is True
    assert started["state"] == "queued"
    assert first is replay
    assert first.startup_reaper_receipt["supported"] is True
    assert isinstance(first.terminal_artifact_hook, _TerminalArtifactStore)
    encoded = json.dumps([preview, started], ensure_ascii=False)
    assert str(fixture["sourceRoot"]) not in encoded
    assert str(fixture["practiceRoot"]) not in encoded
    assert ".hwpx" not in encoded
    assert fixture["source"].read_bytes() == source_before

    traversal = server.start_practice_campaign("../manifest.json", "runtime-start-002")
    assert traversal["ok"] is False
    assert traversal["errorCode"] == "CAMPAIGN_ID_INVALID"
    assert "manifest" not in json.dumps(traversal)


def test_task_resolution_uses_only_fixed_opaque_and_content_addressed_paths(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    service = _build()
    run_ref = fixture["runRef"]
    lease = SimpleNamespace(slot=0, run_id=run_ref["runId"])

    resolved = service.task_resolver(fixture["manifest"], lease)

    assert resolved.source_artifact == fixture["artifact"]
    assert resolved.parameters == {"operationKind": "table", "operations": []}

    injected = dict(fixture["task"])
    injected["parameters"] = {"outputPath": "/tmp/private.hwpx"}
    _write_json(fixture["taskPath"], injected)
    with pytest.raises(ValueError, match="forbidden"):
        service.task_resolver(fixture["manifest"], lease)

    tampered = dict(fixture["task"])
    tampered["parameters"] = {"operationKind": "body", "operations": []}
    _write_json(fixture["taskPath"], tampered)
    with pytest.raises(ValueError, match="content address"):
        service.task_resolver(fixture["manifest"], lease)


@pytest.mark.parametrize(
    "attack", ["not-hwpx", "pii", "preview-pii", "attribute-pii", "encrypted"]
)
def test_task_resolution_independently_rejects_unsafe_sanitized_package_bytes(
    tmp_path: Path, monkeypatch, attack: str
) -> None:
    if attack == "not-hwpx":
        fixture = _layout(tmp_path, monkeypatch, artifact_bytes=b"not a package")
    elif attack == "pii":
        fixture = _layout(
            tmp_path,
            monkeypatch,
            artifact_text="Synthetic contact 010-1234-5678",
        )
    elif attack == "preview-pii":
        valid = _minimal_hwpx(tmp_path / "preview-source.hwpx")
        fixture = _layout(
            tmp_path,
            monkeypatch,
            artifact_bytes=_replace_zip_member(
                valid,
                "Preview/PrvText.txt",
                b"Synthetic contact 010-1234-5678\r\n",
            ),
        )
    elif attack == "attribute-pii":
        valid = _minimal_hwpx(tmp_path / "attribute-source.hwpx")
        with zipfile.ZipFile(io.BytesIO(valid), mode="r") as package:
            header = package.read("Contents/header.xml")
        fixture = _layout(
            tmp_path,
            monkeypatch,
            artifact_bytes=_replace_zip_member(
                valid,
                "Contents/header.xml",
                header.replace(
                    b'engName="Normal"', b'engName="010-1234-5678"', 1
                ),
            ),
        )
    else:
        valid = _minimal_hwpx(tmp_path / "encrypted-source.hwpx")
        fixture = _layout(
            tmp_path, monkeypatch, artifact_bytes=_mark_zip_encrypted(valid)
        )
    service = _build()
    lease = SimpleNamespace(slot=0, run_id=fixture["runRef"]["runId"])

    with pytest.raises(ValueError, match="sanitized HWPX validation"):
        service.task_resolver(fixture["manifest"], lease)


def test_task_resolution_rejects_sanitized_package_over_byte_limit(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runtime_module,
        "_MAX_SANITIZED_HWPX_BYTES",
        fixture["artifact"].stat().st_size - 1,
    )
    service = _build()
    lease = SimpleNamespace(slot=0, run_id=fixture["runRef"]["runId"])

    with pytest.raises(ValueError, match="artifact is unavailable"):
        service.task_resolver(fixture["manifest"], lease)


@pytest.mark.parametrize(
    "failure", ["missing-storage", "overlap", "symlink", "skill-symlink"]
)
def test_runtime_refuses_unconfigured_overlapping_or_non_strict_roots(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    if failure == "missing-storage":
        (fixture["runtimeRoot"] / "queue").rmdir()
    elif failure == "overlap":
        monkeypatch.setenv("HWPX_CORPUS_SOURCE", str(fixture["practiceRoot"]))
    elif failure == "symlink":
        queue = fixture["runtimeRoot"] / "queue"
        queue.rmdir()
        queue.symlink_to(fixture["runtimeRoot"] / "workflow", target_is_directory=True)
    else:
        replacement = tmp_path / "replacement-skill"
        fixture["skillRoot"].rename(replacement)
        fixture["skillRoot"].symlink_to(replacement, target_is_directory=True)

    with pytest.raises(PracticeRuntimeError, match="CAMPAIGN_UNAVAILABLE"):
        _build()


def test_runtime_rejects_task_symlink_and_source_is_never_modified(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    service = _build()
    run_ref = fixture["runRef"]
    lease = SimpleNamespace(slot=0, run_id=run_ref["runId"])
    source_before = fixture["source"].read_bytes()
    task_path = fixture["taskPath"]
    replacement = task_path.with_name("replacement.json")
    task_path.rename(replacement)
    task_path.symlink_to(replacement)

    with pytest.raises(KeyError):
        service.task_resolver(fixture["manifest"], lease)
    assert fixture["source"].read_bytes() == source_before


def test_runtime_refuses_exact_provenance_skew_before_creating_state(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    provenance_path = fixture["runtimeRoot"] / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["stack"]["server"]["version"] = "999.0.0"
    _write_json(provenance_path, provenance)

    with pytest.raises(PracticeRuntimeError, match="CAMPAIGN_UNAVAILABLE"):
        _build()

    assert not (fixture["runtimeRoot"] / "queue" / "practice-campaign-queue.sqlite3").exists()
    assert not (fixture["runtimeRoot"] / "workflow" / "workflows.sqlite3").exists()


def _source_snapshot(root: Path) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        payload = path.read_bytes() if path.is_file() else b""
        rows.append(
            (
                path.relative_to(root).as_posix(),
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
                _digest(payload),
            )
        )
    return tuple(rows)


@pytest.mark.parametrize("terminal", ["workflows.sqlite3", "workflows.sqlite3.key"])
@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_workflow_store_terminal_alias_is_rejected_before_any_state_or_source_write(
    tmp_path: Path, monkeypatch, terminal: str, alias_kind: str
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    workflow_root = fixture["runtimeRoot"] / "workflow"
    if alias_kind == "symlink":
        (workflow_root / terminal).symlink_to(fixture["source"])
    else:
        os.link(fixture["source"], workflow_root / terminal)
    before = _source_snapshot(fixture["sourceRoot"])

    with pytest.raises(PracticeRuntimeError, match="CAMPAIGN_UNAVAILABLE"):
        _build()

    assert _source_snapshot(fixture["sourceRoot"]) == before
    assert not (fixture["runtimeRoot"] / "queue" / "practice-campaign-queue.sqlite3").exists()
    other = (
        "workflows.sqlite3.key"
        if terminal == "workflows.sqlite3"
        else "workflows.sqlite3"
    )
    assert not (workflow_root / other).exists()


def test_workflow_store_outputs_are_revalidated_after_construction(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    original_store = runtime_module.WorkflowStore

    def _store_with_late_alias(path: Path):
        store = original_store(path)
        key = path.with_name(path.name + ".key")
        key.unlink(missing_ok=True)
        os.link(fixture["source"], key)
        return store

    monkeypatch.setattr(runtime_module, "WorkflowStore", _store_with_late_alias)
    source_before = fixture["source"].read_bytes()

    with pytest.raises(PracticeRuntimeError, match="CAMPAIGN_UNAVAILABLE"):
        _build()

    assert fixture["source"].read_bytes() == source_before
    assert not (
        fixture["runtimeRoot"] / "queue" / "practice-campaign-queue.sqlite3"
    ).exists()


@pytest.mark.parametrize(
    "attack", ["corpus", "false-synthetic", "pii-count", "pii"]
)
def test_task_privacy_attestation_is_content_bound_and_fail_closed(
    tmp_path: Path, monkeypatch, attack: str
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    service = _build()
    task = dict(fixture["task"])
    if attack == "corpus":
        task["artifactScope"] = "corpus"
    elif attack == "false-synthetic":
        task["privacy"] = {
            "syntheticInputsOnly": False,
            "highConfidencePiiCount": 0,
        }
    elif attack == "pii-count":
        task["privacy"] = {
            "syntheticInputsOnly": True,
            "highConfidencePiiCount": 1,
        }
    else:
        task["parameters"] = {
            "operationKind": "table",
            "contact": "010-1234-5678",
            "operations": [],
        }
    task["dispatch"] = {
        **task["dispatch"],
        "seedSha256": _task_dispatch_sha256(task),
    }
    _write_json(fixture["taskPath"], task)
    lease = SimpleNamespace(slot=0, run_id=fixture["runRef"]["runId"])

    with pytest.raises(ValueError):
        service.task_resolver(fixture["manifest"], lease)


def test_provenance_rejects_same_version_skill_byte_skew_before_state_creation(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    (fixture["skillRoot"] / "references" / "runner.md").write_text(
        "tampered with the same version\n", encoding="utf-8"
    )

    with pytest.raises(PracticeRuntimeError, match="CAMPAIGN_UNAVAILABLE"):
        _build()

    assert not (fixture["runtimeRoot"] / "queue" / "practice-campaign-queue.sqlite3").exists()
    assert not (fixture["runtimeRoot"] / "workflow" / "workflows.sqlite3").exists()


def test_terminal_artifact_store_is_atomic_content_addressed_and_path_free(
    tmp_path: Path
) -> None:
    results = tmp_path / "results"
    results.mkdir()
    output = tmp_path / "result.hwpx"
    payload = b"PK\x03\x04synthetic completed hwpx"
    output.write_bytes(payload)
    digest = _digest(payload)
    run_id = "PRUN-00000000000000000001"
    outcome = SimpleNamespace(
        output_path=output,
        sandbox=SimpleNamespace(root=tmp_path),
        run_receipt={
            "state": "completed",
            "runId": run_id,
            "artifacts": [
                {
                    "role": "output",
                    "sha256": digest,
                    "bytes": len(payload),
                }
            ],
        },
    )
    store = _TerminalArtifactStore(results)

    first = store(SimpleNamespace(), outcome)
    replay = store(SimpleNamespace(), outcome)
    retained = results / digest[:2] / digest / f"{run_id}.hwpx"

    assert first == replay
    assert retained.read_bytes() == payload
    assert first == {
        "schema": "hwpx.practice-terminal-artifact/v1",
        "runId": run_id,
        "artifactId": (
            "RESULT-"
            + hashlib.sha256(f"{run_id}:{digest}".encode("ascii"))
            .hexdigest()[:20]
            .upper()
        ),
        "artifactSha256": digest,
        "bytes": len(payload),
        "retained": True,
        "privateStorageCoordinatesExposed": False,
    }
    encoded = json.dumps(first)
    assert str(results) not in encoded
    assert str(output) not in encoded


def test_terminal_artifact_store_never_follows_existing_target_symlink(
    tmp_path: Path
) -> None:
    results = tmp_path / "results"
    results.mkdir()
    output = tmp_path / "result.hwpx"
    output.write_bytes(b"PK\x03\x04synthetic result")
    digest = _digest(output.read_bytes())
    run_id = "PRUN-00000000000000000002"
    digest_root = results / digest[:2] / digest
    digest_root.mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"unchanged")
    (digest_root / f"{run_id}.hwpx").symlink_to(outside)
    outcome = SimpleNamespace(
        output_path=output,
        sandbox=SimpleNamespace(root=tmp_path),
        run_receipt={
            "state": "completed",
            "runId": run_id,
            "artifacts": [
                {
                    "role": "output",
                    "sha256": digest,
                    "bytes": output.stat().st_size,
                }
            ],
        },
    )

    with pytest.raises(PracticeRuntimeError):
        _TerminalArtifactStore(results)(SimpleNamespace(), outcome)
    assert outside.read_bytes() == b"unchanged"


def test_startup_reaper_is_bounded_and_emits_only_path_free_evidence() -> None:
    run_id = "PRUN-00000000000000000003"
    digest = _digest("source")

    class Queue:
        def __init__(self) -> None:
            self.candidates = [
                {"runId": run_id, "startArtifactSha256": digest}
            ]

        def recover(self):
            return {
                "schema": "hwpx.practice-queue-recovery/v1",
                "recoveredSlots": 0,
                "campaignIds": [],
                "privateStorageCoordinatesExposed": False,
            }

        def terminal_cleanup_candidates(self, *, limit: int):
            assert limit == 64
            return tuple(self.candidates[:limit])

        def ack_terminal_cleanup(
            self, candidate_run_id: str, expected_sha256: str
        ):
            assert (candidate_run_id, expected_sha256) == (run_id, digest)
            self.candidates.pop(0)
            return {
                "schema": "hwpx.practice-terminal-cleanup-ack/v1",
                "runId": run_id,
                "startArtifactSha256": digest,
                "acknowledged": True,
                "idempotentReplay": False,
                "privateStorageCoordinatesExposed": False,
            }

    class Sandbox:
        def open_owned(self, candidate_run_id: str, expected_sha256: str):
            assert (candidate_run_id, expected_sha256) == (run_id, digest)
            return SimpleNamespace(run_id=run_id)

        def cleanup(self, owned):
            assert owned.run_id == run_id
            return {
                "schema": "hwpx.practice-sandbox-cleanup/v1",
                "sandboxId": "SBX-00000000000000000003",
                "runId": run_id,
                "deleted": True,
                "privateStorageCoordinatesExposed": False,
            }

    receipt = _startup_reap(Queue(), Sandbox())

    assert receipt["supported"] is True
    assert receipt["candidateCount"] == 1
    assert receipt["cleanedCount"] == 1
    assert receipt["acknowledgedCount"] == 1
    assert len(receipt["cleanupReceiptSha256"]) == 1
    encoded = json.dumps(receipt)
    assert "/tmp/" not in encoded
    assert "sourcePath" not in encoded
    assert ".hwpx" not in encoded


def test_service_startup_reaps_queue_authorized_terminal_orphan(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    service = _build()
    service.queue.enqueue(fixture["manifest"])
    lease = service.queue.claim("startup-reaper-worker")
    assert lease is not None
    orphan = service.dispatcher.sandbox_manager.prepare(
        fixture["artifact"],
        run_id=fixture["runRef"]["runId"],
        expected_sha256=fixture["runRef"]["startArtifactSha256"],
    )
    service.queue.fail(lease, "TEST_TERMINAL_FAILURE")
    assert orphan.root.exists()

    restarted = _build()

    assert not orphan.root.exists()
    assert restarted.startup_reaper_receipt["supported"] is True
    assert restarted.startup_reaper_receipt["candidateCount"] == 1
    assert restarted.startup_reaper_receipt["cleanedCount"] == 1
    encoded = json.dumps(restarted.startup_reaper_receipt)
    assert str(fixture["sourceRoot"]) not in encoded
    assert str(fixture["practiceRoot"]) not in encoded


def test_service_startup_recovers_expired_cancellation_before_reaping(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = _layout(tmp_path, monkeypatch)
    service = _build()
    campaign_id = fixture["manifest"]["campaignId"]
    service.queue.enqueue(fixture["manifest"])
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    lease = service.queue.claim(
        "startup-cancel-worker", lease_seconds=1, now=past
    )
    assert lease is not None
    orphan = service.dispatcher.sandbox_manager.prepare(
        fixture["artifact"],
        run_id=fixture["runRef"]["runId"],
        expected_sha256=fixture["runRef"]["startArtifactSha256"],
    )
    cancelling = service.queue.cancel(campaign_id, now=past)
    assert cancelling["state"] == "cancelling"
    assert orphan.root.exists()

    restarted = _build()

    assert restarted.startup_reaper_receipt["recoveredSlots"] == 1
    assert restarted.startup_reaper_receipt["candidateCount"] == 1
    assert restarted.startup_reaper_receipt["acknowledgedCount"] == 1
    assert restarted.queue.status(campaign_id)["state"] == "cancelled"
    assert restarted.queue.terminal_cleanup_candidates() == ()
    assert not orphan.root.exists()


def test_startup_reaper_ack_cursor_prevents_starvation_across_restarts() -> None:
    candidates = [
        {
            "runId": f"PRUN-{slot:020X}",
            "startArtifactSha256": _digest(f"source-{slot}"),
        }
        for slot in range(257)
    ]

    class Queue:
        def recover(self):
            return {
                "schema": "hwpx.practice-queue-recovery/v1",
                "recoveredSlots": 0,
                "campaignIds": [],
                "privateStorageCoordinatesExposed": False,
            }

        def terminal_cleanup_candidates(self, *, limit: int):
            return tuple(candidates[:limit])

        def ack_terminal_cleanup(self, run_id: str, digest: str):
            assert candidates[0] == {
                "runId": run_id,
                "startArtifactSha256": digest,
            }
            candidates.pop(0)
            return {
                "schema": "hwpx.practice-terminal-cleanup-ack/v1",
                "runId": run_id,
                "startArtifactSha256": digest,
                "acknowledged": True,
                "idempotentReplay": False,
                "privateStorageCoordinatesExposed": False,
            }

    class Sandbox:
        def open_owned(self, _run_id: str, _expected_sha256: str):
            return None

    queue = Queue()
    first = _startup_reap(queue, Sandbox())
    second = _startup_reap(queue, Sandbox())

    assert first["candidateCount"] == 256
    assert first["acknowledgedCount"] == 256
    assert first["limitReached"] is True
    assert second["candidateCount"] == 1
    assert second["acknowledgedCount"] == 1
    assert second["limitReached"] is False
    assert candidates == []


@pytest.mark.parametrize("poison", ["malformed", "open-failure", "cleanup-failure"])
def test_startup_reaper_isolates_poison_candidate_and_advances_valid_second(
    poison: str,
) -> None:
    poison_run_id = "PRUN-00000000000000000004"
    poison_digest = _digest("poison-source")
    valid_run_id = "PRUN-00000000000000000005"
    valid_digest = _digest("valid-source")
    poison_candidate = (
        {"runId": "not-opaque", "startArtifactSha256": "bad"}
        if poison == "malformed"
        else {
            "runId": poison_run_id,
            "startArtifactSha256": poison_digest,
        }
    )

    class Queue:
        def __init__(self) -> None:
            self.candidates = [
                poison_candidate,
                {
                    "runId": valid_run_id,
                    "startArtifactSha256": valid_digest,
                },
            ]
            self.acked: list[str] = []

        def recover(self):
            return {
                "schema": "hwpx.practice-queue-recovery/v1",
                "recoveredSlots": 0,
                "campaignIds": [],
                "privateStorageCoordinatesExposed": False,
            }

        def terminal_cleanup_candidates(self, *, limit: int):
            return tuple(self.candidates[:limit])

        def ack_terminal_cleanup(self, run_id: str, digest: str):
            assert (run_id, digest) == (valid_run_id, valid_digest)
            self.acked.append(run_id)
            self.candidates = [
                candidate
                for candidate in self.candidates
                if candidate.get("runId") != run_id
            ]
            return {
                "schema": "hwpx.practice-terminal-cleanup-ack/v1",
                "runId": run_id,
                "startArtifactSha256": digest,
                "acknowledged": True,
                "idempotentReplay": False,
                "privateStorageCoordinatesExposed": False,
            }

    class Sandbox:
        def open_owned(self, run_id: str, _expected_sha256: str):
            if poison == "open-failure" and run_id == poison_run_id:
                raise RuntimeError("/private/poison/open")
            return SimpleNamespace(run_id=run_id)

        def cleanup(self, owned):
            if poison == "cleanup-failure" and owned.run_id == poison_run_id:
                raise RuntimeError("/private/poison/cleanup")
            return {
                "schema": "hwpx.practice-sandbox-cleanup/v1",
                "sandboxId": "SBX-00000000000000000005",
                "runId": owned.run_id,
                "deleted": True,
                "privateStorageCoordinatesExposed": False,
            }

    queue = Queue()
    receipt = _startup_reap(queue, Sandbox())

    assert receipt["candidateCount"] == 2
    assert receipt["failureCount"] == 1
    assert receipt["cleanedCount"] == 1
    assert receipt["acknowledgedCount"] == 1
    assert queue.acked == [valid_run_id]
    assert queue.candidates == [poison_candidate]
    assert "/private/" not in json.dumps(receipt)
