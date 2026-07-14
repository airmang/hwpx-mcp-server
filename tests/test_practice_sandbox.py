from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

import hwpx_mcp_server.practice.sandbox as sandbox_module

from hwpx_mcp_server.practice import (
    PracticeSandboxError,
    PracticeSandboxManager,
    SandboxLimits,
    validate_practice_roots,
)


RUN_A = "PRUN-0123456789ABCDEFFEDC"
RUN_B = "PRUN-1123456789ABCDEFFEDC"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    source = tmp_path / "source"
    practice = tmp_path / "practice"
    storage = practice / "runs" / "sandboxes"
    source.mkdir()
    storage.mkdir(parents=True)
    artifact = source / "private-original-name.hwpx"
    artifact.write_bytes(b"immutable-source-bytes")
    return source, practice, storage, artifact


def _manager(
    tmp_path: Path, **kwargs: object
) -> tuple[PracticeSandboxManager, Path, Path, Path, Path]:
    source, practice, storage, artifact = _roots(tmp_path)
    manager = PracticeSandboxManager(source, practice, storage, **kwargs)
    return manager, source, practice, storage, artifact


def test_copy_before_work_is_content_addressed_private_and_fresh_per_run(
    tmp_path: Path,
) -> None:
    manager, _source_root, _practice, storage, source = _manager(tmp_path)
    digest = _sha(source)
    original = source.read_bytes()

    first = manager.prepare(source, run_id=RUN_A, expected_sha256=digest)
    second = manager.prepare(source, run_id=RUN_B, expected_sha256=digest)

    assert first.working_path.read_bytes() == original
    assert second.working_path.read_bytes() == original
    assert digest in first.root.parts
    assert first.root != second.root
    assert source.name not in str(first.root)
    assert first.working_path.name == "working.hwpx"
    source_stat = source.stat()
    first_stat = first.working_path.stat()
    second_stat = second.working_path.stat()
    assert (source_stat.st_dev, source_stat.st_ino) != (first_stat.st_dev, first_stat.st_ino)
    assert (source_stat.st_dev, source_stat.st_ino) != (second_stat.st_dev, second_stat.st_ino)
    assert (first_stat.st_dev, first_stat.st_ino) != (second_stat.st_dev, second_stat.st_ino)
    assert stat.S_IMODE(first.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(first.working_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(storage.stat().st_mode) == 0o700

    first_output = first.writable_path("outputs/result.hwpx", create_parents=True)
    first_output.write_bytes(b"sandbox-only-edit")
    assert source.read_bytes() == original
    assert second.working_path.read_bytes() == original
    manager.assert_source_unchanged(first)
    receipt = first.redacted_receipt()
    assert receipt["copyVerified"] is True
    assert receipt["privateStorageCoordinatesExposed"] is False
    serialized = json.dumps(receipt)
    assert str(source) not in serialized and str(storage) not in serialized


def test_prepare_is_idempotent_for_same_run_and_rejects_owned_copy_tampering(
    tmp_path: Path,
) -> None:
    manager, _source_root, _practice, _storage, source = _manager(tmp_path)
    digest = _sha(source)
    first = manager.prepare(source, run_id=RUN_A, expected_sha256=digest)
    replay = manager.prepare(source, run_id=RUN_A, expected_sha256=digest)
    assert replay.root == first.root
    assert replay.reused is True

    first.working_path.write_bytes(b"mutated-before-resume")
    with pytest.raises(PracticeSandboxError) as captured:
        manager.prepare(source, run_id=RUN_A, expected_sha256=digest)
    assert captured.value.code == "SANDBOX_CONFLICT"


@pytest.mark.parametrize(
    ("source_parts", "practice_parts", "storage_parts"),
    [
        (("root",), ("root",), ("root", "runs")),
        (("root",), ("root", "practice"), ("root", "practice", "runs")),
        (("root", "source"), ("root",), ("root", "runs")),
        (("source",), ("practice",), ("elsewhere",)),
        (("source",), ("practice",), ("practice",)),
    ],
)
def test_roots_must_exist_be_disjoint_and_storage_must_be_strictly_nested(
    tmp_path: Path,
    source_parts: tuple[str, ...],
    practice_parts: tuple[str, ...],
    storage_parts: tuple[str, ...],
) -> None:
    source = tmp_path.joinpath(*source_parts)
    practice = tmp_path.joinpath(*practice_parts)
    storage = tmp_path.joinpath(*storage_parts)
    for path in (source, practice, storage):
        path.mkdir(parents=True, exist_ok=True)
    with pytest.raises(PracticeSandboxError) as captured:
        validate_practice_roots(source, practice, storage)
    assert captured.value.code == "ROOT_OVERLAP"
    assert str(source) not in str(captured.value)


def test_roots_reject_missing_and_symlink_alias_without_leaking_paths(tmp_path: Path) -> None:
    source = tmp_path / "private-source"
    practice = tmp_path / "private-practice"
    storage = practice / "runs"
    source.mkdir()
    storage.mkdir(parents=True)
    alias = tmp_path / "private-alias"
    alias.symlink_to(source, target_is_directory=True)

    for bad_source in (alias, tmp_path / "missing-private-source"):
        with pytest.raises(PracticeSandboxError) as captured:
            validate_practice_roots(bad_source, practice, storage)
        assert captured.value.code == "INVALID_ROOT"
        assert str(bad_source) not in str(captured.value)


def test_source_artifact_rejects_symlink_escape_directory_and_outside_file(
    tmp_path: Path,
) -> None:
    manager, source_root, _practice, _storage, source = _manager(tmp_path)
    digest = _sha(source)
    outside = tmp_path / "outside.hwpx"
    outside.write_bytes(source.read_bytes())
    linked = source_root / "linked.hwpx"
    linked.symlink_to(outside)

    for candidate in (linked, outside, source_root):
        with pytest.raises(PracticeSandboxError) as captured:
            manager.prepare(candidate, run_id=RUN_A, expected_sha256=digest)
        assert captured.value.code == "SOURCE_REFUSED"
        assert str(candidate) not in str(captured.value)


def test_hash_mismatch_and_invalid_ids_are_redacted_and_leave_no_partial_sandbox(
    tmp_path: Path,
) -> None:
    manager, _source_root, _practice, storage, source = _manager(tmp_path)
    wrong = "0" * 64
    with pytest.raises(PracticeSandboxError) as captured:
        manager.prepare(source, run_id=RUN_A, expected_sha256=wrong)
    assert captured.value.code == "SOURCE_CHANGED"
    assert source.name not in str(captured.value)
    assert not any(path.name.startswith("PSBX-") for path in storage.rglob("*"))

    with pytest.raises(PracticeSandboxError) as captured:
        manager.prepare(source, run_id="../escape", expected_sha256=_sha(source))
    assert captured.value.code == "INVALID_ID"
    assert str(storage) not in str(captured.value)


def test_writable_paths_refuse_absolute_traversal_source_and_symlinks(tmp_path: Path) -> None:
    manager, _source_root, _practice, _storage, source = _manager(tmp_path)
    sandbox = manager.prepare(source, run_id=RUN_A, expected_sha256=_sha(source))

    for relative in ("../escape.hwpx", "/absolute.hwpx", "nested\\escape.hwpx", "."):
        with pytest.raises(PracticeSandboxError) as captured:
            sandbox.writable_path(relative)
        assert captured.value.code == "SANDBOX_ESCAPE"
    with pytest.raises(PracticeSandboxError) as captured:
        sandbox.writable_path("working.hwpx")
    assert captured.value.code == "SANDBOX_ESCAPE"

    outside = tmp_path / "outside-directory"
    outside.mkdir()
    (sandbox.root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PracticeSandboxError) as captured:
        sandbox.writable_path("linked/output.hwpx")
    assert captured.value.code == "SANDBOX_SYMLINK"
    assert str(outside) not in str(captured.value)

    output = sandbox.writable_path("nested/output.hwpx", create_parents=True)
    output.write_bytes(b"sandbox-output")
    assert sandbox.root in output.parents
    assert source.read_bytes() == b"immutable-source-bytes"


def test_lease_guarantees_cleanup_on_failure_and_checks_source_integrity(
    tmp_path: Path,
) -> None:
    manager, _source_root, _practice, _storage, source = _manager(tmp_path)
    digest = _sha(source)
    root: Path | None = None
    with pytest.raises(RuntimeError, match="injected"):
        with manager.lease(source, run_id=RUN_A, expected_sha256=digest) as sandbox:
            root = sandbox.root
            output = sandbox.writable_path("outputs/work.hwpx", create_parents=True)
            output.write_bytes(b"work")
            raise RuntimeError("injected")
    assert root is not None and not root.exists()
    assert _sha(source) == digest

    with pytest.raises(PracticeSandboxError) as captured:
        with manager.lease(source, run_id=RUN_B, expected_sha256=digest):
            source.write_bytes(b"source-tampered")
    assert captured.value.code == "SOURCE_CHANGED"
    assert source.name not in str(captured.value)


def test_cleanup_unlinks_injected_symlink_without_touching_target(tmp_path: Path) -> None:
    manager, _source_root, _practice, _storage, source = _manager(tmp_path)
    sandbox = manager.prepare(source, run_id=RUN_A, expected_sha256=_sha(source))
    outside = tmp_path / "must-survive.bin"
    outside.write_bytes(b"survive")
    (sandbox.root / "untrusted-link").symlink_to(outside)

    receipt = manager.cleanup(sandbox)
    assert receipt["removed"] is True
    assert not sandbox.root.exists()
    assert outside.read_bytes() == b"survive"


def test_cleanup_requires_owned_sentinel_and_obeys_entry_byte_and_deadline_caps(
    tmp_path: Path,
) -> None:
    limits = SandboxLimits(
        max_input_bytes=64,
        max_cleanup_entries=3,
        max_cleanup_bytes=20_000,
        cleanup_deadline_seconds=1.0,
    )
    manager, _source_root, _practice, _storage, source = _manager(tmp_path, limits=limits)
    sandbox = manager.prepare(source, run_id=RUN_A, expected_sha256=_sha(source))
    extra = sandbox.writable_path("extra.bin")
    extra.write_bytes(b"x")
    too_many = sandbox.writable_path("another.bin")
    too_many.write_bytes(b"y")
    with pytest.raises(PracticeSandboxError) as captured:
        manager.cleanup(sandbox)
    assert captured.value.code == "SANDBOX_LIMIT"
    assert sandbox.root.exists()

    too_many.unlink()
    extra.unlink()
    sentinel = sandbox.root / ".hwpx-practice-sandbox"
    sentinel.unlink()
    with pytest.raises(PracticeSandboxError) as captured:
        manager.cleanup(sandbox)
    assert captured.value.code in {"SANDBOX_CONFLICT", "CLEANUP_FAILED"}
    assert sandbox.root.exists()


def test_partial_copy_and_post_copy_source_change_remove_owned_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, _source_root, _practice, storage, source = _manager(tmp_path)
    digest = _sha(source)

    def fail_after_partial(_source: Path, target: Path, *, maximum: int) -> str:
        assert maximum > 0
        target.write_bytes(b"partial-private-bytes")
        raise PracticeSandboxError("COPY_FAILED")

    monkeypatch.setattr(sandbox_module, "_copy_regular_file", fail_after_partial)
    with pytest.raises(PracticeSandboxError) as captured:
        manager.prepare(source, run_id=RUN_A, expected_sha256=digest)
    assert captured.value.code == "COPY_FAILED"
    assert not any(".tmp" in path.name for path in storage.rglob("*"))
    assert not any(path.name.startswith("PSBX-") for path in storage.rglob("*"))

    monkeypatch.undo()
    real_copy = sandbox_module._copy_regular_file

    def mutate_source_after_copy(
        source_path: Path, target: Path, *, maximum: int
    ) -> str:
        copied = real_copy(source_path, target, maximum=maximum)
        source_path.write_bytes(b"changed-after-copy")
        return copied

    monkeypatch.setattr(sandbox_module, "_copy_regular_file", mutate_source_after_copy)
    with pytest.raises(PracticeSandboxError) as captured:
        manager.prepare(source, run_id=RUN_B, expected_sha256=digest)
    assert captured.value.code == "SOURCE_CHANGED"
    assert not any(path.name.startswith("PSBX-") for path in storage.rglob("*"))


def test_cleanup_byte_and_deadline_limits_fail_closed_before_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    byte_limits = SandboxLimits(
        max_input_bytes=64,
        max_cleanup_entries=16,
        max_cleanup_bytes=17_000,
        cleanup_deadline_seconds=1.0,
    )
    manager, _source_root, _practice, _storage, source = _manager(
        tmp_path, limits=byte_limits
    )
    sandbox = manager.prepare(source, run_id=RUN_A, expected_sha256=_sha(source))
    large = sandbox.writable_path("outputs/large.bin", create_parents=True)
    large.write_bytes(b"x" * 18_000)
    with pytest.raises(PracticeSandboxError) as captured:
        manager.cleanup(sandbox)
    assert captured.value.code == "SANDBOX_LIMIT"
    assert sandbox.root.exists()

    large.unlink()
    clock = iter((0.0, 2.0, 2.0, 2.0))
    monkeypatch.setattr(sandbox_module.time, "monotonic", lambda: next(clock))
    with pytest.raises(PracticeSandboxError) as captured:
        manager.cleanup(sandbox)
    assert captured.value.code == "CLEANUP_FAILED"
    assert sandbox.root.exists()


def test_cleanup_refuses_forged_sandbox_object_and_never_deletes_storage_root(
    tmp_path: Path,
) -> None:
    manager, _source_root, _practice, storage, source = _manager(tmp_path)
    sandbox = manager.prepare(source, run_id=RUN_A, expected_sha256=_sha(source))
    forged = sandbox.__class__(
        sandbox_id=sandbox.sandbox_id,
        run_id=sandbox.run_id,
        source_content_hash=sandbox.source_content_hash,
        root=storage,
        working_path=storage / "working.hwpx",
        _manager=manager,
    )
    with pytest.raises(PracticeSandboxError) as captured:
        manager.cleanup(forged)
    assert captured.value.code == "SANDBOX_CONFLICT"
    assert storage.exists()
    assert sandbox.root.exists()
