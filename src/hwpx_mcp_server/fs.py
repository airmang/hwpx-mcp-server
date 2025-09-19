"""Filesystem helpers enforcing the configured work directory."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class WorkdirError(PermissionError):
    """Raised when a path escapes the configured work directory."""


@dataclass
class WorkdirGuard:
    """Utility enforcing that all paths live under a configured root."""

    root: Path

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()

    def ensure_ready(self) -> None:
        """Validate that the root exists and is writable."""

        if not self.root.exists():
            raise FileNotFoundError(f"Workdir '{self.root}' does not exist")
        if not self.root.is_dir():
            raise NotADirectoryError(f"Workdir '{self.root}' is not a directory")
        if not os.access(self.root, os.R_OK | os.W_OK):
            raise PermissionError(f"Workdir '{self.root}' must be readable and writable")

    def resolve_path(self, user_path: str, *, must_exist: bool = True) -> Path:
        """Return an absolute path confined to the workdir."""

        candidate = Path(user_path).expanduser()
        if not candidate.is_absolute():
            candidate = (self.root / candidate).resolve(strict=False)
        else:
            candidate = candidate.resolve(strict=False)

        if not self._contains(candidate):
            raise WorkdirError(f"Path '{user_path}' escapes configured workdir")
        if must_exist and not candidate.exists():
            raise FileNotFoundError(f"Path '{candidate}' does not exist within workdir")
        return candidate

    def resolve_output_path(self, user_path: str) -> Path:
        """Resolve an output location ensuring parent directories exist."""

        resolved = self.resolve_path(user_path, must_exist=False)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    def relative(self, path: Path) -> str:
        """Return a human readable path relative to the workdir when possible."""

        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def ensure_backup(self, path: Path) -> Optional[Path]:
        """Create a ``.bak`` copy of *path* if it exists."""

        if not path.exists():
            return None
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        return backup

    def _contains(self, candidate: Path) -> bool:
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return False
        return True
