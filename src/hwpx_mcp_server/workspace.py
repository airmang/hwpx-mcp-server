# SPDX-License-Identifier: Apache-2.0
"""Canonical fail-closed workspace-root resolution for local MCP paths."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


WORKSPACE_ROOTS_ENV = "HWPX_MCP_WORKSPACE_ROOTS"
LEGACY_SANDBOX_ROOT_ENV = "HWPX_MCP_SANDBOX_ROOT"


class WorkspaceConfigurationError(RuntimeError):
    """The host did not provide a usable, bounded workspace root."""

    code = "WORKSPACE_ROOT_INVALID"


class WorkspacePathError(PermissionError):
    """A requested path escaped or violated the configured workspace policy."""

    def __init__(self, message: str, *, code: str, reason: str) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason

    def safe_details(self) -> dict[str, str]:
        return {"reason": self.reason}


def _split_roots(raw: str) -> list[str]:
    value = raw.strip()
    if not value:
        raise WorkspaceConfigurationError(
            f"{WORKSPACE_ROOTS_ENV} is set but contains no workspace roots"
        )
    if value.startswith("["):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise WorkspaceConfigurationError(
                f"{WORKSPACE_ROOTS_ENV} must be a JSON string array or {os.pathsep!r}-separated paths"
            ) from exc
        if not isinstance(decoded, list) or not all(
            isinstance(item, str) and item.strip() for item in decoded
        ):
            raise WorkspaceConfigurationError(
                f"{WORKSPACE_ROOTS_ENV} JSON value must be a non-empty string array"
            )
        return [item.strip() for item in decoded]
    return [item.strip() for item in value.split(os.pathsep) if item.strip()]


def _normalize_roots(values: Iterable[str | os.PathLike[str]]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for value in values:
        raw = Path(value).expanduser()
        try:
            root = raw.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise WorkspaceConfigurationError(
                "an authorized workspace root does not exist or cannot be resolved"
            ) from exc
        if not root.is_dir():
            raise WorkspaceConfigurationError("an authorized workspace root is not a directory")
        if root == Path(root.anchor):
            raise WorkspaceConfigurationError(
                "a filesystem root cannot be authorized as an HWPX workspace"
            )
        if root not in roots:
            roots.append(root)
    if not roots:
        raise WorkspaceConfigurationError("at least one authorized workspace root is required")
    return tuple(roots)


@dataclass(frozen=True, slots=True)
class WorkspaceResolver:
    """Resolve relative and absolute paths inside one or more authorized roots."""

    roots: tuple[Path, ...]
    source: str

    @classmethod
    def from_environment(cls, *, cwd: Path | None = None) -> "WorkspaceResolver":
        explicit = os.environ.get(WORKSPACE_ROOTS_ENV)
        if explicit is not None:
            return cls(_normalize_roots(_split_roots(explicit)), WORKSPACE_ROOTS_ENV)

        legacy = os.environ.get(LEGACY_SANDBOX_ROOT_ENV)
        if legacy is not None:
            if not legacy.strip():
                raise WorkspaceConfigurationError(
                    f"{LEGACY_SANDBOX_ROOT_ENV} is set but empty"
                )
            return cls(_normalize_roots([legacy]), LEGACY_SANDBOX_ROOT_ENV)

        process_workspace = cwd or Path.cwd()
        return cls(_normalize_roots([process_workspace]), "process-cwd")

    @classmethod
    def from_roots(
        cls,
        roots: Iterable[str | os.PathLike[str]],
        *,
        source: str = "explicit",
    ) -> "WorkspaceResolver":
        return cls(_normalize_roots(roots), source)

    @property
    def primary_root(self) -> Path:
        return self.roots[0]

    def _candidate(self, value: str | os.PathLike[str]) -> Path:
        text = os.fspath(value)
        if not text or not text.strip() or "\0" in text:
            raise WorkspacePathError(
                "workspace path must be a non-empty filesystem path",
                code="WORKSPACE_PATH_INVALID",
                reason="empty_or_invalid_path",
            )
        candidate = Path(text).expanduser()
        return candidate if candidate.is_absolute() else self.primary_root / candidate

    def _authorized_root(self, resolved: Path) -> Path | None:
        for root in self.roots:
            if resolved == root or root in resolved.parents:
                return root
        return None

    def resolve(
        self,
        value: str | os.PathLike[str],
        *,
        must_exist: bool = True,
    ) -> Path:
        candidate = self._candidate(value)
        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise WorkspacePathError(
                "workspace path could not be resolved safely",
                code="WORKSPACE_PATH_INVALID",
                reason="resolution_failed",
            ) from exc

        if self._authorized_root(resolved) is None:
            raise WorkspacePathError(
                "path is outside the authorized HWPX workspace roots",
                code="WORKSPACE_OUTSIDE_ROOT",
                reason="outside_authorized_roots",
            )
        return resolved

    def resolve_output(
        self,
        value: str | os.PathLike[str],
        *,
        create_parents: bool = True,
    ) -> Path:
        resolved = self.resolve(value, must_exist=False)
        if resolved.exists() and resolved.is_dir():
            raise WorkspacePathError(
                "output path identifies a directory, not a document",
                code="WORKSPACE_PATH_INVALID",
                reason="output_is_directory",
            )
        if create_parents:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            parent = resolved.parent.resolve(strict=True)
            if self._authorized_root(parent) is None:
                raise WorkspacePathError(
                    "output parent escaped the authorized HWPX workspace roots",
                    code="WORKSPACE_SYMLINK_ESCAPE",
                    reason="output_parent_escape",
                )
            resolved = parent / resolved.name
        return resolved

    def display_path(self, path: Path) -> str:
        resolved = path.resolve(strict=False)
        if resolved == self.primary_root or self.primary_root in resolved.parents:
            return str(resolved.relative_to(self.primary_root))
        return str(resolved)

    def describe(self) -> dict[str, object]:
        return {
            "source": self.source,
            "roots": [str(root) for root in self.roots],
            "rootCount": len(self.roots),
            "relativePathRoot": str(self.primary_root),
            "failClosed": True,
        }
