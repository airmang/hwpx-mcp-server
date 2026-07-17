import os
import zipfile
from pathlib import Path

import pytest

import hwpx_mcp_server.ops_services.save_policy as save_policy_module
import hwpx_mcp_server.server as server_module

from hwpx_mcp_server.core.transactions import semantic_diff
from hwpx_mcp_server.server import (
    add_paragraph,
    apply_edits,
    create_document,
    get_document_text,
    get_table_text,
    undo_last_edit,
)
from hwpx_mcp_server.storage import build_hwpx_open_safety_report
from hwpx_mcp_server.workspace import WorkspacePathError


def _replace_zip_part(path: Path, part_name: str, payload: bytes) -> None:
    replacement = path.with_suffix(path.suffix + ".rewrite")
    with zipfile.ZipFile(path, "r") as source:
        with zipfile.ZipFile(replacement, "w") as target:
            for info in source.infolist():
                target.writestr(
                    info,
                    payload if info.filename == part_name else source.read(info.filename),
                )
    os.replace(replacement, path)


def _recovery_files(base: Path, marker: str) -> list[Path]:
    return list(
        base.parent.glob(f".{base.name}.{marker}.*.recovery")
    )


def test_dry_run_does_not_modify_document_or_create_backup(tmp_path: Path) -> None:
    target = tmp_path / "dry-run.hwpx"
    create_document(str(target))
    before = target.read_bytes()

    result = add_paragraph(str(target), "dry-run paragraph", dry_run=True)

    assert target.read_bytes() == before
    assert not target.with_suffix(target.suffix + ".bak").exists()
    assert result["dryRun"] is True
    assert result["wouldSave"] is True
    assert result["openSafety"]["ok"] is True
    assert result["semanticDiff"]["changed"] is True
    assert "dry-run paragraph" not in get_document_text(str(target))["text"]


def test_apply_edits_saves_once_with_backup_and_semantic_diff(tmp_path: Path) -> None:
    target = tmp_path / "apply.hwpx"
    create_document(str(target))

    result = apply_edits(
        str(target),
        [
            {"type": "add_paragraph", "text": "2025 plan"},
            {"type": "replace_text", "findText": "2025", "replaceText": "2026"},
            {"type": "add_table", "rows": 1, "cols": 2, "data": [["label", "old"]]},
            {"type": "set_table_cell_text", "tableIndex": 0, "row": 0, "col": 1, "text": "new"},
        ],
    )

    assert result["ok"] is True
    assert result["operationsApplied"] == 4
    assert result["openSafety"]["ok"] is True
    assert result["backup"]["created"] is True
    assert target.with_suffix(target.suffix + ".bak").exists()
    assert result["semanticDiff"]["changed"] is True
    assert "2026 plan" in get_document_text(str(target))["text"]
    assert get_table_text(str(target), 0)["data"] == [["label", "new"]]


def test_apply_edits_failure_rolls_back_without_backup(tmp_path: Path) -> None:
    target = tmp_path / "rollback.hwpx"
    create_document(str(target))
    before = target.read_bytes()

    result = apply_edits(
        str(target),
        [
            {"type": "add_paragraph", "text": "not saved"},
            {"type": "set_table_cell_text", "tableIndex": 99, "row": 0, "col": 0, "text": "boom"},
        ],
    )

    assert result["ok"] is False
    assert result["rolledBack"] is True
    assert result["failedOperationIndex"] == 1
    assert target.read_bytes() == before
    assert not target.with_suffix(target.suffix + ".bak").exists()
    assert "not saved" not in get_document_text(str(target))["text"]


def test_undo_last_edit_restores_previous_backup(tmp_path: Path) -> None:
    target = tmp_path / "undo.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")

    undo = undo_last_edit(str(target))
    text = get_document_text(str(target))["text"]

    assert undo["restored"] is True
    assert undo["openSafety"]["ok"] is True
    assert undo["semanticDiff"]["changed"] is True
    assert "first saved paragraph" in text
    assert "second saved paragraph" not in text
    assert not list(tmp_path.glob(".*.undo-recovery.*.recovery"))


@pytest.mark.skipif(os.name == "nt", reason="symlink setup requires POSIX")
def test_undo_last_edit_rejects_backup_symlink_without_importing_outside_bytes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "undo-symlink.hwpx"
    outside_staging = tmp_path / "outside.hwpx"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-undo.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "authorized current")
    create_document(str(outside_staging))
    add_paragraph(str(outside_staging), "outside backup payload")
    os.replace(outside_staging, outside)
    target_before = target.read_bytes()
    outside_before = outside.read_bytes()
    backup = target.with_suffix(target.suffix + ".bak")
    backup.unlink()
    backup.symlink_to(outside)

    try:
        with pytest.raises(WorkspacePathError):
            undo_last_edit(str(target))

        assert target.read_bytes() == target_before
        assert outside.read_bytes() == outside_before
        assert backup.is_symlink()
    finally:
        outside.unlink(missing_ok=True)


def test_undo_last_edit_never_claims_same_bytes_external_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "undo-race.hwpx"
    replacement = tmp_path / "external-replacement.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    backup = target.with_suffix(target.suffix + ".bak")
    target_before = target.read_bytes()
    backup_before = backup.read_bytes()
    original_diff = server_module._OPS._services.save._semantic_diff_bytes
    replaced = False

    def replace_after_diff(before: bytes, after: bytes) -> dict:
        nonlocal replaced
        report = original_diff(before, after)
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(target.stat().st_mode & 0o777)
        os.replace(replacement, target)
        replaced = True
        return report

    monkeypatch.setattr(
        server_module._OPS._services.save,
        "_semantic_diff_bytes",
        replace_after_diff,
    )

    with pytest.raises(WorkspacePathError):
        undo_last_edit(str(target))

    assert replaced is True
    assert target.read_bytes() == backup_before
    assert backup.read_bytes() == target_before


def test_undo_preserves_owned_target_when_backup_publication_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "undo-backup-race.hwpx"
    replacement = tmp_path / "external-backup-replacement.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    backup = target.with_suffix(target.suffix + ".bak")
    target_before = target.read_bytes()
    backup_before = backup.read_bytes()
    original_diff = server_module._OPS._services.save._semantic_diff_bytes
    replaced = False

    def replace_backup_after_diff(before: bytes, after: bytes) -> dict:
        nonlocal replaced
        report = original_diff(before, after)
        replacement.write_bytes(backup.read_bytes())
        replacement.chmod(backup.stat().st_mode & 0o777)
        os.replace(replacement, backup)
        replaced = True
        return report

    monkeypatch.setattr(
        server_module._OPS._services.save,
        "_semantic_diff_bytes",
        replace_backup_after_diff,
    )

    with pytest.raises(WorkspacePathError):
        undo_last_edit(str(target))

    assert replaced is True
    assert target.read_bytes() == backup_before
    assert backup.read_bytes() == target_before


def test_undo_rolls_back_both_owned_publications_after_evidence_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "undo-owned-rollback.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    backup = target.with_suffix(target.suffix + ".bak")
    target_before = target.read_bytes()
    backup_before = backup.read_bytes()

    def fail_diff(before: bytes, after: bytes) -> dict:
        raise RuntimeError("forced undo evidence failure")

    monkeypatch.setattr(
        server_module._OPS._services.save, "_semantic_diff_bytes", fail_diff
    )

    with pytest.raises(RuntimeError, match="undo evidence"):
        undo_last_edit(str(target))

    assert target.read_bytes() == target_before
    assert backup.read_bytes() == backup_before
    target_recovery = _recovery_files(target, "undo-recovery")
    backup_recovery = _recovery_files(backup, "undo-recovery")
    assert len(target_recovery) == 1
    assert target_recovery[0].read_bytes() == target_before
    assert len(backup_recovery) == 1
    assert backup_recovery[0].read_bytes() == backup_before


def test_undo_preserves_both_preimages_when_backup_rollback_loses_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "undo-second-rollback-race.hwpx"
    external = tmp_path / "undo-external-backup.hwpx"
    external_staging = tmp_path / "undo-external-backup.staging.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    create_document(str(external_staging))
    add_paragraph(str(external_staging), "external backup owner")
    os.replace(external_staging, external)
    backup = target.with_suffix(target.suffix + ".bak")
    target_before = target.read_bytes()
    backup_before = backup.read_bytes()
    external_before = external.read_bytes()
    storage = server_module._OPS.storage
    original_publish = storage.atomic_publish_bytes
    replaced = False

    def fail_diff(before: bytes, after: bytes) -> dict:
        raise RuntimeError("forced undo evidence failure")

    def replace_before_backup_rollback(guard, data, **kwargs):
        nonlocal replaced
        if guard.path == backup and data == backup_before:
            os.replace(external, backup)
            replaced = True
        return original_publish(guard, data, **kwargs)

    monkeypatch.setattr(
        server_module._OPS._services.save, "_semantic_diff_bytes", fail_diff
    )
    monkeypatch.setattr(
        storage,
        "atomic_publish_bytes",
        replace_before_backup_rollback,
    )

    with pytest.raises(RuntimeError, match="undo evidence"):
        undo_last_edit(str(target))

    target_recovery = _recovery_files(target, "undo-recovery")
    backup_recovery = _recovery_files(backup, "undo-recovery")
    assert replaced is True
    assert target.read_bytes() == target_before
    assert backup.read_bytes() == external_before
    assert len(target_recovery) == 1
    assert target_recovery[0].read_bytes() == target_before
    assert len(backup_recovery) == 1
    assert backup_recovery[0].read_bytes() == backup_before


def test_undo_recovery_does_not_overwrite_existing_numbered_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "undo-existing-recovery.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    backup = target.with_suffix(target.suffix + ".bak")
    target_before = target.read_bytes()
    backup_before = backup.read_bytes()
    existing = backup.with_name(backup.name + ".undo-recovery")
    existing.write_bytes(b"existing recovery owner")
    storage = server_module._OPS.storage
    original_publish = storage.atomic_publish_bytes
    recovery_publications: list[Path] = []

    def fail_diff(before: bytes, after: bytes) -> dict:
        raise RuntimeError("forced undo evidence failure")

    def record_recovery_publication(guard, data, **kwargs):
        if ".undo-recovery" in guard.path.name:
            recovery_publications.append(guard.path)
        return original_publish(guard, data, **kwargs)

    monkeypatch.setattr(
        server_module._OPS._services.save, "_semantic_diff_bytes", fail_diff
    )
    monkeypatch.setattr(
        storage,
        "atomic_publish_bytes",
        record_recovery_publication,
    )

    with pytest.raises(RuntimeError, match="undo evidence"):
        undo_last_edit(str(target))

    assert target.read_bytes() == target_before
    assert backup.read_bytes() == backup_before
    assert existing.read_bytes() == b"existing recovery owner"
    assert len(recovery_publications) == 2
    assert recovery_publications[0] in _recovery_files(
        target,
        "undo-recovery",
    )
    assert recovery_publications[1] in _recovery_files(
        backup,
        "undo-recovery",
    )
    assert recovery_publications[1].read_bytes() == backup_before


def test_undo_target_rollback_claim_loss_preserves_a_b_and_external_c(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "undo-target-rollback-race.hwpx"
    external = tmp_path / "undo-target-external.hwpx"
    external_staging = tmp_path / "undo-target-external.staging.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    create_document(str(external_staging))
    add_paragraph(str(external_staging), "external target owner")
    os.replace(external_staging, external)
    backup = target.with_suffix(target.suffix + ".bak")
    target_before = target.read_bytes()
    backup_before = backup.read_bytes()
    external_before = external.read_bytes()
    storage = server_module._OPS.storage
    original_publish = storage.atomic_publish_bytes
    replaced = False

    def fail_diff(before: bytes, after: bytes) -> dict:
        raise RuntimeError("forced undo evidence failure")

    def replace_after_target_rollback(guard, data, **kwargs):
        nonlocal replaced
        publication = original_publish(guard, data, **kwargs)
        if guard.path == target and data == target_before:
            os.replace(external, target)
            replaced = True
        return publication

    monkeypatch.setattr(
        server_module._OPS._services.save, "_semantic_diff_bytes", fail_diff
    )
    monkeypatch.setattr(
        storage,
        "atomic_publish_bytes",
        replace_after_target_rollback,
    )

    with pytest.raises(RuntimeError, match="undo evidence"):
        undo_last_edit(str(target))

    target_recovery = _recovery_files(target, "undo-recovery")
    backup_recovery = _recovery_files(backup, "undo-recovery")
    assert replaced is True
    assert target.read_bytes() == external_before
    assert backup.read_bytes() == backup_before
    assert len(target_recovery) == 1
    assert target_recovery[0].read_bytes() == target_before
    assert len(backup_recovery) == 1
    assert backup_recovery[0].read_bytes() == backup_before


@pytest.mark.parametrize("seam", ["target", "backup"])
def test_undo_publish_then_claim_loss_preserves_both_preimages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
) -> None:
    target = tmp_path / f"undo-{seam}-claim-loss.hwpx"
    external = tmp_path / f"undo-{seam}-claim-external.hwpx"
    external_staging = tmp_path / f"undo-{seam}-claim-staging.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    create_document(str(external_staging))
    add_paragraph(str(external_staging), f"external {seam} owner")
    os.replace(external_staging, external)
    backup = target.with_suffix(target.suffix + ".bak")
    target_before = target.read_bytes()
    backup_before = backup.read_bytes()
    external_before = external.read_bytes()
    storage = server_module._OPS.storage
    original_publish = storage.atomic_publish_bytes
    replaced = False

    def publish_then_lose_claim(guard, data, **kwargs):
        nonlocal replaced
        publication = original_publish(guard, data, **kwargs)
        matches = (
            seam == "target"
            and guard.path == target
            and data == backup_before
        ) or (
            seam == "backup"
            and guard.path == backup
            and data == target_before
        )
        if matches:
            os.replace(external, guard.path)
            replaced = True
            raise WorkspacePathError(
                "forced publish-then-claim loss",
                code="WORKSPACE_PATH_CHANGED",
                reason="output_target_changed",
            )
        return publication

    monkeypatch.setattr(
        storage,
        "atomic_publish_bytes",
        publish_then_lose_claim,
    )

    with pytest.raises(WorkspacePathError, match="claim loss"):
        undo_last_edit(str(target))

    target_recovery = _recovery_files(target, "undo-recovery")
    backup_recovery = _recovery_files(backup, "undo-recovery")
    assert replaced is True
    if seam == "target":
        assert target.read_bytes() == external_before
        assert backup.read_bytes() == backup_before
    else:
        assert target.read_bytes() == target_before
        assert backup.read_bytes() == external_before
    assert len(target_recovery) == 1
    assert target_recovery[0].read_bytes() == target_before
    assert len(backup_recovery) == 1
    assert backup_recovery[0].read_bytes() == backup_before


def test_undo_recovery_claim_loss_retries_fresh_random_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "undo-recovery-claim-loss.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    backup = target.with_suffix(target.suffix + ".bak")
    target_before = target.read_bytes()
    storage = server_module._OPS.storage
    original_read = storage.read_guarded_bytes
    replacement = b"external recovery owner"
    replaced = False

    def fail_diff(before: bytes, after: bytes) -> dict:
        raise RuntimeError("forced undo evidence failure")

    def replace_first_recovery_before_claim(guard):
        nonlocal replaced
        if (
            not replaced
            and guard.path in _recovery_files(
                target,
                "undo-recovery",
            )
        ):
            guard.path.write_bytes(replacement)
            replaced = True
        return original_read(guard)

    monkeypatch.setattr(
        server_module._OPS._services.save, "_semantic_diff_bytes", fail_diff
    )
    monkeypatch.setattr(
        storage,
        "read_guarded_bytes",
        replace_first_recovery_before_claim,
    )

    with pytest.raises(RuntimeError, match="undo evidence"):
        undo_last_edit(str(target))

    target_recoveries = _recovery_files(target, "undo-recovery")
    assert replaced is True
    assert sorted(path.read_bytes() for path in target_recoveries) == sorted(
        [replacement, target_before]
    )
    assert _recovery_files(backup, "undo-recovery")


def test_undo_ignores_exhausted_legacy_recovery_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "undo-legacy-namespace.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    backup = target.with_suffix(target.suffix + ".bak")
    target_before = target.read_bytes()
    backup_before = backup.read_bytes()
    legacy_paths = [
        backup.with_name(
            backup.name
            + ".undo-recovery"
            + ("" if index == 0 else f".{index}")
        )
        for index in range(1000)
    ]
    for index, legacy in enumerate(legacy_paths):
        legacy.write_bytes(f"legacy-{index}".encode())

    def fail_diff(before: bytes, after: bytes) -> dict:
        raise RuntimeError("forced undo evidence failure")

    monkeypatch.setattr(
        server_module._OPS._services.save, "_semantic_diff_bytes", fail_diff
    )

    with pytest.raises(RuntimeError, match="undo evidence"):
        undo_last_edit(str(target))

    assert target.read_bytes() == target_before
    assert backup.read_bytes() == backup_before
    assert legacy_paths[0].read_bytes() == b"legacy-0"
    assert legacy_paths[-1].read_bytes() == b"legacy-999"
    assert _recovery_files(target, "undo-recovery")
    assert _recovery_files(backup, "undo-recovery")


def test_undo_recovery_preflight_failure_does_not_mutate_swap_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "undo-recovery-preflight-failure.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    backup = target.with_suffix(target.suffix + ".bak")
    target_before = target.read_bytes()
    backup_before = backup.read_bytes()

    def fail_recovery(*args, **kwargs):
        raise RuntimeError("forced recovery storage failure")

    monkeypatch.setattr(
        server_module._OPS._services.save,
        "_publish_exact_recovery",
        fail_recovery,
    )

    with pytest.raises(RuntimeError, match="could not be preserved"):
        undo_last_edit(str(target))

    assert target.read_bytes() == target_before
    assert backup.read_bytes() == backup_before


def test_undo_post_cleanup_claim_loss_republishes_a_b_before_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "undo-post-cleanup-claim-loss.hwpx"
    external = tmp_path / "undo-post-cleanup-external.hwpx"
    external_staging = tmp_path / "undo-post-cleanup-staging.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    create_document(str(external_staging))
    add_paragraph(str(external_staging), "external cleanup owner")
    os.replace(external_staging, external)
    backup = target.with_suffix(target.suffix + ".bak")
    target_before = target.read_bytes()
    backup_before = backup.read_bytes()
    external_before = external.read_bytes()
    storage = server_module._OPS.storage
    original_remove = storage.remove_guarded_output
    replaced = False

    def replace_target_after_first_cleanup(guard) -> None:
        nonlocal replaced
        original_remove(guard)
        if not replaced and ".undo-recovery." in guard.path.name:
            os.replace(external, target)
            replaced = True

    monkeypatch.setattr(
        storage,
        "remove_guarded_output",
        replace_target_after_first_cleanup,
    )

    with pytest.raises(WorkspacePathError, match="changed"):
        undo_last_edit(str(target))

    target_recoveries = _recovery_files(target, "undo-recovery")
    backup_recoveries = _recovery_files(backup, "undo-recovery")
    assert replaced is True
    assert target.read_bytes() == external_before
    assert backup.read_bytes() == target_before
    assert any(
        path.read_bytes() == target_before
        for path in target_recoveries
    )
    assert any(
        path.read_bytes() == backup_before
        for path in backup_recoveries
    )


@pytest.mark.skipif(os.name == "nt", reason="symlink setup requires POSIX")
def test_undo_last_edit_rejects_in_workspace_backup_alias(tmp_path: Path) -> None:
    target = tmp_path / "undo-alias.hwpx"
    other = tmp_path / "other.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "authorized current")
    create_document(str(other))
    add_paragraph(str(other), "other authorized document")
    target_before = target.read_bytes()
    other_before = other.read_bytes()
    backup = target.with_suffix(target.suffix + ".bak")
    backup.unlink()
    backup.symlink_to(other)

    with pytest.raises(WorkspacePathError):
        undo_last_edit(str(target))

    assert target.read_bytes() == target_before
    assert other.read_bytes() == other_before
    assert backup.is_symlink()


def test_undo_validates_guarded_bytes_without_temp_path_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "undo-immutable-evidence.hwpx"
    create_document(str(target))
    add_paragraph(str(target), "first saved paragraph")
    add_paragraph(str(target), "second saved paragraph")
    target_before = target.read_bytes()
    backup = target.with_suffix(target.suffix + ".bak")
    stale_section = (
        "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>"
        "<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section' "
        "xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>"
        "<hp:p id='1' paraPrIDRef='0' styleIDRef='0' pageBreak='0' "
        "columnBreak='0' merged='0'>"
        "<hp:run charPrIDRef='0'><hp:t>Short</hp:t></hp:run>"
        "<hp:linesegarray><hp:lineseg textpos='40'/></hp:linesegarray>"
        "</hp:p></hs:sec>"
    ).encode("utf-8")
    _replace_zip_part(backup, "Contents/section0.xml", stale_section)
    backup_before = backup.read_bytes()
    assert build_hwpx_open_safety_report(backup)["ok"] is False

    observed_sources: list[object] = []
    original_report = save_policy_module.build_hwpx_verification_report

    def substitute_only_path_sources(source, *args, **kwargs):
        observed_sources.append(source)
        if isinstance(source, Path):
            source.write_bytes(target_before)
        return original_report(source, *args, **kwargs)

    monkeypatch.setattr(
        save_policy_module,
        "build_hwpx_verification_report",
        substitute_only_path_sources,
    )

    with pytest.raises(RuntimeError, match="backup document failed open-safety"):
        undo_last_edit(str(target))

    assert observed_sources
    assert all(isinstance(source, bytes) for source in observed_sources)
    assert target.read_bytes() == target_before
    assert backup.read_bytes() == backup_before


def test_semantic_diff_accepts_immutable_bytes_with_existing_schema(
    tmp_path: Path,
) -> None:
    target = tmp_path / "semantic-diff-bytes.hwpx"
    create_document(str(target))
    before = target.read_bytes()
    add_paragraph(str(target), "immutable bytes diff")
    after = target.read_bytes()

    report = semantic_diff(before, after)

    assert report["schemaVersion"] == "hwpx.semantic-diff.v1"
    assert report["changed"] is True
    assert report["counts"]["paragraphsAfter"] > report["counts"]["paragraphsBefore"]
