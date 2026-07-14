# SPDX-License-Identifier: Apache-2.0
"""Private practice copy-before-work sandboxes.

The raw corpus and reviewed derivatives are inputs, never work locations.  This
module copies one hash-pinned regular file into a per-run directory and exposes
only paths below that owned directory for mutation.  Public failures are coded
and deliberately contain no filesystem coordinates.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping

from hwpx.practice.registry import validate_storage_roots


_RUN_ID = re.compile(r"PRUN-[A-F0-9]{20}\Z")
_SANDBOX_ID = re.compile(r"PSBX-[A-F0-9]{20}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SENTINEL_NAME = ".hwpx-practice-sandbox"
_WORKING_NAME = "working.hwpx"
_SENTINEL_SCHEMA = "hwpx.practice-sandbox-owner/v1"

_PUBLIC_MESSAGES = {
    "INVALID_ROOT": "practice storage roots are invalid or unavailable",
    "ROOT_OVERLAP": "practice storage roots violate isolation policy",
    "INVALID_ID": "practice sandbox identifier is invalid",
    "INVALID_HASH": "practice source content address is invalid",
    "SOURCE_REFUSED": "practice source is unavailable or outside an allowed root",
    "SOURCE_CHANGED": "practice source integrity changed during the run",
    "COPY_FAILED": "practice source copy could not be verified",
    "SANDBOX_CONFLICT": "practice sandbox ownership or content conflicts",
    "SANDBOX_ESCAPE": "practice write path escaped its owned sandbox",
    "SANDBOX_SYMLINK": "practice sandbox symlink policy was violated",
    "SANDBOX_LIMIT": "practice sandbox resource limit was exceeded",
    "CLEANUP_FAILED": "practice sandbox cleanup could not be completed safely",
}


class PracticeSandboxError(RuntimeError):
    """Path-redacted, fail-closed sandbox error safe for an MCP response."""

    def __init__(self, code: str) -> None:
        if code not in _PUBLIC_MESSAGES:
            code = "COPY_FAILED"
        self.code = code
        super().__init__(f"{code}: {_PUBLIC_MESSAGES[code]}")


@dataclass(frozen=True)
class SandboxLimits:
    """Hard caps that keep copy and cleanup work finite."""

    max_input_bytes: int = 256 * 1024 * 1024
    max_cleanup_entries: int = 2_048
    max_cleanup_bytes: int = 1024 * 1024 * 1024
    cleanup_deadline_seconds: float = 10.0

    def __post_init__(self) -> None:
        if (
            self.max_input_bytes < 1
            or self.max_cleanup_entries < 2
            # Reserve enough room for the owned sentinel in addition to the
            # largest permitted input, so a newly prepared sandbox is always
            # cleanup-eligible before a workflow adds outputs.
            or self.max_cleanup_bytes < self.max_input_bytes + 16 * 1024
            or not 0 < self.cleanup_deadline_seconds <= 60
        ):
            raise ValueError("sandbox limits must be positive and bounded")


def _raw_absolute(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if ".." in path.parts:
        raise PracticeSandboxError("INVALID_ROOT")
    return path


def _strict_existing_directory(value: str | Path) -> Path:
    raw = _raw_absolute(value)
    try:
        metadata = raw.lstat()
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise PracticeSandboxError("INVALID_ROOT") from exc
    # Comparing the spelling with the resolved path rejects a symlink in any
    # configured root component, not only a symlink at the final component.
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or raw != resolved:
        raise PracticeSandboxError("INVALID_ROOT")
    if resolved == Path(resolved.anchor):
        raise PracticeSandboxError("INVALID_ROOT")
    return resolved


def validate_practice_roots(
    source_root: str | Path,
    practice_root: str | Path,
    storage_root: str | Path,
) -> tuple[Path, Path, Path]:
    """Validate three strict, existing roots before any sandbox mutation.

    ``storage_root`` must be a strict descendant of ``practice_root``.  The
    corpus source must be disjoint from both.  The S-072 two-root contract is
    reused first, then narrowed for Leap B's run storage.
    """

    source = _strict_existing_directory(source_root)
    practice = _strict_existing_directory(practice_root)
    storage = _strict_existing_directory(storage_root)
    try:
        validate_storage_roots(source, practice)
        validate_storage_roots(source, storage)
    except ValueError as exc:
        raise PracticeSandboxError("ROOT_OVERLAP") from exc
    if storage == practice or practice not in storage.parents:
        raise PracticeSandboxError("ROOT_OVERLAP")
    return source, practice, storage


def _normalize_hash(value: str) -> str:
    digest = value.removeprefix("sha256:")
    if not _SHA256.fullmatch(digest):
        raise PracticeSandboxError("INVALID_HASH")
    return digest


def _sandbox_id(run_id: str, content_hash: str) -> str:
    token = hashlib.sha256(f"{run_id}\n{content_hash}".encode("ascii")).hexdigest()[:20]
    return f"PSBX-{token.upper()}"


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _regular_lstat(path: Path, code: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PracticeSandboxError(code) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise PracticeSandboxError(code)
    return metadata


def _hash_regular_file(path: Path, *, maximum: int, code: str) -> tuple[str, os.stat_result]:
    before = _regular_lstat(path, code)
    if before.st_size > maximum:
        raise PracticeSandboxError("SANDBOX_LIMIT")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ):
                raise PracticeSandboxError(code)
            size = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                if size > maximum:
                    raise PracticeSandboxError("SANDBOX_LIMIT")
                digest.update(chunk)
            after = os.fstat(stream.fileno())
    except PracticeSandboxError:
        raise
    except OSError as exc:
        raise PracticeSandboxError(code) from exc
    if (
        size != before.st_size
        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    ):
        raise PracticeSandboxError(code)
    return digest.hexdigest(), after


def _safe_mkdir(parent: Path, name: str) -> Path:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise PracticeSandboxError("SANDBOX_ESCAPE")
    try:
        parent_stat = parent.lstat()
        if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
            raise PracticeSandboxError("SANDBOX_SYMLINK")
    except PracticeSandboxError:
        raise
    except OSError as exc:
        raise PracticeSandboxError("SANDBOX_SYMLINK") from exc
    child = parent / name
    try:
        os.mkdir(child, mode=0o700)
    except FileExistsError:
        try:
            metadata = child.lstat()
        except OSError as exc:
            raise PracticeSandboxError("SANDBOX_SYMLINK") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise PracticeSandboxError("SANDBOX_SYMLINK")
    except OSError as exc:
        raise PracticeSandboxError("COPY_FAILED") from exc
    try:
        os.chmod(child, 0o700, follow_symlinks=False)
    except OSError as exc:
        raise PracticeSandboxError("COPY_FAILED") from exc
    return child


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _regular_lstat(temporary, "COPY_FAILED")
        os.chmod(temporary, 0o600, follow_symlinks=False)
        os.replace(temporary, path)
        _regular_lstat(path, "COPY_FAILED")
    except PracticeSandboxError:
        raise
    except OSError as exc:
        raise PracticeSandboxError("COPY_FAILED") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if temporary.is_symlink() or temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def _read_regular_json(path: Path) -> object:
    before = _regular_lstat(path, "SANDBOX_CONFLICT")
    if before.st_size > 16 * 1024:
        raise PracticeSandboxError("SANDBOX_CONFLICT")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            ):
                raise PracticeSandboxError("SANDBOX_CONFLICT")
            payload = stream.read(16 * 1024 + 1)
            after = os.fstat(stream.fileno())
        if (
            len(payload) > 16 * 1024
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        ):
            raise PracticeSandboxError("SANDBOX_CONFLICT")
        return json.loads(payload.decode("utf-8"))
    except PracticeSandboxError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PracticeSandboxError("SANDBOX_CONFLICT") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _copy_regular_file(source: Path, target: Path, *, maximum: int) -> str:
    source_before = _regular_lstat(source, "SOURCE_REFUSED")
    if source_before.st_size > maximum:
        raise PracticeSandboxError("SANDBOX_LIMIT")
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    target_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    source_descriptor: int | None = None
    target_descriptor: int | None = None
    try:
        source_descriptor = os.open(source, source_flags)
        target_descriptor = os.open(target, target_flags, 0o600)
        with os.fdopen(source_descriptor, "rb") as source_stream, os.fdopen(
            target_descriptor, "wb"
        ) as target_stream:
            source_descriptor = target_descriptor = None
            opened_source = os.fstat(source_stream.fileno())
            opened_target = os.fstat(target_stream.fileno())
            if not stat.S_ISREG(opened_source.st_mode) or not stat.S_ISREG(opened_target.st_mode):
                raise PracticeSandboxError("COPY_FAILED")
            if (opened_source.st_dev, opened_source.st_ino) != (
                source_before.st_dev,
                source_before.st_ino,
            ):
                raise PracticeSandboxError("SOURCE_CHANGED")
            if (opened_source.st_dev, opened_source.st_ino) == (
                opened_target.st_dev,
                opened_target.st_ino,
            ):
                raise PracticeSandboxError("COPY_FAILED")
            size = 0
            for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                size += len(chunk)
                if size > maximum:
                    raise PracticeSandboxError("SANDBOX_LIMIT")
                digest.update(chunk)
                target_stream.write(chunk)
            target_stream.flush()
            os.fsync(target_stream.fileno())
            source_after = os.fstat(source_stream.fileno())
        if (
            size != source_before.st_size
            or (
                source_after.st_dev,
                source_after.st_ino,
                source_after.st_size,
                source_after.st_mtime_ns,
            )
            != (
                source_before.st_dev,
                source_before.st_ino,
                source_before.st_size,
                source_before.st_mtime_ns,
            )
        ):
            raise PracticeSandboxError("SOURCE_CHANGED")
        os.chmod(target, 0o600, follow_symlinks=False)
        copied = _regular_lstat(target, "COPY_FAILED")
        if (copied.st_dev, copied.st_ino) == (source_before.st_dev, source_before.st_ino):
            raise PracticeSandboxError("COPY_FAILED")
        return digest.hexdigest()
    except PracticeSandboxError:
        raise
    except OSError as exc:
        raise PracticeSandboxError("COPY_FAILED") from exc
    finally:
        if source_descriptor is not None:
            os.close(source_descriptor)
        if target_descriptor is not None:
            os.close(target_descriptor)


@dataclass(frozen=True)
class PracticeSandbox:
    """An internal owned sandbox; use ``redacted_receipt`` at trust boundaries.

    ``working_path`` is the immutable, hash-pinned input copy.  Mutating tools
    must use a distinct path obtained from :meth:`writable_path`.
    """

    sandbox_id: str
    run_id: str
    source_content_hash: str
    root: Path = field(repr=False)
    working_path: Path = field(repr=False)
    reused: bool = False
    _source_path: Path | None = field(default=None, repr=False, compare=False)
    _manager: "PracticeSandboxManager | None" = field(default=None, repr=False, compare=False)

    def writable_path(self, relative_path: str, *, create_parents: bool = False) -> Path:
        if self._manager is None:
            raise PracticeSandboxError("SANDBOX_CONFLICT")
        return self._manager.writable_path(
            self, relative_path, create_parents=create_parents
        )

    def redacted_receipt(self) -> dict[str, Any]:
        return {
            "schema": "hwpx.practice-sandbox-receipt/v1",
            "sandboxId": self.sandbox_id,
            "runId": self.run_id,
            "sourceContentHash": self.source_content_hash,
            "copyVerified": True,
            "freshCopy": not self.reused,
            "privateStorageCoordinatesExposed": False,
        }


class PracticeSandboxManager:
    """Allocate, validate, and boundedly remove content-addressed run sandboxes."""

    def __init__(
        self,
        source_root: str | Path,
        practice_root: str | Path,
        storage_root: str | Path,
        *,
        limits: SandboxLimits | None = None,
    ) -> None:
        self.source_root, self.practice_root, self.storage_root = validate_practice_roots(
            source_root, practice_root, storage_root
        )
        self.limits = limits or SandboxLimits()
        try:
            os.chmod(self.storage_root, 0o700, follow_symlinks=False)
        except OSError as exc:
            raise PracticeSandboxError("INVALID_ROOT") from exc

    def _ensure_storage_owned(self) -> None:
        try:
            metadata = self.storage_root.lstat()
            resolved = self.storage_root.resolve(strict=True)
        except OSError as exc:
            raise PracticeSandboxError("INVALID_ROOT") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or resolved != self.storage_root
        ):
            raise PracticeSandboxError("INVALID_ROOT")

    def _source_path(self, value: str | Path) -> Path:
        raw = _raw_absolute(value)
        try:
            metadata = raw.lstat()
            source = raw.resolve(strict=True)
        except (OSError, PracticeSandboxError) as exc:
            raise PracticeSandboxError("SOURCE_REFUSED") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or raw != source:
            raise PracticeSandboxError("SOURCE_REFUSED")
        allowed = self.source_root in source.parents or self.practice_root in source.parents
        forbidden = source == self.storage_root or self.storage_root in source.parents
        if not allowed or forbidden:
            raise PracticeSandboxError("SOURCE_REFUSED")
        return source

    def _sandbox_path(self, run_id: str, digest: str) -> tuple[str, Path]:
        if not _RUN_ID.fullmatch(run_id):
            raise PracticeSandboxError("INVALID_ID")
        sandbox_id = _sandbox_id(run_id, digest)
        return sandbox_id, self.storage_root / digest[:2] / digest / sandbox_id

    def _sentinel(self, sandbox: PracticeSandbox) -> dict[str, Any]:
        return {
            "schema": _SENTINEL_SCHEMA,
            "sandboxId": sandbox.sandbox_id,
            "runId": sandbox.run_id,
            "sourceContentHash": sandbox.source_content_hash,
        }

    def _verify_owned(self, sandbox: PracticeSandbox, *, require_working: bool = True) -> None:
        self._ensure_storage_owned()
        if (
            not _SANDBOX_ID.fullmatch(sandbox.sandbox_id)
            or not _RUN_ID.fullmatch(sandbox.run_id)
            or not _SHA256.fullmatch(sandbox.source_content_hash)
        ):
            raise PracticeSandboxError("SANDBOX_CONFLICT")
        expected_id, expected_root = self._sandbox_path(
            sandbox.run_id, sandbox.source_content_hash
        )
        if sandbox.sandbox_id != expected_id or sandbox.root != expected_root:
            raise PracticeSandboxError("SANDBOX_CONFLICT")
        if (
            sandbox.root in {self.source_root, self.practice_root, self.storage_root}
            or self.source_root in sandbox.root.parents
            or sandbox.root == Path(sandbox.root.anchor)
        ):
            raise PracticeSandboxError("SANDBOX_CONFLICT")
        try:
            root_stat = sandbox.root.lstat()
            if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
                raise PracticeSandboxError("SANDBOX_SYMLINK")
            sentinel = _read_regular_json(sandbox.root / _SENTINEL_NAME)
        except PracticeSandboxError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PracticeSandboxError("SANDBOX_CONFLICT") from exc
        if sentinel != self._sentinel(sandbox):
            raise PracticeSandboxError("SANDBOX_CONFLICT")
        if require_working:
            working = sandbox.root / _WORKING_NAME
            if sandbox.working_path != working:
                raise PracticeSandboxError("SANDBOX_CONFLICT")
            copied_hash, _ = _hash_regular_file(
                working,
                maximum=self.limits.max_input_bytes,
                code="SANDBOX_CONFLICT",
            )
            if copied_hash != sandbox.source_content_hash:
                raise PracticeSandboxError("SANDBOX_CONFLICT")

    def _load_existing(
        self, root: Path, *, sandbox_id: str, run_id: str, digest: str, source: Path
    ) -> PracticeSandbox:
        sandbox = PracticeSandbox(
            sandbox_id=sandbox_id,
            run_id=run_id,
            source_content_hash=digest,
            root=root,
            working_path=root / _WORKING_NAME,
            reused=True,
            _source_path=source,
            _manager=self,
        )
        self._verify_owned(sandbox)
        source_hash, source_stat = _hash_regular_file(
            source, maximum=self.limits.max_input_bytes, code="SOURCE_CHANGED"
        )
        working_stat = _regular_lstat(sandbox.working_path, "SANDBOX_CONFLICT")
        if source_hash != digest or (source_stat.st_dev, source_stat.st_ino) == (
            working_stat.st_dev,
            working_stat.st_ino,
        ):
            raise PracticeSandboxError("SOURCE_CHANGED")
        return sandbox

    def prepare(
        self,
        source_artifact: str | Path,
        *,
        run_id: str,
        expected_sha256: str,
    ) -> PracticeSandbox:
        """Atomically create or validate one deterministic per-run sandbox."""

        self._ensure_storage_owned()
        digest = _normalize_hash(expected_sha256)
        source = self._source_path(source_artifact)
        source_before, _ = _hash_regular_file(
            source, maximum=self.limits.max_input_bytes, code="SOURCE_CHANGED"
        )
        if source_before != digest:
            raise PracticeSandboxError("SOURCE_CHANGED")
        sandbox_id, target = self._sandbox_path(run_id, digest)
        if target.exists() or target.is_symlink():
            return self._load_existing(
                target,
                sandbox_id=sandbox_id,
                run_id=run_id,
                digest=digest,
                source=source,
            )

        prefix = _safe_mkdir(self.storage_root, digest[:2])
        content_root = _safe_mkdir(prefix, digest)
        staging = content_root / f".{sandbox_id}.{uuid.uuid4().hex}.tmp"
        try:
            os.mkdir(staging, mode=0o700)
            os.chmod(staging, 0o700, follow_symlinks=False)
            working = staging / _WORKING_NAME
            sandbox = PracticeSandbox(
                sandbox_id=sandbox_id,
                run_id=run_id,
                source_content_hash=digest,
                root=staging,
                working_path=working,
                reused=False,
                _source_path=source,
                _manager=self,
            )
            _atomic_write(
                staging / _SENTINEL_NAME, _canonical_json(self._sentinel(sandbox))
            )
            copied_hash = _copy_regular_file(
                source, working, maximum=self.limits.max_input_bytes
            )
            if copied_hash != digest:
                raise PracticeSandboxError("COPY_FAILED")
            try:
                os.rename(staging, target)
            except OSError:
                if target.exists() and not target.is_symlink():
                    self._bounded_remove_tree(staging)
                    return self._load_existing(
                        target,
                        sandbox_id=sandbox_id,
                        run_id=run_id,
                        digest=digest,
                        source=source,
                    )
                raise
            sandbox = PracticeSandbox(
                sandbox_id=sandbox_id,
                run_id=run_id,
                source_content_hash=digest,
                root=target,
                working_path=target / _WORKING_NAME,
                reused=False,
                _source_path=source,
                _manager=self,
            )
            source_after, source_stat = _hash_regular_file(
                source, maximum=self.limits.max_input_bytes, code="SOURCE_CHANGED"
            )
            working_stat = _regular_lstat(sandbox.working_path, "COPY_FAILED")
            if source_after != digest or (source_stat.st_dev, source_stat.st_ino) == (
                working_stat.st_dev,
                working_stat.st_ino,
            ):
                raise PracticeSandboxError("SOURCE_CHANGED")
            self._verify_owned(sandbox)
            return sandbox
        except PracticeSandboxError:
            if staging.exists() and not staging.is_symlink():
                if (staging / _SENTINEL_NAME).is_file():
                    self._bounded_remove_tree(staging)
                else:
                    try:
                        staging.rmdir()
                    except OSError as exc:
                        raise PracticeSandboxError("CLEANUP_FAILED") from exc
            if target.exists() and not target.is_symlink():
                candidate = PracticeSandbox(
                    sandbox_id=sandbox_id,
                    run_id=run_id,
                    source_content_hash=digest,
                    root=target,
                    working_path=target / _WORKING_NAME,
                    _manager=self,
                )
                try:
                    self._verify_owned(candidate, require_working=False)
                    self._bounded_remove_tree(target)
                except PracticeSandboxError:
                    pass
            raise
        except OSError as exc:
            if staging.exists() and not staging.is_symlink():
                if (staging / _SENTINEL_NAME).is_file():
                    self._bounded_remove_tree(staging)
                else:
                    try:
                        staging.rmdir()
                    except OSError as cleanup_exc:
                        raise PracticeSandboxError("CLEANUP_FAILED") from cleanup_exc
            raise PracticeSandboxError("COPY_FAILED") from exc

    def assert_source_unchanged(self, sandbox: PracticeSandbox) -> None:
        if sandbox._source_path is None:
            raise PracticeSandboxError("SOURCE_CHANGED")
        source_hash, _ = _hash_regular_file(
            sandbox._source_path,
            maximum=self.limits.max_input_bytes,
            code="SOURCE_CHANGED",
        )
        if source_hash != sandbox.source_content_hash:
            raise PracticeSandboxError("SOURCE_CHANGED")

    def writable_path(
        self,
        sandbox: PracticeSandbox,
        relative_path: str,
        *,
        create_parents: bool = False,
    ) -> Path:
        """Return a mutation path only after strict containment and symlink checks."""

        self._verify_owned(sandbox)
        if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
            raise PracticeSandboxError("SANDBOX_ESCAPE")
        pure = PurePosixPath(relative_path)
        if (
            pure.is_absolute()
            or not pure.parts
            or not pure.name
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise PracticeSandboxError("SANDBOX_ESCAPE")
        if pure.name in {_SENTINEL_NAME, _WORKING_NAME}:
            raise PracticeSandboxError("SANDBOX_ESCAPE")
        current = sandbox.root
        parents = pure.parts[:-1]
        for part in parents:
            candidate = current / part
            if candidate.exists() or candidate.is_symlink():
                try:
                    metadata = candidate.lstat()
                except OSError as exc:
                    raise PracticeSandboxError("SANDBOX_SYMLINK") from exc
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    raise PracticeSandboxError("SANDBOX_SYMLINK")
            elif create_parents:
                candidate = _safe_mkdir(current, part)
            else:
                raise PracticeSandboxError("SANDBOX_ESCAPE")
            current = candidate
        target = current / pure.name
        if target.exists() or target.is_symlink():
            try:
                metadata = target.lstat()
            except OSError as exc:
                raise PracticeSandboxError("SANDBOX_SYMLINK") from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise PracticeSandboxError("SANDBOX_SYMLINK")
        try:
            resolved_parent = target.parent.resolve(strict=True)
        except OSError as exc:
            raise PracticeSandboxError("SANDBOX_ESCAPE") from exc
        if sandbox.root != resolved_parent and sandbox.root not in resolved_parent.parents:
            raise PracticeSandboxError("SANDBOX_ESCAPE")
        return target

    def _scan_for_cleanup(self, root: Path, *, started: float) -> tuple[int, int]:
        entries = 0
        size = 0
        stack = [root]
        while stack:
            if time.monotonic() - started > self.limits.cleanup_deadline_seconds:
                raise PracticeSandboxError("CLEANUP_FAILED")
            directory = stack.pop()
            try:
                directory_stat = directory.lstat()
                if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
                    raise PracticeSandboxError("SANDBOX_SYMLINK")
                with os.scandir(directory) as iterator:
                    children = list(iterator)
            except PracticeSandboxError:
                raise
            except OSError as exc:
                raise PracticeSandboxError("CLEANUP_FAILED") from exc
            for child in children:
                entries += 1
                if entries > self.limits.max_cleanup_entries:
                    raise PracticeSandboxError("SANDBOX_LIMIT")
                try:
                    metadata = child.stat(follow_symlinks=False)
                except OSError as exc:
                    raise PracticeSandboxError("CLEANUP_FAILED") from exc
                if stat.S_ISDIR(metadata.st_mode):
                    stack.append(Path(child.path))
                elif stat.S_ISREG(metadata.st_mode):
                    size += metadata.st_size
                    if size > self.limits.max_cleanup_bytes:
                        raise PracticeSandboxError("SANDBOX_LIMIT")
                elif stat.S_ISLNK(metadata.st_mode):
                    # It is safe to unlink the directory entry itself.  Never
                    # traverse or stat its target.
                    continue
                else:
                    raise PracticeSandboxError("CLEANUP_FAILED")
        return entries, size

    def _bounded_remove_tree(self, root: Path) -> None:
        started = time.monotonic()
        if root in {self.source_root, self.practice_root, self.storage_root}:
            raise PracticeSandboxError("CLEANUP_FAILED")
        if self.storage_root not in root.parents or root == Path(root.anchor):
            raise PracticeSandboxError("CLEANUP_FAILED")
        try:
            root_stat = root.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise PracticeSandboxError("CLEANUP_FAILED") from exc
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            raise PracticeSandboxError("CLEANUP_FAILED")
        _regular_lstat(root / _SENTINEL_NAME, "CLEANUP_FAILED")
        self._scan_for_cleanup(root, started=started)

        def remove(directory: Path) -> None:
            if time.monotonic() - started > self.limits.cleanup_deadline_seconds:
                raise PracticeSandboxError("CLEANUP_FAILED")
            try:
                directory_stat = directory.lstat()
                if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(
                    directory_stat.st_mode
                ):
                    raise PracticeSandboxError("CLEANUP_FAILED")
                with os.scandir(directory) as iterator:
                    children = list(iterator)
            except PracticeSandboxError:
                raise
            except OSError as exc:
                raise PracticeSandboxError("CLEANUP_FAILED") from exc
            for child in children:
                try:
                    metadata = child.stat(follow_symlinks=False)
                except OSError as exc:
                    raise PracticeSandboxError("CLEANUP_FAILED") from exc
                child_path = Path(child.path)
                try:
                    if stat.S_ISDIR(metadata.st_mode):
                        remove(child_path)
                        child_path.rmdir()
                    elif stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                        child_path.unlink()
                    else:
                        raise PracticeSandboxError("CLEANUP_FAILED")
                except PracticeSandboxError:
                    raise
                except OSError as exc:
                    raise PracticeSandboxError("CLEANUP_FAILED") from exc

        remove(root)
        try:
            root.rmdir()
        except OSError as exc:
            raise PracticeSandboxError("CLEANUP_FAILED") from exc

    def cleanup(self, sandbox: PracticeSandbox) -> dict[str, Any]:
        """Delete exactly one strictly identified, sentinel-owned sandbox."""

        self._verify_owned(sandbox, require_working=False)
        self._bounded_remove_tree(sandbox.root)
        for parent in (sandbox.root.parent, sandbox.root.parent.parent):
            try:
                parent_stat = parent.lstat()
                if stat.S_ISDIR(parent_stat.st_mode) and not stat.S_ISLNK(parent_stat.st_mode):
                    parent.rmdir()
            except OSError:
                break
        return {
            "schema": "hwpx.practice-sandbox-cleanup/v1",
            "sandboxId": sandbox.sandbox_id,
            "removed": True,
            "privateStorageCoordinatesExposed": False,
        }

    @contextmanager
    def lease(
        self,
        source_artifact: str | Path,
        *,
        run_id: str,
        expected_sha256: str,
    ) -> Iterator[PracticeSandbox]:
        """Prepare a sandbox and always run integrity check plus bounded cleanup."""

        sandbox = self.prepare(
            source_artifact, run_id=run_id, expected_sha256=expected_sha256
        )
        try:
            yield sandbox
        finally:
            try:
                self.assert_source_unchanged(sandbox)
            finally:
                self.cleanup(sandbox)


__all__ = [
    "PracticeSandbox",
    "PracticeSandboxError",
    "PracticeSandboxManager",
    "SandboxLimits",
    "validate_practice_roots",
]
